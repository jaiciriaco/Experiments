from __future__ import annotations

import argparse
import logging
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from .browser import open_start_page, persistent_browser
from .config import load_config
from .inspector import run_inspection
from .scraper import CourseScraper, load_courses


def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("uned_backup")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def create_backup_zip(config: dict) -> Path | None:
    output: Path = config["output_dir"]
    if not output.exists():
        return None
    archive_base = output.parent / f"UNED_Backup_{datetime.now().astimezone().date().isoformat()}"
    candidate = archive_base.with_suffix(".zip")
    index = 2
    while candidate.exists():
        candidate = output.parent / f"{archive_base.name}_{index}.zip"
        index += 1
    with zipfile.ZipFile(
        candidate, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        for file_path in output.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, Path(output.name) / file_path.relative_to(output))
        for key in ("inspection_file", "manifest_file", "log_file"):
            file_path: Path = config[key]
            if file_path.exists() and not file_path.is_relative_to(output):
                archive.write(file_path, file_path.name)
    return candidate


def command_inspect(config: dict) -> dict:
    with persistent_browser(config) as (_, page):
        open_start_page(page, config["start_url"])
        return run_inspection(page, config)


def command_backup(config: dict, skip_ready_prompt: bool = False) -> dict:
    courses = load_courses(config)
    if not courses:
        raise RuntimeError(
            "No hay asignaturas detectadas. Repite la inspección o configura manual_course_urls."
        )
    logger = setup_logging(config["log_file"])
    with persistent_browser(config) as (context, page):
        if not skip_ready_prompt:
            open_start_page(page, config["start_url"])
            print("\nComprueba que tu sesión de Ágora está iniciada en el navegador.")
            input("Pulsa INTRO para comenzar la copia de todas las asignaturas visibles...")
        scraper = CourseScraper(context, page, config, logger)
        summary = scraper.run(courses)
    archive = create_backup_zip(config) if config.get("create_zip") else None
    summary["zip"] = str(archive) if archive else None
    return summary


def command_all(config: dict) -> dict:
    # Se usan dos aperturas del navegador porque el perfil persistente queda guardado
    # entre ambas fases y así se cierra limpiamente el inspector antes del backup.
    command_inspect(config)
    return command_backup(config, skip_ready_prompt=False)


def command_doctor(config: dict) -> None:
    print(f"Python: {sys.version.split()[0]}")
    print("Configuración: OK")
    print(f"URL inicial: {config['start_url']}")
    print(f"Destino: {config['output_dir']}")
    try:
        import playwright  # noqa: F401
        import requests  # noqa: F401

        print("Dependencias Python: OK")
    except ImportError as exc:
        print(f"Dependencias Python: FALTAN ({exc})")
        raise SystemExit(1) from exc


def print_summary(summary: dict) -> None:
    print("\n========== RESUMEN ==========")
    print(f"Asignaturas procesadas: {summary.get('courses', 0)}")
    for course, counts in summary.get("per_course", {}).items():
        print(
            f"  - {course}: {counts.get('downloaded', 0)} descargados, {counts.get('errors', 0)} errores"
        )
    totals = summary.get("totals", {})
    print(f"Total descargado: {totals.get('downloaded', 0)}")
    print(f"Total omitido: {totals.get('skipped', 0)}")
    print(f"Total de errores: {totals.get('errors', 0)}")
    if summary.get("zip"):
        print(f"ZIP creado: {summary['zip']}")
    print("Revisa manifest.json y download_log.txt para el detalle.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copia local de materiales accesibles de Campus UNED"
    )
    parser.add_argument(
        "command", choices=["inspect", "backup", "all", "doctor"], nargs="?", default="all"
    )
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    try:
        config, _ = load_config(args.config)
        if args.command == "doctor":
            command_doctor(config)
            return 0
        if args.command == "inspect":
            command_inspect(config)
            return 0
        summary = command_all(config) if args.command == "all" else command_backup(config)
        print_summary(summary)
        return 0
    except KeyboardInterrupt:
        print("\nOperación cancelada. Los archivos ya descargados se conservan.")
        return 130
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        print("Ejecuta diagnostico.bat y consulta download_log.txt si existe.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
