from datetime import date
from xml.etree import ElementTree as ET
import logging
import time

import requests

from src.models import Target, TradeRow

log = logging.getLogger(__name__)

# 태그명은 docs/api-notes.md 기준. 실호출로 확인 후 다르면 _text() 인자만 교체한다.
ENDPOINTS = {
    "APT": ("https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev"
            "/getRTMSDataSvcAptTradeDev", "아파트"),
    "VL":  ("https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade"
            "/getRTMSDataSvcRHTrade", "연립다세대"),
}


class KorApiError(Exception):
    pass


def recent_deal_ymds(today: date, months: int = 3) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(months):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def parse_money_manwon(s: str | None) -> int:
    if s is None:
        raise ValueError("거래금액이 없습니다")
    cleaned = str(s).replace(",", "").replace("　", "").strip()
    if not cleaned.isdigit():
        raise ValueError(f"거래금액을 읽을 수 없습니다: {s!r}")
    return int(cleaned) * 10_000


def _text(item: ET.Element, *names: str) -> str | None:
    for n in names:
        el = item.find(n)
        if el is not None and el.text is not None and el.text.strip():
            return el.text.strip()
    return None


def _norm_date(s: str | None) -> str | None:
    """'26.09.28' 또는 '20260928' 형태를 YYYY-MM-DD로."""
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    if len(digits) == 6:
        return f"20{digits[:2]}-{digits[2:4]}-{digits[4:]}"
    return None


def parse_trades(xml_text: str, lawd_cd: str, asset: str) -> list[TradeRow]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise KorApiError(f"XML 파싱 실패: {e}") from e

    if root.find(".//cmmMsgHeader") is not None:
        msg = _text(root, ".//returnAuthMsg") or "알 수 없는 인증 오류"
        raise KorApiError(f"국토부 API 오류: {msg}")

    code = _text(root, ".//resultCode")
    if code is not None and code not in ("00", "0"):
        msg = _text(root, ".//resultMsg") or ""
        raise KorApiError(f"국토부 API 오류 {code}: {msg}")

    name_tag = ENDPOINTS[asset][1]
    rows: list[TradeRow] = []
    for item in root.findall(".//item"):
        try:
            price = parse_money_manwon(_text(item, "거래금액"))
            y = int(_text(item, "년"))
            m = int(_text(item, "월"))
            d = int(_text(item, "일"))
            area = float(_text(item, "전용면적"))
        except (ValueError, TypeError) as e:
            log.warning("항목 건너뜀 (%s): %s", e,
                        ET.tostring(item, encoding="unicode")[:200])
            continue

        floor_raw = _text(item, "층")
        rows.append(TradeRow(
            lawd_cd=lawd_cd,
            dong=_text(item, "법정동") or "",
            complex_name=_text(item, name_tag, "아파트", "연립다세대") or "",
            deal_date=f"{y:04d}-{m:02d}-{d:02d}",
            area=area,
            floor=int(floor_raw) if floor_raw and floor_raw.lstrip("-").isdigit() else None,
            price=price,
            deal_kind=_text(item, "거래유형") or "",
            released=bool((_text(item, "해제여부") or "").upper() == "O"),
            released_date=_norm_date(_text(item, "해제사유발생일")),
            register_date=_norm_date(_text(item, "등기일자")),
            asset=asset,
        ))
    return rows


def fetch_trades(api_key: str, lawd_cd: str, deal_ymd: str, asset: str,
                 session: requests.Session | None = None) -> list[TradeRow]:
    url = ENDPOINTS[asset][0]
    sess = session or requests.Session()
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = sess.get(url, params={
                "serviceKey": api_key, "LAWD_CD": lawd_cd,
                "DEAL_YMD": deal_ymd, "numOfRows": 1000, "pageNo": 1,
            }, timeout=30)
            r.raise_for_status()
            return parse_trades(r.text, lawd_cd, asset)
        except (requests.RequestException, KorApiError) as e:
            last = e
            log.warning("국토부 호출 실패 (%s/3 %s %s): %s", attempt + 1, lawd_cd, deal_ymd, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise KorApiError(f"{lawd_cd} {deal_ymd} {asset} 3회 실패") from last


def collect_all(api_key: str, targets: list[Target], today: date) -> list[TradeRow]:
    ymds = recent_deal_ymds(today)
    lawd_cds = sorted({t.lawd_cd for t in targets})
    sess = requests.Session()
    rows: list[TradeRow] = []
    for lawd_cd in lawd_cds:
        for asset in ("APT", "VL"):
            for ymd in ymds:
                rows.extend(fetch_trades(api_key, lawd_cd, ymd, asset, sess))
    return rows
