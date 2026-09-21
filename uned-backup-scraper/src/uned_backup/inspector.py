from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page

from .utils import atomic_write_json, normalize_url, url_extension

EXTRACT_LINKS_JS = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const sectionFor = (anchor) => {
    const container = anchor.closest('section, article, li, .section, .course-section, .activity, .card, tr');
    if (container) {
      const heading = container.querySelector('h1, h2, h3, h4, h5, h6, .sectionname, .card-title, .instancename');
      if (heading && clean(heading.innerText)) return clean(heading.innerText);
    }
    let node = anchor;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      let previous = node.previousElementSibling;
      while (previous) {
        if (/^H[1-6]$/.test(previous.tagName) && clean(previous.innerText)) return clean(previous.innerText);
        const heading = previous.querySelector && previous.querySelector('h1, h2, h3, h4, h5, h6');
        if (heading && clean(heading.innerText)) return clean(heading.innerText);
        previous = previous.previousElementSibling;
      }
    }
    return 'Otros';
  };
  return Array.from(document.querySelectorAll('a[href]')).map((anchor) => ({
    text: clean(anchor.innerText || anchor.textContent || anchor.getAttribute('aria-label') || anchor.title),
    href: anchor.href,
    title: clean(anchor.title),
    aria_label: clean(anchor.getAttribute('aria-label')),
    download: anchor.hasAttribute('download'),
    section: sectionFor(anchor),
    visible: !!(anchor.offsetWidth || anchor.offsetHeight || anchor.getClientRects().length)
  })).filter((item) => item.href && /^(https?:|blob:)/i.test(item.href));
}
"""


FETCH_MOODLE_COURSES_JS = r"""
async () => {
  const cfg = window.M && window.M.cfg ? window.M.cfg : null;
  if (!cfg || !cfg.sesskey || !cfg.wwwroot) {
    return {courses: [], available: false};
  }
  const method = 'core_course_get_enrolled_courses_by_timeline_classification';
  const endpoint = `${cfg.wwwroot}/lib/ajax/service.php?sesskey=${encodeURIComponent(cfg.sesskey)}&info=${method}`;
  const request = [{
    index: 0,
    methodname: method,
    args: {
      classification: 'all',
      limit: 0,
      offset: 0,
      sort: 'fullname',
      customfieldname: '',
      customfieldvalue: ''
    }
  }];
  const response = await fetch(endpoint, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(request)
  });
  if (!response.ok) {
    return {courses: [], available: true};
  }
  const payload = await response.json();
  const first = Array.isArray(payload) ? payload[0] : null;
  if (!first || first.error || !first.data) {
    return {courses: [], available: true};
  }
  const rawCourses = Array.isArray(first.data) ? first.data : (first.data.courses || []);
  const courses = rawCourses
    .filter((course) => course && course.id && course.hidden !== true && course.visible !== 0)
    .map((course) => ({
      id: Number(course.id),
      name: String(course.fullname || course.displayname || course.shortname || `Curso ${course.id}`),
      url: String(course.viewurl || `${cfg.wwwroot}/course/view.php?id=${course.id}`)
    }));
  return {courses, available: true};
}
"""


def _course_score(link: dict[str, Any]) -> int:
    href = link["href"].lower()
    text = (link.get("text") or "").lower()
    score = 0
    if "/course/view.php" in href or "/courses/" in href:
        score += 8
    if "/portal/site/" in href or "courseid=" in href or "course_id=" in href:
        score += 6
    if any(token in href for token in ("agora", "alf", "curso-virtual", "curso_virtual")):
        score += 2
    if any(token in text for token in ("asignatura", "curso virtual", "aula virtual")):
        score += 2
    if len(text) >= 10 and any(char.isdigit() for char in text):
        score += 1
    if text in {"mis cursos", "cursos", "estudios", "campus", "inicio", "home"}:
        score -= 4
    if not link.get("visible", True):
        score -= 2
    return score


def _dedupe_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for link in links:
        href = normalize_url(link.get("href", ""))
        key = (href, link.get("text", ""))
        if not href or key in seen:
            continue
        seen.add(key)
        item = dict(link)
        item["href"] = href
        result.append(item)
    return result


def extract_links(frame: Frame) -> list[dict[str, Any]]:
    try:
        links = frame.evaluate(EXTRACT_LINKS_JS)
    except Exception:
        return []
    return _dedupe_links(links)


def inspect_page(page: Page, config: dict) -> dict[str, Any]:
    frames = []
    all_links: list[dict[str, Any]] = []
    for frame in page.frames:
        links = extract_links(frame)
        all_links.extend(links)
        frames.append(
            {
                "url": frame.url,
                "name": frame.name,
                "is_main": frame == page.main_frame,
                "links_found": len(links),
            }
        )
    all_links = _dedupe_links(all_links)
    candidates = []
    for link in all_links:
        score = _course_score(link)
        if score >= 5:
            candidates.append({**link, "score": score})
    candidates.sort(key=lambda item: (-item["score"], item.get("text", "")))
    unique_courses = []
    seen_urls: set[str] = set()
    for item in candidates:
        if item["href"] in seen_urls:
            continue
        seen_urls.add(item["href"])
        unique_courses.append(item)

    file_candidates = [
        link
        for link in all_links
        if link.get("download") or url_extension(link["href"]) in config["include_extensions"]
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "page": {"url": page.url, "title": page.title()},
        "frames": frames,
        "links": all_links,
        "course_candidates": unique_courses,
        "file_candidates": file_candidates,
    }


def fetch_moodle_courses(page: Page) -> tuple[list[dict[str, Any]], bool]:
    """Obtiene los cursos mediante el mismo servicio AJAX que usa 'Mis cursos'."""
    try:
        result = page.evaluate(FETCH_MOODLE_COURSES_JS)
    except Exception:
        return [], False
    courses = []
    for course in result.get("courses", []):
        if not course.get("url"):
            continue
        courses.append(
            {
                "href": normalize_url(course["url"]),
                "text": course.get("name") or f"Curso {course.get('id', '')}",
                "section": "Ágora - Mis cursos",
                "score": 100,
                "source": "moodle_ajax",
            }
        )
    return courses, bool(result.get("available"))


def _merge_course_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    seen: set[str] = set()
    for group in groups:
        for course in group:
            url = normalize_url(course.get("href", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append({**course, "href": url})
    return merged


def moodle_session_ready(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                "Boolean(window.M && M.cfg && M.cfg.sesskey && M.cfg.wwwroot "
                "&& document.readyState !== 'loading')"
            )
        )
    except Exception:
        return False


def run_inspection(page: Page, config: dict) -> dict[str, Any]:
    print("\nEn el navegador:")
    print("  1. Inicia sesión tú mismo si Ágora te lo solicita.")
    print("  2. Espera hasta ver la página REAL de Ágora, no una pestaña que diga 'Loading'.")
    print("  3. No hace falta abrir cada asignatura; basta con estar dentro de Ágora.")

    for attempt in range(1, 4):
        input("\nCuando Ágora esté completamente visible, vuelve aquí y pulsa INTRO...")
        if moodle_session_ready(page):
            break
        try:
            title = page.title()
        except Exception:
            title = "(página aún no disponible)"
        print(f"\nÁgora todavía no ha terminado de cargar. Título actual: {title}")
        if attempt < 3:
            print("Espera a que aparezca el contenido real en el navegador y vuelve a intentarlo.")
    else:
        raise RuntimeError(
            "Ágora no llegó a cargar una sesión de Moodle válida. "
            "Comprueba el navegador, la conexión y el inicio de sesión."
        )

    try:
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    report = inspect_page(page, config)
    ajax_courses, ajax_available = fetch_moodle_courses(page)
    report["course_candidates"] = _merge_course_candidates(
        ajax_courses, report["course_candidates"]
    )
    report["course_discovery"] = {
        "method": "moodle_ajax_and_rendered_dom",
        "ajax_available": ajax_available,
        "ajax_courses_found": len(ajax_courses),
    }

    manual = config.get("manual_course_urls", [])
    for item in manual:
        if isinstance(item, str):
            url, name = item, "Asignatura manual"
        else:
            url, name = item.get("url", ""), item.get("name", "Asignatura manual")
        if url:
            report["course_candidates"].append(
                {"href": normalize_url(url), "text": name, "section": "Manual", "score": 100}
            )

    report["course_candidates"] = _merge_course_candidates(report["course_candidates"])

    inspection_path: Path = config["inspection_file"]
    atomic_write_json(inspection_path, report)
    print(f"\nInspección guardada: {inspection_path}")
    print(f"Asignaturas candidatas detectadas: {len(report['course_candidates'])}")
    for index, course in enumerate(report["course_candidates"], start=1):
        print(f"  {index:02d}. {course.get('text') or '(sin título)'}")
    if not report["course_candidates"]:
        print("\nNo se detectaron enlaces de asignaturas automáticamente.")
        print(
            "Abre 'Mis cursos' en Ágora, comprueba que aparecen las tarjetas y repite la inspección."
        )
        print("Como alternativa, README.md explica cómo añadir enlaces manuales.")
    return report
