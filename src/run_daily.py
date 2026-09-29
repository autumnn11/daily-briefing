from datetime import date
from pathlib import Path
import argparse
import logging
import sys
import time

from src.collect_kor import KorApiError, collect_all
from src.config import load_env, load_watchlist
from src.notify import Summary, build_blocks, post
from src.store import init_db, log_run, upsert_trades

log = logging.getLogger(__name__)


def _money(won: int) -> str:
    eok, rest = divmod(won, 100_000_000)
    man = rest // 10_000
    if eok and man:
        return f"{eok}억 {man:,}"
    if eok:
        return f"{eok}억"
    return f"{man:,}만"


def _line(conn, r, today) -> str:
    return (f"• {r.complex_name} 전용 {r.area:.1f} {r.floor}층 — "
            f"{_money(r.price)} (계약 {r.deal_date})")


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

    summary = Summary(headline=headline, counts=counts, lines=lines, warnings=warnings)

    if not dry_run:
        t1 = time.monotonic()
        try:
            post(env["SLACK_BOT_TOKEN"], env["SLACK_CHANNEL"],
                 build_blocks(summary, today_s), headline)
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
