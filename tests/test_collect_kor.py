from datetime import date
from pathlib import Path
import pytest
from src.collect_kor import (
    KorApiError, parse_money_manwon, parse_trades, recent_deal_ymds,
)

FIX = Path(__file__).parent / "fixtures"


def test_3개월_창을_만든다():
    assert recent_deal_ymds(date(2026, 9, 29)) == ["202609", "202608", "202607"]


def test_연_경계를_넘어간다():
    assert recent_deal_ymds(date(2026, 1, 15)) == ["202601", "202512", "202511"]


def test_2월_경계():
    assert recent_deal_ymds(date(2026, 3, 1)) == ["202603", "202602", "202601"]


@pytest.mark.parametrize("raw,expected", [
    ("11,200", 112_000_000),
    (" 11,200 ", 112_000_000),
    ("112000", 1_120_000_000),
    ("3,800", 38_000_000),
])
def test_만원_문자열을_원으로_바꾼다(raw, expected):
    assert parse_money_manwon(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "-", "abc"])
def test_금액이_지저분하면_ValueError(raw):
    with pytest.raises(ValueError):
        parse_money_manwon(raw)


def test_에러_본문은_거래0건이_아니라_예외다():
    xml = (FIX / "kor_error.xml").read_text(encoding="utf-8")
    with pytest.raises(KorApiError):
        parse_trades(xml, lawd_cd="11290", asset="APT")


def test_정상_응답을_TradeRow로_바꾼다():
    xml = (FIX / "kor_apt_sample.xml").read_text(encoding="utf-8")
    rows = parse_trades(xml, lawd_cd="11290", asset="APT")
    assert rows, "샘플에 거래가 1건 이상 있어야 한다"
    r = rows[0]
    assert r.lawd_cd == "11290"
    assert r.asset == "APT"
    assert r.price > 0
    assert r.area > 0
    assert len(r.deal_date) == 10 and r.deal_date[4] == "-"
    assert isinstance(r.released, bool)


def test_해제와_등기일자를_읽는다():
    xml = (FIX / "kor_apt_sample.xml").read_text(encoding="utf-8")
    r = parse_trades(xml, lawd_cd="11290", asset="APT")[1]
    assert r.released is True
    assert r.released_date == "2026-09-25"
    assert r.register_date == "2026-09-22"
    assert r.deal_kind == "직거래"


def test_금액이_깨진_항목은_건너뛰고_나머지는_살린다():
    xml = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><거래금액> </거래금액><전용면적>84.93</전용면적><층>15</층>
        <년>2026</년><월>9</월><일>14</일><아파트>깨진건</아파트><법정동>장위동</법정동></item>
      <item><거래금액>11,200</거래금액><전용면적>84.93</전용면적><층>15</층>
        <년>2026</년><월>9</월><일>14</일><아파트>래미안장위</아파트><법정동>장위동</법정동></item>
    </items></body></response>"""
    rows = parse_trades(xml, lawd_cd="11290", asset="APT")
    assert len(rows) == 1
    assert rows[0].complex_name == "래미안장위"


def test_로그_메시지에서_인증키를_가린다():
    from src.collect_kor import _redact

    msg = ("401 Client Error: Unauthorized for url: "
           "https://apis.data.go.kr/1613000/X/getX?serviceKey=SECRET%2Fkey%3D%3D"
           "&LAWD_CD=11290&DEAL_YMD=202609")
    out = _redact(msg)
    assert "SECRET" not in out
    assert "serviceKey=***" in out
    assert "LAWD_CD=11290" in out


def test_인증키가_없는_메시지는_그대로_둔다():
    from src.collect_kor import _redact

    assert _redact("타임아웃") == "타임아웃"
