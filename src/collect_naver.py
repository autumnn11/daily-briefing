from pathlib import Path
import logging
import os
import random
import sys
import tempfile
import time

import pandas as pd

from src.models import ListingRow, Target

log = logging.getLogger(__name__)

# 중개사 개인 연락처. DB 적재 전에 버린다 (스펙 10절).
PII_COLUMNS = ("부동산상호", "중개사이름", "전화1", "전화2")

SCRAPER_DIR = Path(__file__).resolve().parent.parent / "vendor" / "anti_bot_scraper"

# 스크레이퍼 기본값은 갭투자 후보만 남기고 워커가 12개다. 매일 도는 자동화에 맞춰 덮어쓴다.
OVERRIDES = {
    "ONLY_PREV_GT_SALE": False,
    "ONLY_WITH_PREV_JEONSE": False,
    "DETAIL_WORKERS": 4,
    "MAX_COMPLEX_DETAIL": 100,
    "MAX_ARTICLE_DETAIL": 1500,
}


class NaverBlocked(Exception):
    pass


def _int_or_none(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float_or_none(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_or_none(v) -> str | None:
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def _str(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def read_xlsx(path: Path, area_name: str) -> list[ListingRow]:
    df = pd.read_excel(path, engine="openpyxl")
    df = df.drop(columns=[c for c in PII_COLUMNS if c in df.columns])

    rows: list[ListingRow] = []
    for rec in df.to_dict("records"):
        no = _str(rec.get("매물번호"))
        price = _int_or_none(rec.get("매매 금액(원)"))
        if not no or price is None:
            continue
        rows.append(ListingRow(
            listing_no=no,
            name=_str(rec.get("매물명")),
            deal_type=_str(rec.get("거래유형")),
            price=price,
            floor=_str(rec.get("층수")),
            area=_float_or_none(rec.get("면적(㎡)")) or 0.0,
            exclusive_area=_float_or_none(rec.get("전용면적")) or 0.0,
            direction=_str(rec.get("방향")),
            features=_str(rec.get("특징")),
            listed_date=_date_or_none(rec.get("등록일")),
            prev_jeonse=_int_or_none(rec.get("기전세금(원)")),
            gap=_int_or_none(rec.get("갭금액(원)")),
            gap_ratio=_float_or_none(rec.get("갭비율")),
            area_name=area_name,
        ))
    return rows


def newest_xlsx(dirpath: Path) -> Path | None:
    files = sorted(Path(dirpath).glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def scrape_target(target: Target) -> list[ListingRow]:
    """vendor 스크레이퍼를 한 구역에 대해 돌리고 매물을 읽어온다.

    스크레이퍼의 main() 은 input() 으로 좌표를 물어 스케줄러에서 못 쓴다.
    모듈 상수를 덮어쓴 뒤 crawl_detailed() 를 직접 부르고, 임시 디렉터리에
    떨어진 xlsx 를 읽고 지운다.
    """
    if target.coord is None:
        return []

    if str(SCRAPER_DIR) not in sys.path:
        sys.path.insert(0, str(SCRAPER_DIR))
    import asyncio

    import scraper_kr

    for key, value in OVERRIDES.items():
        setattr(scraper_kr, key, value)
    if target.asset:
        scraper_kr.ASSET_TYPES = target.asset

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            asyncio.run(scraper_kr.crawl_detailed(
                target.coord.lat, target.coord.lon, target.coord.zoom))
            found = newest_xlsx(Path(tmp))
            if found is None:
                log.warning("%s: 스크레이퍼가 xlsx를 만들지 않았습니다 (수집 0건)",
                            target.name)
                return []
            rows = read_xlsx(found, target.name)
            found.unlink(missing_ok=True)
            return rows
        finally:
            os.chdir(cwd)


def collect_all(targets: list[Target]) -> tuple[dict[str, list[ListingRow]], list[str]]:
    result: dict[str, list[ListingRow]] = {}
    warnings: list[str] = []
    seoul = [t for t in targets if t.region == "수도권" and t.coord is not None]

    for i, target in enumerate(seoul):
        if i:
            time.sleep(random.uniform(5, 15))
        try:
            result[target.name] = scrape_target(target)
        except NaverBlocked as e:
            warnings.append(f"호가 미수집 — 네이버 차단 ({target.name})")
            log.warning("차단 감지, 재시도하지 않고 중단: %s", e)
            break
        except Exception as e:
            warnings.append(f"호가 미수집 — {target.name} ({type(e).__name__})")
            log.exception("네이버 수집 실패: %s", target.name)
    return result, warnings
