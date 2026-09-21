from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from playwright.sync_api import BrowserContext, Page

from .downloader import Downloader, ManifestStore
from .inspector import extract_links
from .utils import (
    host_allowed,
    normalize_url,
    sanitize_component,
    unique_path,
    url_extension,
)

RESOURCE_PATTERNS = (
    "/pluginfile.php/",
    "/draftfile.php/",
    "/forcedownload/",
    "/mod/resource/",
    "download.php",
    "file.php",
    "attachment.php",
)
CRAWL_PATTERNS = (
    "/mod/folder/",
    "/mod/page/",
    "/mod/book/",
    "/mod/url/",
    "/mod/assign/",
    "/mod/lesson/",
    "/mod/quiz/",
    "/course/section",
    "/course/view.php",
    "/course/overview.php",
    "/local/joulegrader/view.php",
    "/portal/site/",
    "/content/",
    "/group/",
)

SNAPSHOT_PATHS = {
    "/mod/assign/view.php",
    "/mod/page/view.php",
    "/mod/quiz/view.php",
    "/mod/quiz/review.php",
    "/mod/lesson/view.php",
    "/course/overview.php",
    "/local/joulegrader/view.php",
}

FETCH_COURSE_CONTENTS_JS = r"""
async (courseId) => {
  const cfg = window.M && window.M.cfg ? window.M.cfg : null;
  if (!cfg || !cfg.sesskey || !cfg.wwwroot || !courseId) {
    return {available: false, links: [], error: 'Moodle no expuso una sesión válida'};
  }
  const method = 'core_course_get_contents';
  const endpoint = `${cfg.wwwroot}/lib/ajax/service.php?sesskey=${encodeURIComponent(cfg.sesskey)}&info=${method}`;
  const request = [{
    index: 0,
    methodname: method,
    args: {courseid: Number(courseId), options: []}
  }];
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(request)
    });
    if (!response.ok) {
      return {available: true, links: [], error: `HTTP ${response.status}`};
    }
    const payload = await response.json();
    const first = Array.isArray(payload) ? payload[0] : null;
    if (!first || first.error || !Array.isArray(first.data)) {
      const message = first && first.exception && first.exception.message
        ? first.exception.message
        : 'respuesta no compatible';
      return {available: true, links: [], error: message};
    }
    const links = [];
    for (const section of first.data) {
      const sectionName = String(section.name || `Sección ${section.section ?? ''}`).trim() || 'Otros';
      for (const module of (section.modules || [])) {
        if (module.url) {
          links.push({
            href: String(module.url),
            text: String(module.name || module.modname || 'Actividad'),
            section: sectionName,
            download: false,
            from_course_index: true
          });
        }
        for (const content of (module.contents || [])) {
          if (!content.fileurl) continue;
          links.push({
            href: String(content.fileurl),
            text: String(content.filename || module.name || 'Archivo'),
            section: sectionName,
            download: true,
            from_course_index: true
          });
        }
      }
    }
    return {available: true, links, error: ''};
  } catch (error) {
    return {available: true, links: [], error: String(error)};
  }
}
"""


def _excluded(url: str, config: dict) -> bool:
    lowered = url.lower()
    return any(pattern.lower() in lowered for pattern in config["exclude_url_patterns"])


def _has_language_override(url: str) -> bool:
    return any(
        key.lower() in {"lang", "language"}
        for key, _value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    )


def _with_preferred_language(url: str, language: str) -> str:
    if not language:
        return url
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"lang", "language"}
    ]
    query.append(("lang", language))
    return parsed._replace(query=urlencode(query)).geturl()


def is_download_candidate(link: dict, config: dict) -> bool:
    url = link["href"]
    extension = url_extension(url)
    if extension in config["include_extensions"]:
        return config.get("include_images", True) or extension not in {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
        }
    lowered = url.lower()
    text = (link.get("text") or "").lower()
    return bool(
        link.get("download")
        or any(pattern in lowered for pattern in RESOURCE_PATTERNS)
        or any(word in text for word in ("descargar", "download", "archivo adjunto"))
    )


def is_crawl_candidate(url: str, config: dict, initial_course_url: str) -> bool:
    if _excluded(url, config) or not host_allowed(url, config["allowed_domains"]):
        return False
    # El selector de idioma de Moodle aparece en todas las páginas. Seguirlo
    # genera una copia de cada actividad por idioma y puede agotar el límite de
    # rastreo antes de alcanzar los materiales reales.
    if _has_language_override(url):
        return False
    lowered = url.lower()
    if url_extension(url) in config["include_extensions"]:
        return False
    if "/course/view.php" in lowered or "/course/overview.php" in lowered:
        target_query = parse_qs(urlparse(url).query)
        initial_query = parse_qs(urlparse(initial_course_url).query)
        target_course_id = (target_query.get("id") or [None])[0]
        initial_course_id = (initial_query.get("id") or [None])[0]
        if not target_course_id or target_course_id != initial_course_id:
            return False
        if "/course/view.php" in lowered and normalize_url(url) != normalize_url(
            initial_course_url
        ):
            # Algunos formatos de Moodle muestran una única sección por página.
            # Solo se siguen variantes del mismo curso que indiquen una sección;
            # se ignoran enlaces de idioma y otras variantes de navegación.
            section_keys = {"section", "sectionid", "topic"}
            if not section_keys.intersection(target_query):
                return False
    if "/local/joulegrader/view.php" in lowered:
        target_query = parse_qs(urlparse(url).query)
        initial_query = parse_qs(urlparse(initial_course_url).query)
        target_course_id = (target_query.get("courseid") or [None])[0]
        initial_course_id = (initial_query.get("id") or [None])[0]
        if not target_course_id or target_course_id != initial_course_id:
            return False
    return any(pattern in lowered for pattern in CRAWL_PATTERNS)


def is_snapshot_candidate(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path not in SNAPSHOT_PATHS:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if path == "/local/joulegrader/view.php":
        # La portada y cada área individual pueden contener calificaciones,
        # comentarios o respuestas escritas directamente en Moodle. No se
        # captura action=downloadall: ese enlace ya se descarga como ZIP.
        return bool(query.get("courseid") and set(query).issubset({"courseid", "guser", "garea"}))
    if path == "/mod/quiz/review.php":
        # La URL sin ``page`` representa la primera página; las siguientes se
        # guardan por separado. ``showall=0`` es un duplicado de la primera.
        return bool(
            query.get("attempt")
            and query.get("cmid")
            and "showall" not in query
            and set(query).issubset({"attempt", "cmid", "page"})
        )
    if path == "/course/overview.php":
        return set(query).issubset({"id"})
    return set(query).issubset({"id"})


def snapshot_filename(title: str, url: str) -> str:
    """Crea un nombre estable y descriptivo para cada página offline."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    base = title or "Actividad"
    if parsed.path.lower() == "/mod/quiz/review.php":
        attempt = (query.get("attempt") or ["desconocido"])[0]
        try:
            page_number = int((query.get("page") or ["0"])[0]) + 1
        except (TypeError, ValueError):
            page_number = 1
        base = f"{base} - revisión {attempt} - página {page_number}"
    elif parsed.path.lower() == "/local/joulegrader/view.php" and query.get("garea"):
        area = query["garea"][0]
        base = f"{base} - detalle Open Grader {area}"
    filename = sanitize_component(base, "Actividad", max_length=120)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return filename


def _course_id(course_url: str) -> int | None:
    value = (parse_qs(urlparse(course_url).query).get("id") or [None])[0]
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def fetch_course_index(page: Page, course_url: str) -> tuple[list[dict], bool, str]:
    """Obtiene el índice completo de secciones y actividades del curso.

    Moodle puede mostrar solo una sección o cargar bloques de forma diferida. El
    servicio autenticado devuelve las URLs raíz de todas las actividades que la
    sesión actual puede ver; después cada tarea se abre normalmente para recoger
    también entregas y comentarios privados del alumno.
    """
    course_id = _course_id(course_url)
    if course_id is None:
        return [], False, "La URL no contiene un identificador de curso"
    try:
        result = page.evaluate(FETCH_COURSE_CONTENTS_JS, course_id)
    except Exception as exc:
        return [], False, f"{type(exc).__name__}: {exc}"

    unique = []
    seen: set[str] = set()
    for link in result.get("links", []):
        target = normalize_url(link.get("href", ""))
        if not target or target in seen:
            continue
        seen.add(target)
        unique.append({**link, "href": target})
    return unique, bool(result.get("available")), str(result.get("error") or "")


def is_indexed_module_candidate(link: dict, config: dict) -> bool:
    """Acepta solo la URL raíz de una actividad entregada por Moodle."""
    if not link.get("from_course_index"):
        return False
    url = link.get("href", "")
    parsed = urlparse(url)
    return bool(
        not _excluded(url, config)
        and host_allowed(url, config["allowed_domains"])
        and "/mod/" in parsed.path.lower()
        and parsed.path.lower().endswith("/view.php")
    )


def _course_name(course: dict, index: int) -> str:
    text = (course.get("text") or "").strip()
    return sanitize_component(text, f"Asignatura_{index:02d}")


class CourseScraper:
    def __init__(
        self, context: BrowserContext, page: Page, config: dict, logger: logging.Logger
    ) -> None:
        self.context = context
        self.page = page
        self.config = config
        self.logger = logger
        self.manifest = ManifestStore(config["manifest_file"])
        self.downloader = Downloader(context, config, self.manifest, logger)
        self.attempted_download_urls: set[str] = set()

    def run(self, courses: list[dict]) -> dict:
        totals = Counter()
        per_course: dict[str, Counter] = {}
        for index, course in enumerate(courses, start=1):
            name = _course_name(course, index)
            url = course["href"]
            print(f"\n[{index}/{len(courses)}] {name}")
            counts = self._scrape_course(name, url)
            per_course[name] = counts
            totals.update(counts)
            print(
                f"  Descargados: {counts['downloaded']} | Omitidos: {counts['skipped']} | Errores: {counts['errors']}"
            )
        self.manifest.save()
        return {
            "courses": len(courses),
            "per_course": {name: dict(counts) for name, counts in per_course.items()},
            "totals": dict(totals),
        }

    def _scrape_course(self, course_name: str, initial_url: str) -> Counter:
        counts = Counter(downloaded=0, skipped=0, errors=0, pages=0)
        queue = deque([(normalize_url(initial_url), 0, "Otros")])
        visited: set[str] = set()
        max_pages = int(self.config["max_pages_per_course"])
        max_depth = int(self.config["max_depth"])
        course_index_loaded = False

        while queue and len(visited) < max_pages:
            url, depth, inherited_section = queue.popleft()
            if url in visited or depth > max_depth or _excluded(url, self.config):
                continue
            visited.add(url)
            navigation_url = url
            if depth == 0:
                navigation_url = _with_preferred_language(
                    url, str(self.config.get("preferred_language", "es"))
                )
            loaded, navigation_error = self._navigate_with_retries(navigation_url)
            if not loaded:
                self.logger.warning("No se pudo abrir %s: %s", url, navigation_error)
                counts["errors"] += 1
                continue

            counts["pages"] += 1
            self.logger.info("PÁGINA VISITADA [%s]: %s", course_name, self.page.url)
            snapshot_outcome = self._save_activity_snapshot(course_name, inherited_section, url)
            if snapshot_outcome == "downloaded":
                counts["downloaded"] += 1
            elif snapshot_outcome in {"duplicate_url", "duplicate_hash"}:
                counts["skipped"] += 1
            elif snapshot_outcome == "error":
                counts["errors"] += 1
            links = []
            for frame in self.page.frames:
                links.extend(extract_links(frame))
            if not course_index_loaded and depth == 0:
                course_index_loaded = True
                indexed_links, index_available, index_error = fetch_course_index(
                    self.page, initial_url
                )
                links.extend(indexed_links)
                if indexed_links:
                    print(
                        f"  Índice interno: {len(indexed_links)} actividades/archivos localizados"
                    )
                    self.logger.info(
                        "ÍNDICE DEL CURSO: %s actividades/archivos descubiertos para %s",
                        len(indexed_links),
                        course_name,
                    )
                elif index_available:
                    self.logger.warning(
                        "ÍNDICE DEL CURSO sin resultados para %s: %s",
                        course_name,
                        index_error or "sin detalle",
                    )
                else:
                    self.logger.info(
                        "ÍNDICE DEL CURSO no disponible para %s; se usa el rastreo HTML",
                        course_name,
                    )
            seen_on_page: set[str] = set()
            for link in links:
                target = normalize_url(link["href"])
                if not target or target in seen_on_page or _excluded(target, self.config):
                    continue
                seen_on_page.add(target)
                link["href"] = target
                section = link.get("section") or inherited_section or "Otros"
                internal = host_allowed(target, self.config["allowed_domains"])

                external_direct = (
                    not internal
                    and self.config["external_direct_files"]
                    and url_extension(target) in self.config["include_extensions"]
                )
                if is_download_candidate(link, self.config) and (internal or external_direct):
                    if target in self.attempted_download_urls:
                        continue
                    self.attempted_download_urls.add(target)
                    link["source_url"] = self.page.url
                    outcome = self.downloader.download(course_name, section, link)
                    if outcome.status == "downloaded":
                        counts["downloaded"] += 1
                    elif outcome.status in {"duplicate_url", "duplicate_hash", "skipped_media"}:
                        counts["skipped"] += 1
                    elif outcome.status == "error":
                        counts["errors"] += 1
                    elif (
                        outcome.status == "not_file"
                        and depth < max_depth
                        and (
                            is_crawl_candidate(target, self.config, initial_url)
                            or is_indexed_module_candidate(link, self.config)
                        )
                    ):
                        queue.append((target, depth + 1, section))
                elif depth < max_depth and (
                    is_crawl_candidate(target, self.config, initial_url)
                    or is_indexed_module_candidate(link, self.config)
                ):
                    queue.append((target, depth + 1, section))
        if queue and len(visited) >= max_pages:
            self.logger.warning(
                "LÍMITE DE PÁGINAS alcanzado para %s: %s páginas visitadas y %s pendientes",
                course_name,
                len(visited),
                len(queue),
            )
        return counts

    def _save_activity_snapshot(self, course_name: str, section: str, url: str) -> str:
        """Guarda como PDF el contenido HTML que Moodle no ofrece como archivo."""
        if not self.config.get("save_activity_pages", True) or not is_snapshot_candidate(url):
            return "not_applicable"
        if self.manifest.already_has_url(url):
            self.logger.info("OMITIDO (página offline ya registrada): %s", url)
            return "duplicate_url"

        try:
            try:
                title = self.page.locator("h1").first.inner_text(timeout=2000).strip()
            except Exception:
                title = self.page.title().split("|", 1)[0].strip()
            filename = snapshot_filename(title, url)

            course_dir = sanitize_component(course_name, "Asignatura", max_length=65)
            destination_dir = self.config["output_dir"] / course_dir / "Páginas offline"
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / filename
            if destination.exists():
                destination = unique_path(destination)
            partial = destination.with_suffix(destination.suffix + ".part")

            self.page.emulate_media(media="screen")
            payload = self.page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "12mm", "right": "10mm", "bottom": "12mm", "left": "10mm"},
            )
            digest = hashlib.sha256(payload).hexdigest()
            if self.manifest.already_has_hash(digest):
                self.logger.info("OMITIDO (página offline duplicada): %s", url)
                return "duplicate_hash"
            partial.write_bytes(payload)
            partial.replace(destination)

            item = {
                "asignatura": course_name,
                "seccion": section or "Páginas offline",
                "nombre": destination.name,
                "url_original": url,
                "url_final": self.page.url,
                "ruta_local": str(destination.relative_to(self.config["output_dir"])),
                "fecha_descarga": datetime.now(timezone.utc).isoformat(),
                "tamano_bytes": len(payload),
                "sha256": digest,
                "content_type": "application/pdf",
                "tipo": "pagina_offline",
            }
            self.manifest.add_file(item)
            self.logger.info("PÁGINA OFFLINE GUARDADA: %s -> %s", url, destination)
            return "downloaded"
        except Exception as exc:
            self.logger.warning(
                "No se pudo guardar la página offline %s: %s: %s",
                url,
                type(exc).__name__,
                exc,
            )
            return "error"

    def _navigate_with_retries(self, url: str) -> tuple[bool, str]:
        """Tolera recargas de Moodle, incluidas las provocadas por idioma forzado."""
        last_error = "La página no llegó a mostrar contenido"
        previous_url = normalize_url(self.page.url)
        timeout = int(self.config["navigation_timeout_ms"])
        delay = int(self.config["delay_between_pages_ms"])

        for attempt in range(1, 4):
            try:
                self.page.goto(url, wait_until="commit", timeout=timeout)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.logger.info(
                    "Navegación %s/3 interrumpida para %s; comprobando si Moodle recargó la página",
                    attempt,
                    url,
                )

            try:
                self.page.wait_for_function(
                    "document.readyState !== 'loading' && document.body "
                    "&& document.body.innerText.trim().length > 40",
                    timeout=min(timeout, 30000),
                )
            except Exception:
                pass
            self.page.wait_for_timeout(delay)

            try:
                current_url = normalize_url(self.page.url)
                title = self.page.title().strip().lower()
                has_content = bool(
                    self.page.evaluate(
                        "document.readyState !== 'loading' && document.body "
                        "&& document.body.innerText.trim().length > 40"
                    )
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                current_url = previous_url
                title = "loading"
                has_content = False

            moved = current_url != previous_url or current_url == normalize_url(url)
            is_login = "/login/" in current_url.lower()
            is_loading = title.startswith("loading ") or title == "loading"
            if moved and has_content and not is_login and not is_loading:
                if attempt > 1 or last_error != "La página no llegó a mostrar contenido":
                    self.logger.info("Navegación recuperada correctamente: %s", current_url)
                return True, ""

            if attempt < 3:
                self.page.wait_for_timeout(1000 * attempt)
        return False, last_error


def load_courses(config: dict) -> list[dict]:
    inspection_path: Path = config["inspection_file"]
    if not inspection_path.exists():
        raise FileNotFoundError("No existe inspection.json. Ejecuta primero la inspección.")
    report = json.loads(inspection_path.read_text(encoding="utf-8"))
    courses = report.get("course_candidates", [])
    unique = []
    seen = set()
    for course in courses:
        url = normalize_url(course.get("href", ""))
        if url and url not in seen:
            seen.add(url)
            unique.append({**course, "href": url})
    return unique
