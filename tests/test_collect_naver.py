from pathlib import Path
import pandas as pd
import pytest
from src.collect_naver import PII_COLUMNS, newest_xlsx, read_xlsx

FIX = Path(__file__).parent / "fixtures"


def test_매물을_읽는다():
    rows = read_xlsx(FIX / "naver_sample.xlsx", area_name="장위 재개발 일대")
    assert len(rows) == 2
    r = rows[0]
    assert r.listing_no == "2444012345"
    assert r.deal_type == "매매"
    assert r.price == 380_000_000
    assert r.area_name == "장위 재개발 일대"
    assert r.listed_date == "2026-09-01"


def test_전세_매물도_버리지_않는다():
    rows = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")
    assert {r.deal_type for r in rows} == {"매매", "전세"}


def test_개인정보_컬럼은_ListingRow에_없다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[0]
    for col in PII_COLUMNS:
        assert not hasattr(r, col)
    blob = repr(r)
    assert "홍길동" not in blob
    assert "010-1234-5678" not in blob
    assert "장위부동산" not in blob


def test_갭_지표는_보존된다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[0]
    assert r.gap == 40_000_000
    assert r.gap_ratio == pytest.approx(0.1053)


def test_갭_지표가_비어있어도_None으로_읽는다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[1]
    assert r.gap is None
    assert r.prev_jeonse is None


def test_xlsx가_없으면_None을_돌려준다(tmp_path):
    assert newest_xlsx(tmp_path) is None


def test_가장_최근_xlsx를_고른다(tmp_path):
    import time
    for name in ("매물정보_확장_20260929_080000.xlsx", "매물정보_확장_20260929_090000.xlsx"):
        pd.DataFrame([{"a": 1}]).to_excel(tmp_path / name, index=False, engine="openpyxl")
        time.sleep(0.02)
    assert newest_xlsx(tmp_path).name == "매물정보_확장_20260929_090000.xlsx"


def test_매물번호나_가격이_없는_행은_버린다(tmp_path):
    p = tmp_path / "bad.xlsx"
    pd.DataFrame([
        {"매물번호": "", "매매 금액(원)": 1000, "매물명": "번호없음", "거래유형": "매매"},
        {"매물번호": "1", "매매 금액(원)": None, "매물명": "가격없음", "거래유형": "매매"},
        {"매물번호": "2", "매매 금액(원)": 500, "매물명": "정상", "거래유형": "매매"},
    ]).to_excel(p, index=False, engine="openpyxl")
    rows = read_xlsx(p, area_name="x")
    assert [r.name for r in rows] == ["정상"]
