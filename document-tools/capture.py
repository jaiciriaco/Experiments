"""Capture a bounded number of pages using a manually authenticated browser."""
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('output', type=Path)
    parser.add_argument('--pages', type=int, required=True)
    parser.add_argument('--next-selector', required=True)
    parser.add_argument('--wait-ms', type=int, default=2000)
    args = parser.parse_args()
    if urlparse(args.url).scheme not in {'http', 'https'}:
        parser.error('URL must use HTTP or HTTPS')
    if not 1 <= args.pages <= 1000 or args.wait_ms < 500:
        parser.error('Use 1–1000 pages and a wait of at least 500 ms')
    from playwright.sync_api import sync_playwright
    # A new output directory prevents overwriting an earlier capture.
    args.output.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            page.goto(args.url)
            input('Sign in manually, open the first page, then press Enter here: ')
            previous = None
            for index in range(1, args.pages + 1):
                page.wait_for_timeout(args.wait_ms)
                screenshot = page.screenshot()
                digest = hashlib.sha256(screenshot).digest()
                if digest == previous:
                    raise RuntimeError('Identical consecutive capture; check navigation')
                (args.output / f'page_{index:04}.png').write_bytes(screenshot)
                previous = digest
                if index == args.pages:
                    break
                button = page.locator(args.next_selector)
                if button.count() != 1 or not button.is_visible() or not button.is_enabled():
                    raise RuntimeError('Next-page control unavailable or ambiguous')
                button.click()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
