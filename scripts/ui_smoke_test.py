import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def wait_text_contains(page, selector: str, token: str, timeout_ms: int) -> None:
    js = """
    ([selector, token]) => {
      const node = document.querySelector(selector);
      if (!node) return false;
      return (node.textContent || '').includes(token);
    }
    """
    page.wait_for_function(js, arg=[selector, token], timeout=timeout_ms)


def run(base_url: str, search_text: str, screenshot_path: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("#statusText", timeout=30_000)
            wait_text_contains(page, "#statusText", "Loaded", timeout_ms=30_000)

            items = page.locator(".slide-item")
            if items.count() == 0:
                raise RuntimeError("No slide items rendered in UI.")

            first_name = items.first.locator(".slide-name").inner_text()
            items.first.click()
            wait_text_contains(page, "#statusText", "Opened", timeout_ms=30_000)

            meta_name = page.locator("#mName").inner_text().strip()
            if not meta_name or meta_name == "-":
                raise RuntimeError("Metadata panel did not update after opening slide.")

            if search_text:
                page.fill("#searchInput", search_text)
                wait_text_contains(page, "#statusText", "Loaded", timeout_ms=30_000)
                if page.locator(".slide-item").count() == 0:
                    raise RuntimeError(f"Search returned no results for '{search_text}'.")

            page.click("#refreshBtn")
            wait_text_contains(page, "#statusText", "Loaded", timeout_ms=30_000)

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)

            print("UI_HEALTH=ok")
            print(f"FIRST_SLIDE={first_name}")
            print(f"META_NAME={meta_name}")
            print(f"SCREENSHOT={screenshot_path}")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"UI check timeout: {exc}") from exc
        finally:
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local viewer UI smoke test.")
    parser.add_argument("--base-url", required=True, help="Example: http://127.0.0.1:8011")
    parser.add_argument("--search-text", default="", help="Optional search keyword")
    parser.add_argument(
        "--screenshot-path",
        default="scripts/artifacts/ui-smoke.png",
        help="Screenshot output path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args.base_url, args.search_text, Path(args.screenshot_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"UI_HEALTH=failed reason={exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
