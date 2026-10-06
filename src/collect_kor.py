from datetime import date
from xml.etree import ElementTree as ET
import logging
import re
import time

import requests

from src.models import Target, TradeRow

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_PAGES = 20

# 태그명은 docs/api-notes.md 기준. 실호출로 확인 후 다르면 _text() 인자만 교체한다.
ENDPOINTS = {
    "APT": ("https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev"
            "/getRTMSDataSvcAptTradeDev", "아파트"),
    "VL":  ("https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade"
            "/getRTMSDataSvcRHTrade", "연립다세대"),
}


_KEY_RE = re.compile(r"(serviceKey=)[^&\s]*", re.IGNORECASE)


class KorApiError(Exception):
    pass


def _redact(msg: str) -> str:
    """로그·예외 메시지에서 인증키를 가린다.

    requests 는 파라미터를 URL에 실어 보내고 raise_for_status() 메시지에
    그 URL을 통째로 넣는다. 그대로 로깅하면 작업 스케줄러가 남기는
    run.log 에 인증키가 평문으로 쌓인다.
    """
    return _KEY_RE.sub(r"\1***", str(msg))


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
    if code is not None:
        # 서비스에 따라 성공 코드가 "0" / "00" / "000" 로 제각각이다. 숫자로 본다.
        try:
            failed = int(code) != 0
        except ValueError:
            failed = True
        if failed:
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


def _total_count(xml_text: str) -> int | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    raw = _text(root, ".//totalCount")
    return int(raw) if raw and raw.isdigit() else None


def _fetch_page(api_key, lawd_cd, deal_ymd, asset, page, sess) -> str:
    """한 페이지를 3회까지 재시도해 본문을 돌려준다."""
    url = ENDPOINTS[asset][0]
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = sess.get(url, params={
                "serviceKey": api_key, "LAWD_CD": lawd_cd,
                "DEAL_YMD": deal_ymd, "numOfRows": PAGE_SIZE, "pageNo": page,
            }, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = e
            log.warning("국토부 호출 실패 (%s/3 %s %s p%s): %s",
                        attempt + 1, lawd_cd, deal_ymd, page, _redact(e))
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise KorApiError(
        f"{lawd_cd} {deal_ymd} {asset} p{page} 3회 실패: {_redact(last)}") from last


def fetch_trades(api_key: str, lawd_cd: str, deal_ymd: str, asset: str,
                 session: requests.Session | None = None) -> list[TradeRow]:
    """한 시군구·한 달치를 전부 가져온다 (페이지 끝까지).

    1페이지만 받으면 거래가 많은 달이 조용히 잘리고, 잘린 건이 나중에 순서가
    바뀌며 올라와 몇 주 뒤 "오늘의 신규"로 둔갑한다.
    """
    sess = session or requests.Session()
    rows: list[TradeRow] = []
    page = 1
    while page <= MAX_PAGES:
        body = _fetch_page(api_key, lawd_cd, deal_ymd, asset, page, sess)
        rows.extend(parse_trades(body, lawd_cd, asset))
        total = _total_count(body)
        if total is None or page * PAGE_SIZE >= total:
            break
        page += 1
    else:
        log.warning("%s %s %s: %s페이지 상한에 걸렸습니다", lawd_cd, deal_ymd, asset, MAX_PAGES)
    return rows


def filter_by_watchlist(rows: list[TradeRow], targets: list[Target]) -> list[TradeRow]:
    """관심 대상에 걸리는 거래만 남긴다.

    국토부는 시군구 통째로만 주기 때문에 성북구를 부르면 성북구 전체가 온다.
    법정동(그리고 단지명이 지정됐으면 단지명)으로 좁히지 않으면 브리핑이
    관심 없는 수백 건으로 덮인다.
    """
    kept: list[TradeRow] = []
    for row in rows:
        for t in targets:
            if t.lawd_cd != row.lawd_cd:
                continue
            if t.dongs and row.dong not in t.dongs:
                continue
            if t.complex_name and t.complex_name not in row.complex_name:
                continue
            kept.append(row)
            break
    return kept


def collect_all(api_key: str, targets: list[Target],
                today: date) -> tuple[list[TradeRow], list[str]]:
    """관심 대상의 최근 3개월 거래를 모은다.

    호출 하나가 실패해도 나머지 성공분은 살린다. 12번 중 마지막 한 번 때문에
    하루치를 통째로 버리면 브리핑이 사라진다.
    """
    ymds = recent_deal_ymds(today)
    lawd_cds = sorted({t.lawd_cd for t in targets})
    sess = requests.Session()
    rows: list[TradeRow] = []
    warnings: list[str] = []

    for lawd_cd in lawd_cds:
        for asset in ("APT", "VL"):
            for ymd in ymds:
                try:
                    rows.extend(fetch_trades(api_key, lawd_cd, ymd, asset, sess))
                except KorApiError as e:
                    warnings.append(f"국토부 수집 일부 실패 — {lawd_cd} {ymd} {asset}")
                    log.warning("%s", _redact(e))

    return filter_by_watchlist(rows, targets), warnings
