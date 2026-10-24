from collections import deque
from dataclasses import dataclass, field

import numpy as np

# Индексы ключевых точек COCO-17, которые использует YOLOv8-pose
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

PERSON = "person"


@dataclass
class Detection:
    box: np.ndarray                        # x1, y1, x2, y2 в пикселях исходного кадра
    score: float
    cls: str                               # person, helmet, head, vest, forklift, phone, fire, smoke...
    keypoints: np.ndarray | None = None    # (17, 3): x, y, уверенность
    attrs: dict = field(default_factory=dict)   # признаки по пикселям кадра (цвет пламени и т. п.)

    @property
    def width(self) -> float:
        return float(self.box[2] - self.box[0])

    @property
    def height(self) -> float:
        return float(self.box[3] - self.box[1])

    @property
    def center(self) -> tuple[float, float]:
        return float(self.box[0] + self.box[2]) / 2, float(self.box[1] + self.box[3]) / 2

    @property
    def foot(self) -> tuple[float, float]:
        """Точка контакта с полом: середина нижней грани рамки."""
        return float(self.box[0] + self.box[2]) / 2, float(self.box[3])

    def keypoint(self, idx: int, min_conf: float = 0.3) -> np.ndarray | None:
        if self.keypoints is None or self.keypoints[idx, 2] < min_conf:
            return None
        return self.keypoints[idx, :2]


@dataclass
class Track:
    id: int
    cls: str
    det: Detection
    first_seen: float
    last_seen: float
    history: deque = field(default_factory=lambda: deque(maxlen=150))   # (t, Detection), ~30 с при 5 к/с

    def window(self, now: float, seconds: float) -> list[tuple[float, Detection]]:
        return [(t, d) for t, d in self.history if now - t <= seconds]

    @property
    def age(self) -> float:
        return self.last_seen - self.first_seen
