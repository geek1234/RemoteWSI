import argparse
import sys
import time
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


def wait_normalization_done(page, timeout_ms: int) -> str:
    deadline = time.time() + (timeout_ms / 1000.0)
    last_text = ""
    while time.time() < deadline:
        last_text = page.locator("#normStatus").inner_text().strip()
        if "Done:" in last_text:
            return last_text
        if "failed" in last_text.lower():
            raise RuntimeError(last_text)
        page.wait_for_timeout(500)
    raise RuntimeError(f"Normalization did not finish within timeout. Last status: {last_text}")


def run(base_url: str, screenshot_path: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            wait_text_contains(page, "#statusText", "Loaded", timeout_ms=30_000)

            source_select = page.locator("#normSourceSelect")
            target_select = page.locator("#normTargetSelect")
            if source_select.locator("option").count() == 0:
                raise RuntimeError("No native slides in Source selector.")

            source_value = source_select.locator("option").nth(0).get_attribute("value")
            target_count = target_select.locator("option").count()
            target_value = target_select.locator("option").nth(1).get_attribute("value") if target_count >= 2 else source_value
            if not source_value or not target_value:
                raise RuntimeError("Could not resolve source/target option values.")

            source_select.select_option(value=source_value)
            target_select.select_option(value=target_value)

            if page.locator("#runNormBtn").is_disabled():
                raise RuntimeError("Run GDDN button is disabled.")

            page.click("#runNormBtn")
            wait_text_contains(page, "#normStatus", "Running GDDN", timeout_ms=20_000)
            norm_text = wait_normalization_done(page, timeout_ms=240_000)

            img_ids = [
                "#normSourcePreview",
                "#normTargetPreview",
                "#normTiledPreview",
                "#normMonoPreview",
                "#normDiffPreview",
            ]

            for img_id in img_ids:
                ok = page.evaluate(
                    """
                    (selector) => {
                      const img = document.querySelector(selector);
                      if (!img) return false;
                      return !!img.getAttribute('src') && img.naturalWidth > 0 && img.naturalHeight > 0;
                    }
                    """,
                    img_id,
                )
                if not ok:
                    raise RuntimeError(f"Normalization preview image not loaded: {img_id}")

            card_expectations = [
                ("targetPreview", "Target Preview"),
                ("normalizedTiled", "Normalized (Tiled)"),
                ("normalizedMono", "Normalized (Mono)"),
                ("differenceHeatmap", "Difference Heatmap"),
            ]
            for artifact_key, expected_title in card_expectations:
                card_selector = f'.norm-card.switchable[data-artifact-key="{artifact_key}"]'
                page.click(card_selector)
                wait_text_contains(page, "#mainViewLabel", expected_title, timeout_ms=10_000)
                is_active = page.evaluate(
                    """
                    (selector) => {
                      const card = document.querySelector(selector);
                      return !!card && card.classList.contains('active');
                    }
                    """,
                    card_selector,
                )
                if not is_active:
                    raise RuntimeError(f"Expected active highlight after clicking card: {artifact_key}")

            page.evaluate(
                """
                () => {
                  const slider = document.getElementById('infoSizeRange');
                  if (!slider) return;
                  slider.value = '180';
                  slider.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            panel_height = page.evaluate(
                "() => document.getElementById('metaPanel')?.style.height || ''"
            )
            if panel_height != "180px":
                raise RuntimeError(f"Meta panel size slider did not apply. Current height: {panel_height}")

            page.click("#toggleInfoBtn")
            collapsed_ok = page.evaluate(
                """
                () => {
                  const panel = document.getElementById('metaPanel');
                  const btn = document.getElementById('toggleInfoBtn');
                  return !!panel
                    && panel.classList.contains('collapsed')
                    && panel.style.height === '38px'
                    && !!btn
                    && (btn.textContent || '').includes('Expand Info');
                }
                """
            )
            if not collapsed_ok:
                raise RuntimeError("Info panel collapse did not apply expected state.")

            page.click("#toggleInfoBtn")
            expanded_ok = page.evaluate(
                """
                () => {
                  const panel = document.getElementById('metaPanel');
                  const btn = document.getElementById('toggleInfoBtn');
                  return !!panel
                    && !panel.classList.contains('collapsed')
                    && panel.style.height === '180px'
                    && !!btn
                    && (btn.textContent || '').includes('Collapse Info');
                }
                """
            )
            if not expanded_ok:
                raise RuntimeError("Info panel expand did not restore expected state.")

            page.evaluate(
                """
                () => {
                  const slider = document.getElementById('normSizeRange');
                  if (!slider) return;
                  slider.value = '420';
                  slider.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            norm_panel_height = page.evaluate(
                "() => document.getElementById('normPanel')?.style.height || ''"
            )
            if norm_panel_height != "420px":
                raise RuntimeError(
                    f"GDDN panel size slider did not apply. Current height: {norm_panel_height}"
                )

            page.click("#toggleNormPanelBtn")
            norm_collapsed_ok = page.evaluate(
                """
                () => {
                  const panel = document.getElementById('normPanel');
                  const btn = document.getElementById('toggleNormPanelBtn');
                  return !!panel
                    && panel.classList.contains('collapsed')
                    && panel.style.height === '40px'
                    && !!btn
                    && (btn.textContent || '').includes('Expand GDDN');
                }
                """
            )
            if not norm_collapsed_ok:
                raise RuntimeError("GDDN panel collapse did not apply expected state.")

            page.click("#toggleNormPanelBtn")
            norm_expanded_ok = page.evaluate(
                """
                () => {
                  const panel = document.getElementById('normPanel');
                  const btn = document.getElementById('toggleNormPanelBtn');
                  const slider = document.getElementById('normSizeRange');
                  return !!panel
                    && !panel.classList.contains('collapsed')
                    && panel.style.height === '420px'
                    && !!btn
                    && (btn.textContent || '').includes('Collapse GDDN')
                    && !!slider
                    && !slider.disabled;
                }
                """
            )
            if not norm_expanded_ok:
                raise RuntimeError("GDDN panel expand did not restore expected state.")

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)

            print("COLOR_NORM_UI=ok")
            print(f"NORM_STATUS={norm_text}")
            print(f"SCREENSHOT={screenshot_path}")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"UI color-normalization check timeout: {exc}") from exc
        finally:
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Color-normalization UI smoke test.")
    parser.add_argument("--base-url", required=True, help="Example: http://127.0.0.1:8011")
    parser.add_argument(
        "--screenshot-path",
        default="scripts/artifacts/ui-color-normalization-smoke.png",
        help="Screenshot output path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args.base_url, Path(args.screenshot_path))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"COLOR_NORM_UI=failed reason={exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
