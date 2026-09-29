from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"], default=True, default_for_string=True),
)


def render_html(context: dict) -> str:
    return _env.get_template("briefing.html.j2").render(**context)


def write_html(context: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{context['target_date']}.html"
    path.write_text(render_html(context), encoding="utf-8")
    return path


def build_context(target_date: date, headline: str, counts: dict,
                  cards: list[dict], warnings: list[str],
                  listing_sections: list[dict] | None = None,
                  price_changes: list[dict] | None = None,
                  thermometer: list[dict] | None = None) -> dict:
    return {
        "target_date": target_date.isoformat(),
        "headline": headline,
        "counts": counts,
        "cards": cards,
        "warnings": warnings,
        "listing_sections": listing_sections or [],
        "price_changes": price_changes or [],
        "thermometer": thermometer or [],
    }


def html_to_png(html_path: Path, png_path: Path, width: int = 1200) -> Path:
    from playwright.sync_api import sync_playwright

    html_path, png_path = Path(html_path), Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    device_scale_factor=2)
            page.goto(html_path.resolve().as_uri())
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()
    return png_path
