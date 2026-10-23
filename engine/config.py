"""Чтение конфигурации камер из YAML: зоны, правила, расписания, калибровка пола."""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .rules import RULES
from .scene import FloorMap, RuleConfig, Schedule, Scene, Zone

SEVERITY_DEFAULT = {
    "fall": "critical", "immobile": "critical", "vehicle_proximity": "critical", "open_fire": "critical",
    "smoke": "high", "zone_intrusion": "high", "no_helmet": "high", "smoking": "high",
    "no_vest": "medium", "running": "medium", "phone": "medium",
}
CONFIRM_DEFAULT = {
    "fall": 1.0, "immobile": 0.0, "vehicle_proximity": 1.0, "open_fire": 2.0, "smoke": 3.0,
    "zone_intrusion": 2.0, "no_helmet": 3.0, "no_vest": 3.0, "running": 1.0, "phone": 3.0, "smoking": 2.0,
}


@dataclass
class CameraConfig:
    id: str
    title: str
    source: str
    analysis_fps: float
    scene: Scene
    loop: bool = True
    raw: dict = field(default_factory=dict)


def _rule(entry: dict) -> RuleConfig:
    entry = dict(entry)
    rtype = entry.pop("type")
    if rtype not in RULES:
        raise ValueError(f"неизвестное правило {rtype!r}; доступны: {', '.join(sorted(RULES))}")
    schedule = entry.pop("schedule", None)
    return RuleConfig(
        type=rtype,
        confirm_s=float(entry.pop("confirm_s", CONFIRM_DEFAULT.get(rtype, 2.0))),
        severity=entry.pop("severity", SEVERITY_DEFAULT.get(rtype, "high")),
        schedule=Schedule.parse(schedule) if schedule else None,
        params=entry,
    )


def parse_camera(data: dict) -> CameraConfig:
    zones = []
    if data.get("rules"):
        zones.append(Zone(id="frame", title="Весь кадр", polygon=None, rules=[_rule(r) for r in data["rules"]]))
    for z in data.get("zones", []):
        zones.append(Zone(
            id=z["id"], title=z.get("title", z["id"]),
            polygon=np.asarray(z["polygon"], np.float32) if z.get("polygon") else None,
            rules=[_rule(r) for r in z.get("rules", [])],
            exclude=set(z.get("exclude", [])),
        ))
    floor = data.get("floor")
    scene = Scene(camera_id=data["camera"], zones=zones,
                  floor=FloorMap(floor["image_pts"], floor["world_m"]) if floor else None)
    return CameraConfig(id=data["camera"], title=data.get("title", data["camera"]), source=str(data["source"]),
                        analysis_fps=float(data.get("analysis_fps", 5)), scene=scene,
                        loop=bool(data.get("loop", True)), raw=data)


def load_cameras(directory: str | Path) -> list[CameraConfig]:
    return [parse_camera(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.yaml"))]
