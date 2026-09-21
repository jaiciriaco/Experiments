from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urldefrag, urlparse, urlunparse

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(value: str, fallback: str = "Sin_nombre", max_length: int = 120) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value or "")
    value = re.sub(r"\s+", " ", value).strip().rstrip(". ")
    if not value:
        value = fallback
    stem = value.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED:
        value = f"_{value}"
    if len(value) > max_length:
        suffix = Path(value).suffix
        room = max(1, max_length - len(suffix))
        value = value[:room].rstrip() + suffix
    return value


def normalize_url(url: str) -> str:
    clean, _ = urldefrag(url.strip())
    parsed = urlparse(clean)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    return urlunparse((scheme, netloc, parsed.path, parsed.params, parsed.query, ""))


def host_allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain.lower() or host.endswith("." + domain.lower()) for domain in domains)


def url_extension(url: str) -> str:
    return Path(unquote(urlparse(url).path)).suffix.lower()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def is_probably_html(content_type: str) -> bool:
    lowered = content_type.lower()
    return "text/html" in lowered or "application/xhtml" in lowered
