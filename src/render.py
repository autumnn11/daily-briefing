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


def rerender(target_date: date, db_path: Path, out_dir: Path,
             watchlist_path: Path | None = None) -> Path:
    """DB에 이미 쌓인 것만으로 그 날짜 브리핑을 다시 만든다. 네트워크를 쓰지 않는다.

    스펙 6절이 요구하는 재렌더 경로다. 30일 동안 브리핑 형식을 다듬을 때
    API를 다시 때리지 않고 과거 날짜를 반복해서 뽑아볼 수 있어야 한다.
    """
    from src.config import load_watchlist
    from src.judge import badges, judge_price
    from src.models import TradeRow
    from src.store import init_db, max_floor

    conn = init_db(db_path)
    day = target_date.isoformat()
    rows = conn.execute(
        "SELECT * FROM trades WHERE first_seen = ? ORDER BY price DESC", (day,)
    ).fetchall()

    targets = load_watchlist(watchlist_path) if watchlist_path else []
    seoul_keys = {(t.lawd_cd, d) for t in targets if t.region == "수도권" for d in t.dongs}

    cards = []
    for r in rows:
        if seoul_keys and (r["lawd_cd"], r["dong"]) not in seoul_keys:
            continue
        trade = TradeRow(r["lawd_cd"], r["dong"], r["complex_name"], r["deal_date"],
                         r["area"], r["floor"], r["price"], r["deal_kind"],
                         bool(r["released"]), r["released_date"], r["register_date"],
                         r["asset"])
        v = judge_price(conn, trade)
        eok, rest = divmod(r["price"], 100_000_000)
        man = rest // 10_000
        cards.append({
            "complex_name": r["complex_name"], "area": r["area"], "floor": r["floor"],
            "price_text": f"{eok}억 {man:,}" if man else f"{eok}억",
            "verdict": v.kind, "pct": v.pct,
            "badges": badges(trade, max_floor(conn, r["lawd_cd"], r["dong"],
                                              r["complex_name"]), target_date),
            "deal_date": r["deal_date"],
        })

    ctx = build_context(
        target_date,
        f"{day} 브리핑 다시 렌더 ({len(cards)}건)",
        {"신규신고": len(cards), "신규매물": 0, "소멸매물": 0,
         "가격조정": 0, "계약파기": 0},
        cards,
        ["DB에 저장된 내용으로 다시 렌더했습니다 (수집 없음)"],
    )
    html = write_html(ctx, out_dir)
    html_to_png(html, html.with_suffix(".png"))
    return html


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="DB에 쌓인 내용으로 과거 브리핑을 다시 렌더한다")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--db", default="data/briefing.db")
    ap.add_argument("--out", default="briefings")
    ap.add_argument("--watchlist", default="watchlist.yml")
    ns = ap.parse_args(argv)
    path = rerender(date.fromisoformat(ns.date), Path(ns.db), Path(ns.out),
                    Path(ns.watchlist))
    print(path)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
