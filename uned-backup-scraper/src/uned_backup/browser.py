from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Error, Page, sync_playwright


@contextmanager
def persistent_browser(config: dict) -> Iterator[tuple[BrowserContext, Page]]:
    profile_dir: Path = config["profile_dir"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        options = {
            "user_data_dir": str(profile_dir),
            "headless": bool(config["headless"]),
            "accept_downloads": True,
            "slow_mo": int(config.get("slow_mo_ms", 0)),
            "viewport": {"width": 1440, "height": 900},
        }
        channel = config.get("browser_channel")
        if channel:
            options["channel"] = channel
        try:
            context = playwright.chromium.launch_persistent_context(**options)
        except Error:
            if not channel:
                raise
            print("No se pudo abrir Google Chrome; se usará Chromium como alternativa.")
            options.pop("channel", None)
            context = playwright.chromium.launch_persistent_context(**options)
        context.set_default_timeout(int(config["navigation_timeout_ms"]))
        context.set_default_navigation_timeout(int(config["navigation_timeout_ms"]))
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


def open_start_page(page: Page, start_url: str) -> None:
    try:
        # 'commit' permite que el usuario vea y complete el SSO sin bloquear
        # el programa esperando a que todas las redirecciones hayan terminado.
        page.goto(start_url, wait_until="commit")
    except Exception as exc:
        print(f"Aviso: no se pudo completar la carga inicial ({exc}).")
        print("Puedes escribir o pegar la dirección manualmente en el navegador abierto.")
