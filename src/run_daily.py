from datetime import date, timedelta
from pathlib import Path
import argparse
import logging
import sys
import time

from src.collect_kor import KorApiError, collect_all
from src.collect_naver import collect_all as collect_naver_all
from src.config import load_env, load_watchlist
from src.judge import badges, judge_price, thermometer
from src.notify import Summary, build_blocks, post
from src.render import build_context, html_to_png, write_html
from src.store import (init_db, listing_counts, log_run, max_floor,
                       upsert_listings, upsert_trades)

log = logging.getLogger(__name__)


def _money(won: int) -> str:
    eok, rest = divmod(won, 100_000_000)
    man = rest // 10_000
    if eok and man:
        return f"{eok}억 {man:,}"
    if eok:
        return f"{eok}억"
    return f"{man:,}만"


TAG = {"신고가": ":arrow_up: 신고가", "하락": ":arrow_down:",
       "보합": "보합", "표본부족": "비교 표본 부족"}


def _line(conn, r, today) -> str:
    v = judge_price(conn, r)
    tag = TAG[v.kind]
    if v.kind == "하락":
        tag = f"{tag} {v.pct}%"
    bs = badges(r, max_floor(conn, r.complex_name), today)
    suffix = f" `{' · '.join(bs)}`" if bs else ""
    return (f"• {r.complex_name} 전용 {r.area:.1f} {r.floor}층 — "
            f"{_money(r.price)} {tag} (계약 {r.deal_date}){suffix}")


def _card(conn, r, today) -> dict:
    v = judge_price(conn, r)
    return {
        "complex_name": r.complex_name, "area": r.area, "floor": r.floor,
        "price_text": _money(r.price), "verdict": v.kind, "pct": v.pct,
        "badges": badges(r, max_floor(conn, r.complex_name), today),
        "deal_date": r.deal_date,
    }


def build_listing_rows(conn, area_name: str, rows, diff, today: date,
                       prev: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """한 구역의 호가 동향 행과 가격 조정 행을 만든다.

    prev(어제 거래유형별 매물 수)는 반드시 upsert_listings 호출 **전에** 재어
    넘겨야 한다. upsert 가 살아남은 매물의 last_seen 을 오늘로 밀어버려서,
    나중에 재면 어제 수가 '오늘 사라진 매물 수'로 쪼그라든다.
    """
    now = listing_counts(conn, area_name, today.isoformat())

    sections: list[dict] = []
    for deal_type, count in sorted(now.items()):
        before = prev.get(deal_type, 0)
        delta = count - before
        lows = [r.price for r in rows if r.deal_type == deal_type]
        sections.append({
            "area_name": area_name,
            "deal_type": deal_type,
            "count": count,
            "prev": before,
            "delta_text": f"{delta:+d}" if delta else "―",
            "new": sum(1 for r in diff.new if r.deal_type == deal_type),
            "gone": sum(1 for r in diff.gone if r["deal_type"] == deal_type),
            "low_text": _money(min(lows)) if lows else "―",
        })

    changes = [{
        "name": c.name,
        "down": c.now < c.prev,
        "delta_text": f"{(c.now - c.prev) / c.prev * 100:+.1f}%",
        "prev_text": _money(c.prev),
        "now_text": _money(c.now),
        "days": c.days,
        "times": c.times,
    } for c in diff.price_changes]

    return sections, changes


def run(today: date, db_path: Path, watchlist_path: Path,
        env: dict, dry_run: bool = False) -> Summary:
    today_s = today.isoformat()
    conn = init_db(db_path)
    targets = load_watchlist(watchlist_path)
    warnings: list[str] = []

    new_trades, released = [], []
    t0 = time.monotonic()
    if targets:
        try:
            rows = collect_all(env.get("KOR_API_KEY", ""), targets, today)
            diff = upsert_trades(conn, rows, today_s)
            new_trades, released = diff.new, diff.released
            log_run(conn, "kor_api", True, len(rows), time.monotonic() - t0, None)
        except KorApiError as e:
            warnings.append(f"국토부 수집 실패 — {e}")
            log_run(conn, "kor_api", False, 0, time.monotonic() - t0, str(e))

    counts = {"신규신고": len(new_trades), "계약파기": len(released)}

    listing_rows: list[dict] = []
    change_rows: list[dict] = []
    n_new = n_gone = n_chg = 0

    if targets:
        t3 = time.monotonic()
        by_area, naver_warns = collect_naver_all(targets)
        warnings.extend(naver_warns)
        yesterday = (today - timedelta(days=1)).isoformat()
        for area_name, rows in by_area.items():
            prev = listing_counts(conn, area_name, yesterday)
            d = upsert_listings(conn, rows, area_name, today_s)
            n_new += len(d.new)
            n_gone += len(d.gone)
            n_chg += len(d.price_changes)
            sections, changes = build_listing_rows(conn, area_name, rows, d, today, prev)
            listing_rows.extend(sections)
            change_rows.extend(changes)
        log_run(conn, "naver", not naver_warns, n_new + n_chg,
                time.monotonic() - t3, "; ".join(naver_warns) or None)

    counts.update({"신규매물": n_new, "소멸매물": n_gone, "가격조정": n_chg})
    counts = {k: counts.get(k, 0) for k in
              ("신규신고", "신규매물", "소멸매물", "가격조정", "계약파기")}

    if not targets:
        headline = "감시 대상이 없습니다. watchlist.yml을 채워주세요."
    elif new_trades:
        top = max(new_trades, key=lambda r: r.price)
        headline = (f"{top.dong} 신규 신고 {len(new_trades)}건, "
                    f"최고 {top.complex_name} {_money(top.price)}")
    else:
        headline = "신규 신고 없음"

    lines = [_line(conn, r, today)
             for r in sorted(new_trades, key=lambda r: r.price, reverse=True)]
    lines += [f"• :x: 파기 — {r.complex_name} {_money(r.price)} ({r.released_date})"
              for r in released]

    thermo_rows = []
    for tgt in [t for t in targets if t.region == "지방"]:
        th = thermometer(conn, tgt, today)
        thermo_rows.append({
            "name": th.name,
            "recent_counts": " · ".join(f"{ym[5:]}월 {n}건" for ym, n in th.recent_counts),
            "yoy_text": (f"{th.yoy:+d}건" if th.yoy is not None else "―"),
            "pyeong_text": (f"{th.pyeong // 10_000:,}만" if th.pyeong else "―"),
            "trend_text": (f"{th.trend_pct:+.1f}%" if th.trend_pct is not None else "―"),
            "from_high_text": (f"{th.from_high_pct:+.1f}%"
                               if th.from_high_pct is not None else "―"),
        })

    png_path = None
    t2 = time.monotonic()
    try:
        cards = [_card(conn, r, today)
                 for r in sorted(new_trades, key=lambda r: r.price, reverse=True)]
        ctx = build_context(today, headline, counts, cards, warnings,
                            listing_sections=listing_rows,
                            price_changes=change_rows,
                            thermometer=thermo_rows)
        html_path = write_html(ctx, Path("briefings"))
        png_path = html_to_png(html_path, html_path.with_suffix(".png"))
        log_run(conn, "render", True, len(cards), time.monotonic() - t2, None)
    except Exception as e:
        log.exception("렌더 실패")
        png_path = None
        warnings.append("이미지 렌더 실패 — 텍스트만 보냅니다")
        log_run(conn, "render", False, 0, time.monotonic() - t2, str(e))

    summary = Summary(headline=headline, counts=counts, lines=lines, warnings=warnings)

    if not dry_run:
        t1 = time.monotonic()
        try:
            post(env["SLACK_BOT_TOKEN"], env["SLACK_CHANNEL"],
                 build_blocks(summary, today_s), headline, png_path=png_path)
            log_run(conn, "slack", True, len(lines), time.monotonic() - t1, None)
        except Exception as e:
            log.exception("Slack 발송 실패")
            log_run(conn, "slack", False, 0, time.monotonic() - t1, str(e))

    return summary


def _configure_streams(*streams) -> None:
    """출력 스트림을 UTF-8로 고정한다.

    Windows 콘솔의 기본 코드페이지는 cp949라 em dash 같은 문자를 찍으면
    UnicodeEncodeError로 실행이 죽는다. 작업 스케줄러가 run.log로
    리다이렉트할 때도 같은 인코딩을 쓰므로 여기서 한 번 고정한다.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_streams(sys.stdout, sys.stderr)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--db", default="data/briefing.db")
    ap.add_argument("--watchlist", default="watchlist.yml")
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)

    s = run(date.fromisoformat(ns.date), Path(ns.db), Path(ns.watchlist),
            load_env(), dry_run=ns.dry_run)
    print(s.headline)
    for line in s.lines:
        print(line)
    for w in s.warnings:
        print("경고:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
