import numpy as np


def iou(a: np.ndarray, b: np.ndarray) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def ioa(inner: np.ndarray, outer: np.ndarray) -> float:
    """Доля площади inner, лежащая внутри outer."""
    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return float(inter / area) if area > 0 else 0.0


def point_in_polygon(pt: tuple[float, float], poly: np.ndarray) -> bool:
    """Луч вправо из точки; poly — (N, 2)."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Гомография 3×3 по четырём (или больше) парам точек, метод DLT."""
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.asarray(rows, dtype=np.float64))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def apply_homography(h: np.ndarray, pt: tuple[float, float]) -> np.ndarray:
    p = h @ np.array([pt[0], pt[1], 1.0])
    return p[:2] / p[2]
