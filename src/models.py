from dataclasses import dataclass


@dataclass(frozen=True)
class Coord:
    lat: float
    lon: float
    zoom: int


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    lawd_cd: str
    dongs: list[str]
    complex_name: str | None
    coord: Coord | None
    asset: str | None
    region: str


@dataclass(frozen=True)
class TradeRow:
    lawd_cd: str
    dong: str
    complex_name: str
    deal_date: str            # YYYY-MM-DD
    area: float               # 전용면적 m2
    floor: int | None
    price: int                # 원
    deal_kind: str            # 중개거래 / 직거래 / ""
    released: bool            # 해제 여부
    released_date: str | None
    register_date: str | None  # 등기일자
    asset: str                # APT / VL
