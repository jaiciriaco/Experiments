from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from playwright.sync_api import BrowserContext

from .utils import atomic_write_json, is_probably_html, sanitize_component, unique_path


@dataclass
class DownloadOutcome:
    status: str
    path: Path | None = None
    error: str | None = None


class ManifestStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            try:
                import json

                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {"version": 1, "files": [], "errors": []}
        else:
            self.data = {"version": 1, "files": [], "errors": []}
        self._refresh_indexes()

    def _refresh_indexes(self) -> None:
        self.urls = {item.get("url_original") for item in self.data["files"]}
        self.hashes = {item.get("sha256") for item in self.data["files"] if item.get("sha256")}

    def already_has_url(self, url: str) -> bool:
        return url in self.urls

    def already_has_hash(self, digest: str) -> bool:
        return digest in self.hashes

    def add_file(self, item: dict) -> None:
        self.data["files"].append(item)
        self.urls.add(item["url_original"])
        self.hashes.add(item["sha256"])
        self.save()

    def add_error(self, item: dict) -> None:
        self.data["errors"].append(item)
        self.save()

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(self.path, self.data)


def _filename_from_disposition(value: str) -> str | None:
    if not value:
        return None
    message = Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    return unquote(filename) if filename else None


def _repair_mojibake(value: str) -> str:
    """Repara nombres UTF-8 interpretados erróneamente como Latin-1."""
    if not any(marker in value for marker in ("Ã", "Â", "â€")):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _guess_filename(
    url: str, link_text: str, headers: requests.structures.CaseInsensitiveDict
) -> str:
    name = _filename_from_disposition(headers.get("content-disposition", ""))
    if not name:
        candidate = Path(unquote(urlparse(url).path)).name
        if "." in candidate:
            name = candidate
    if not name and "." in link_text and len(link_text) < 180:
        name = link_text
    if not name:
        mime = headers.get("content-type", "").split(";", 1)[0].strip()
        extension = mimetypes.guess_extension(mime) or ""
        name = f"archivo{extension}"
    return sanitize_component(_repair_mojibake(name), "archivo")


class Downloader:
    def __init__(
        self,
        context: BrowserContext,
        config: dict,
        manifest: ManifestStore,
        logger: logging.Logger,
    ) -> None:
        self.context = context
        self.config = config
        self.manifest = manifest
        self.logger = logger

    def _session_for(self, url: str, referer: str | None = None) -> requests.Session:
        session = requests.Session()
        try:
            user_agent = self.context.pages[0].evaluate("navigator.userAgent")
            session.headers["User-Agent"] = user_agent
        except Exception:
            pass
        session.headers["Referer"] = referer or url
        for cookie in self.context.cookies():
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
        return session

    def download(self, course: str, section: str, link: dict) -> DownloadOutcome:
        url = link["href"]
        if self.manifest.already_has_url(url):
            self.logger.info("OMITIDO (URL ya registrada): %s", url)
            return DownloadOutcome("duplicate_url")

        retries = 3
        last_error = "Error desconocido"
        for attempt in range(1, retries + 1):
            try:
                outcome = self._download_once(course, section, link)
                if outcome.status != "error":
                    return outcome
                last_error = outcome.error or last_error
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            self.logger.warning(
                "Intento %s/%s fallido para %s: %s", attempt, retries, url, last_error
            )

        error = {
            "asignatura": course,
            "seccion": section,
            "url": url,
            "error": last_error,
            "fecha": datetime.now(timezone.utc).isoformat(),
        }
        self.manifest.add_error(error)
        self.logger.error("ERROR definitivo: %s", url)
        return DownloadOutcome("error", error=last_error)

    def _download_once(self, course: str, section: str, link: dict) -> DownloadOutcome:
        url = link["href"]
        timeout = int(self.config["download_timeout_seconds"])
        session = self._session_for(url, link.get("source_url"))
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            disposition = response.headers.get("content-disposition", "")
            if content_type.lower().startswith(("video/", "audio/")):
                self.logger.info("OMITIDO (audio/vídeo): %s", url)
                return DownloadOutcome("skipped_media")
            if is_probably_html(content_type) and "attachment" not in disposition.lower():
                return DownloadOutcome("not_file")

            declared_size = int(response.headers.get("content-length") or 0)
            max_bytes = int(self.config["max_file_size_mb"]) * 1024 * 1024
            if declared_size and declared_size > max_bytes:
                return DownloadOutcome(
                    "error", error=f"Supera el límite de {self.config['max_file_size_mb']} MB"
                )

            filename = sanitize_component(
                _guess_filename(response.url, link.get("text", ""), response.headers),
                "archivo",
                max_length=90,
            )
            course_dir = sanitize_component(course, "Asignatura", max_length=65)
            section_dir = sanitize_component(section, "Otros", max_length=55)
            destination_dir = self.config["output_dir"] / course_dir / section_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = unique_path(destination_dir / filename)
            partial = destination.with_suffix(destination.suffix + ".part")
            digest = hashlib.sha256()
            size = 0
            try:
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(
                                f"Supera el límite de {self.config['max_file_size_mb']} MB"
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                file_hash = digest.hexdigest()
                if self.manifest.already_has_hash(file_hash):
                    partial.unlink(missing_ok=True)
                    self.logger.info("OMITIDO (contenido duplicado): %s", url)
                    return DownloadOutcome("duplicate_hash")
                partial.replace(destination)
            except Exception:
                partial.unlink(missing_ok=True)
                raise

        item = {
            "asignatura": course,
            "seccion": section,
            "nombre": destination.name,
            "url_original": url,
            "url_final": response.url,
            "ruta_local": str(destination.relative_to(self.config["output_dir"])),
            "fecha_descarga": datetime.now(timezone.utc).isoformat(),
            "tamano_bytes": size,
            "sha256": file_hash,
            "content_type": content_type,
        }
        self.manifest.add_file(item)
        self.logger.info("DESCARGADO: %s -> %s", url, destination)
        return DownloadOutcome("downloaded", path=destination)
