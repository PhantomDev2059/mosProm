"""Лица: детектор YuNet + распознаватель SFace (OpenCV, модели из opencv_zoo).

Из рамки человека берём верхнюю часть, ищем там лицо, выравниваем и считаем
128-мерный дескриптор. Сравнение дескрипторов — косинусная близость.
"""
import threading
from pathlib import Path

import cv2
import numpy as np

DET_FILE = "face_detection_yunet_2023mar.onnx"
REC_FILE = "face_recognition_sface_2021dec.onnx"
MATCH_THRESHOLD = 0.363          # рекомендованный порог SFace для косинусной близости
MIN_FACE_PX = 24                 # лица мельче не распознаются надёжно
# нового сотрудника заводим только по хорошему лицу; плохие лица могут лишь совпасть с известным.
# Подобрано на 7 демо-камерах: без этих порогов мелкие/смазанные/тёмные лица давали
# дубли и «мусорные» профили (24 профиля на 12 человек → 13, мусорных 3 → 0)
NEW_MIN_PX = 32
NEW_MIN_SCORE = 0.85
NEW_MIN_SHARP = 12.0             # дисперсия лапласиана выровненного лица 112×112
HEAD_PART = 0.45                 # доля рамки человека сверху, где ищем лицо
DET_SIDE = (160, 640)            # кроп для детектора масштабируем в этот диапазон по большей стороне


def models_path(models_dir: Path) -> Path | None:
    """Каталог, где лежат обе модели: models_dir/faces или сам models_dir."""
    for d in (Path(models_dir) / "faces", Path(models_dir)):
        if (d / DET_FILE).is_file() and (d / REC_FILE).is_file():
            return d
    return None


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Косинусная близость двух дескрипторов."""
    a, b = np.asarray(a, np.float32).ravel(), np.asarray(b, np.float32).ravel()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class FaceEngine:
    def __init__(self, models_dir: Path, score_threshold: float = 0.8):
        d = models_path(models_dir)
        if d is None:
            raise FileNotFoundError(f"нет моделей лиц ({DET_FILE}, {REC_FILE}) в {models_dir}")
        self.dir = d
        self.score_threshold = score_threshold
        self.lock = threading.Lock()                     # детектор и распознаватель не потокобезопасны
        self._det = None
        self._rec = None

    @staticmethod
    def available(models_dir: Path) -> bool:
        return models_path(models_dir) is not None

    def _models(self):
        if self._det is None:
            self._det = cv2.FaceDetectorYN.create(str(self.dir / DET_FILE), "", (320, 320),
                                                  self.score_threshold, 0.3, 50)
            self._rec = cv2.FaceRecognizerSF.create(str(self.dir / REC_FILE), "")
        return self._det, self._rec

    def embed(self, frame_bgr: np.ndarray, person_box) -> tuple[np.ndarray, np.ndarray, float] | None:
        """Дескриптор лица человека в рамке [x1, y1, x2, y2].

        Возвращает (нормированный дескриптор 128 float32, кроп лица BGR для фото профиля,
        качество 0..1) или None, если лица не видно.
        """
        r = self.analyze(frame_bgr, person_box)
        return None if r is None else (r["emb"], r["photo"], r["quality"])

    def analyze(self, frame_bgr: np.ndarray, person_box) -> dict | None:
        """То же, что embed(), но с подробностями: emb, photo, quality, size, score, sharp, frontal,
        good — годится ли лицо, чтобы завести по нему нового сотрудника."""
        H, W = frame_bgr.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in np.asarray(person_box, np.float32).ravel()[:4])
        bw, bh = x2 - x1, y2 - y1
        if bw < MIN_FACE_PX or bh < MIN_FACE_PX:
            return None
        # верх рамки с небольшим запасом: голова часто выходит за рамку детектора
        cx1, cx2 = int(max(0, x1 - 0.1 * bw)), int(min(W, x2 + 0.1 * bw))
        cy1, cy2 = int(max(0, y1 - 0.05 * bh)), int(min(H, y1 + HEAD_PART * bh))
        if cx2 - cx1 < MIN_FACE_PX or cy2 - cy1 < MIN_FACE_PX:
            return None
        crop = frame_bgr[cy1:cy2, cx1:cx2]
        side = max(crop.shape[:2])
        scale = min(max(1.0, DET_SIDE[0] / side), DET_SIDE[1] / side, 3.0)
        img = crop if scale == 1.0 else cv2.resize(crop, None, fx=scale, fy=scale,
                                                   interpolation=cv2.INTER_LINEAR if scale > 1 else cv2.INTER_AREA)
        img = np.ascontiguousarray(img)
        with self.lock:
            det, rec = self._models()
            det.setInputSize((img.shape[1], img.shape[0]))
            _, faces = det.detect(img)
            if faces is None or len(faces) == 0:
                return None
            # лучшее лицо: крупнее и увереннее
            sizes = np.minimum(faces[:, 2], faces[:, 3]) / scale
            best = int(np.argmax(sizes * faces[:, 14]))
            face, size, score = faces[best], float(sizes[best]), float(faces[best, 14])
            if size < MIN_FACE_PX:
                return None
            aligned = rec.alignCrop(img, face)
            feat = rec.feature(aligned).ravel().astype(np.float32)
        feat /= np.linalg.norm(feat) + 1e-9
        # фото профиля: квадрат вокруг лица с полями, в исходном разрешении
        fx, fy, fw, fh = (face[:4] / scale).tolist()
        m = 0.35 * max(fw, fh)
        s = max(fw, fh) + 2 * m
        px, py = cx1 + fx + fw / 2 - s / 2, cy1 + fy + fh / 2 - s / 2
        px1, py1 = int(max(0, px)), int(max(0, py))
        px2, py2 = int(min(W, px + s)), int(min(H, py + s))
        photo = frame_bgr[py1:py2, px1:px2].copy()
        sharp = float(cv2.Laplacian(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())
        # анфас: нос посередине между глазами (точки YuNet: правый глаз, левый глаз, нос, …)
        dr, dl = abs(face[8] - face[4]), abs(face[6] - face[8])
        frontal = float(min(dr, dl) / max(dr, dl, 1e-6))
        # качество для выбора фото профиля: уверенность × размер × резкость × анфас
        quality = score * min(1.0, size / 80.0) * min(1.0, 0.5 + sharp / 300.0) * (0.4 + 0.6 * frontal)
        good = size >= NEW_MIN_PX and score >= NEW_MIN_SCORE and sharp >= NEW_MIN_SHARP
        return dict(emb=feat, photo=photo, quality=float(quality), size=size, score=score, sharp=sharp,
                    frontal=frontal, good=bool(good))

    similarity = staticmethod(similarity)
