from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "start_url": "https://agora.uned.es/my/courses.php",
    "courses_url": "https://agora.uned.es/my/courses.php",
    "profile_dir": ".browser-profile",
    "output_dir": "UNED_Backup",
    "inspection_file": "inspection.json",
    "manifest_file": "manifest.json",
    "log_file": "download_log.txt",
    "headless": False,
    "browser_channel": "chrome",
    "preferred_language": "es",
    "slow_mo_ms": 0,
    "navigation_timeout_ms": 45000,
    "download_timeout_seconds": 120,
    "delay_between_pages_ms": 700,
    "max_pages_per_course": 500,
    "max_depth": 4,
    "max_file_size_mb": 1024,
    "create_zip": True,
    "allowed_domains": ["uned.es"],
    "external_direct_files": True,
    "include_images": True,
    "save_activity_pages": True,
    "include_extensions": [
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".rtf",
        ".ppt",
        ".pptx",
        ".odp",
        ".xls",
        ".xlsx",
        ".ods",
        ".csv",
        ".tsv",
        ".txt",
        ".md",
        ".epub",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".py",
        ".ipynb",
        ".java",
        ".c",
        ".cpp",
        ".cs",
        ".js",
        ".ts",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
    ],
    "exclude_url_patterns": [
        "logout",
        "cerrar-sesion",
        "delete",
        "remove",
        "unenrol",
        "unsubscribe",
        "action=delete",
        "action=remove",
        "confirm=",
        "calendar/export",
        "message/",
        "grade/report",
        "admin/",
    ],
    "manual_course_urls": [],
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        user_config = json.load(handle)
    config = _merge(DEFAULT_CONFIG, user_config)
    base_dir = config_path.parent

    for key in ("profile_dir", "output_dir", "inspection_file", "manifest_file", "log_file"):
        value = Path(config[key])
        if not value.is_absolute():
            value = base_dir / value
        config[key] = value.resolve()
    return config, base_dir
