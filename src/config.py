from pathlib import Path
import os

import yaml

from src.models import Coord, Target


def _target(raw: dict, region: str) -> Target:
    lawd_cd = str(raw.get("lawd_cd", "")).strip()
    if len(lawd_cd) != 5 or not lawd_cd.isdigit():
        raise ValueError(f"lawd_cd는 숫자 5자리여야 합니다: {raw.get('이름')} -> {lawd_cd!r}")
    c = raw.get("좌표")
    coord = Coord(float(c["lat"]), float(c["lon"]), int(c["zoom"])) if c else None
    return Target(
        name=str(raw.get("이름", "")).strip(),
        kind=str(raw.get("유형", "단지")).strip(),
        lawd_cd=lawd_cd,
        dongs=[str(d).strip() for d in raw.get("법정동", [])],
        complex_name=(str(raw["단지명"]).strip() if raw.get("단지명") else None),
        coord=coord,
        asset=(str(raw["자산"]).strip() if raw.get("자산") else None),
        region=region,
    )


def load_watchlist(path: Path) -> list[Target]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    targets: list[Target] = []
    for region in ("수도권", "지방"):
        for raw in data.get(region) or []:
            targets.append(_target(raw, region))
    return targets


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for key in ("KOR_API_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env
