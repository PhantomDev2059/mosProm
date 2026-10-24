from pathlib import Path

import numpy as np
import pytest

from api.people import PeopleIndex
from engine.faces import MATCH_THRESHOLD, FaceEngine, similarity

REAL_MODELS = Path("/workspace/linq/models/"
                   "faces")
BOX = [10, 10, 70, 190]


class FakeEngine:
    """Дескриптор задаётся «номером лица» в пикселе [0, 0] кадра; 0 — лица нет."""

    def __init__(self, faces: dict[int, np.ndarray]):
        self.faces = faces

    def embed(self, frame, box):
        k = int(frame[0, 0, 0])
        if k not in self.faces:
            return None
        v = self.faces[k].astype(np.float32)
        return v / np.linalg.norm(v), np.full((40, 40, 3), 30 * k, np.uint8), float(k) / 10


def unit(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def near(v, cos, seed=99):
    """Вектор с заданной косинусной близостью к v."""
    r = unit(seed)
    r -= (r @ v) * v
    r /= np.linalg.norm(r)
    return cos * v + np.sqrt(1 - cos ** 2) * r


def frame(k):
    f = np.zeros((200, 100, 3), np.uint8)
    f[0, 0, 0] = k
    return f


def make(tmp_path, faces, **kw):
    return PeopleIndex(tmp_path / "people.db", tmp_path / "media", tmp_path / "nomodels",
                       engine=FakeEngine(faces), **kw)


def feed(idx, items):
    for eid, k in items:
        idx.submit(eid, frame(k), BOX)
        assert idx.wait_idle(5)


def test_clustering_and_numbering(tmp_path):
    a, b = unit(1), unit(2)
    idx = make(tmp_path, {1: a, 2: near(a, 0.7), 3: b, 4: near(b, 0.6, seed=5)})
    feed(idx, [("e1", 1), ("e2", 2), ("e3", 3), ("e4", 4), ("e5", 0)])
    people = idx.people()
    assert [p["name"] for p in people] == ["Сотрудник №1", "Сотрудник №2"]
    assert [p["n"] for p in people] == [2, 2]
    p1, p2 = people[0]["id"], people[1]["id"]
    assert idx.person_of("e1")["id"] == idx.person_of("e2")["id"] == p1
    assert idx.person_of("e4") == {"id": p2, "name": "Сотрудник №2", "photo": f"/media/face-{p2}.jpg"}
    assert idx.person_of("e5") is None                      # лица не было — ничего не записали
    assert sorted(idx.events_of(p1)) == ["e1", "e2"]
    assert (tmp_path / "media" / f"face-{p1}.jpg").is_file()
    assert idx.get(p1)["quality"] == pytest.approx(0.2)    # фото заменили на более качественное


def test_threshold(tmp_path):
    a = unit(3)
    idx = make(tmp_path, {1: a, 2: near(a, MATCH_THRESHOLD + 0.02), 3: near(a, MATCH_THRESHOLD - 0.1, seed=7)})
    feed(idx, [("e1", 1), ("e2", 2), ("e3", 3)])
    assert idx.person_of("e2")["id"] == idx.person_of("e1")["id"]
    assert idx.person_of("e3")["id"] != idx.person_of("e1")["id"]
    assert len(idx.people()) == 2


def test_rename_and_merge(tmp_path):
    a, b = unit(10), unit(11)
    idx = make(tmp_path, {1: a, 2: b})
    feed(idx, [("e1", 1), ("e2", 2), ("e3", 2)])
    p1, p2 = (p["id"] for p in idx.people())
    assert idx.rename(p1, "  Иванов   И. ")
    assert idx.get(p1)["name"] == "Иванов И."
    assert not idx.rename("нет-такого", "x")
    assert idx.merge(p2, p1)
    assert not idx.merge(p1, p1)
    assert [p["id"] for p in idx.people()] == [p1]
    assert idx.get(p2)["id"] == p1                         # старый id (например, привязанный в Telegram) ведёт к p1
    assert idx.get(p1)["n"] == 3
    assert sorted(idx.events_of(p1)) == ["e1", "e2", "e3"]
    assert idx.person_of("e3")["id"] == p1
    assert (tmp_path / "media" / f"face-{p1}.jpg").is_file()
    assert not (tmp_path / "media" / f"face-{p2}.jpg").exists()
    # средний дескриптор теперь ближе к b (2 лица) — новое лицо b попадает к p1
    feed(idx, [("e4", 2)])
    assert idx.person_of("e4")["id"] == p1
    # номера после слияния не повторяются
    idx.engine.faces[5] = unit(12)
    feed(idx, [("e5", 5)])
    assert sorted(p["name"] for p in idx.people()) == ["Иванов И.", "Сотрудник №2"]


class GatedEngine:
    """analyze(): дескриптор и признак «хорошее лицо» задаются по номеру в пикселе [0, 0]."""

    def __init__(self, faces: dict[int, tuple[np.ndarray, bool, float]]):
        self.faces = faces

    def analyze(self, frame, box):
        k = int(frame[0, 0, 0])
        if k not in self.faces:
            return None
        v, good, q = self.faces[k]
        return {"emb": v / np.linalg.norm(v), "photo": np.full((40, 40, 3), 30 * k, np.uint8), "quality": q, "good": good}


def gated(tmp_path, faces):
    return PeopleIndex(tmp_path / "people.db", tmp_path / "media", tmp_path / "nomodels", engine=GatedEngine(faces))


def test_bad_face_matches_but_never_creates(tmp_path):
    a, b = unit(50), unit(51)
    idx = gated(tmp_path, {1: (b, False, 0.1), 2: (a, True, 0.6), 3: (near(a, 0.6), False, 0.1)})
    feed(idx, [("e1", 1)])
    assert idx.people() == [] and idx.person_of("e1") is None        # мелкое лицо незнакомца — без профиля
    feed(idx, [("e2", 2), ("e3", 3)])
    assert len(idx.people()) == 1
    assert idx.person_of("e3")["id"] == idx.person_of("e2")["id"]   # но с известным совпадает
    assert idx.get(idx.people()[0]["id"])["quality"] == pytest.approx(0.6)   # плохое фото не заменило хорошее


def test_borderline_match_does_not_replace_photo(tmp_path):
    a = unit(55)
    idx = gated(tmp_path, {1: (a, True, 0.3), 2: (near(a, 0.4), True, 0.9), 3: (near(a, 0.8), True, 0.8)})
    feed(idx, [("e1", 1), ("e2", 2)])
    pid = idx.person_of("e1")["id"]
    assert idx.person_of("e2")["id"] == pid and idx.get(pid)["quality"] == pytest.approx(0.3)
    feed(idx, [("e3", 3)])                                          # уверенное совпадение — фото меняем
    assert idx.get(pid)["quality"] == pytest.approx(0.8)


def test_repeated_submit_per_event(tmp_path):
    a, b = unit(60), unit(61)
    idx = gated(tmp_path, {1: (a, True, 0.3), 2: (near(a, 0.8), True, 0.7), 3: (b, True, 0.9)})
    feed(idx, [("cam-1-x", 0)])                                     # на открытии лица не видно
    assert idx.person_of("cam-1-x") is None
    feed(idx, [("cam-1-x", 1)])                                     # позже — появилось
    pid = idx.person_of("cam-1-x")["id"]
    feed(idx, [("cam-1-x", 2), ("cam-1-x", 3)])                     # тот же человек лучше; чужое лицо — мимо
    assert idx.person_of("cam-1-x")["id"] == pid
    assert len(idx.people()) == 1 and idx.get(pid)["n"] == 1       # n — число событий, не кадров
    assert idx.get(pid)["quality"] == pytest.approx(0.7)


def angle(u, v, deg):
    t = np.deg2rad(deg)
    return np.cos(t) * u + np.sin(t) * v


@pytest.mark.parametrize("same_cam", [True, False])
def test_auto_merge_split_profiles(tmp_path, same_cam):
    u = unit(70)
    v = unit(71) - (unit(71) @ u) * u
    v /= np.linalg.norm(v)
    # анфас (0°) и профиль (72°, косинус 0.31 < 0.363) завели двух людей; лицо под 20° сдвигает
    # первого к 10° — средние сближаются до 0.47: сливаем, только если оба видны на одной камере
    idx = gated(tmp_path, {1: (angle(u, v, 0), True, 0.9), 2: (angle(u, v, 72), True, 0.5),
                           3: (angle(u, v, 20), True, 0.4)})
    feed(idx, [("cam-a-1", 1), ("cam-a-2" if same_cam else "cam-b-2", 2)])
    first, second = (p["id"] for p in idx.people())
    idx.rename(second, "Петров")
    feed(idx, [("cam-a-3", 3)])
    people = idx.people()
    if same_cam:
        assert [p["id"] for p in people] == [first]                 # старший профиль остался
        assert people[0]["name"] == "Петров" and people[0]["n"] == 3
        assert sorted(idx.events_of(second)) == ["cam-a-1", "cam-a-2", "cam-a-3"]
        assert idx.get(second)["id"] == first
    else:
        assert len(people) == 2
    # ещё ближе (средние ≥ 0.5) — сливаем и с разных камер
    idx.engine.faces[4] = (angle(u, v, 40), True, 0.4)
    feed(idx, [("cam-a-4", 4), ("cam-a-5", 4)])
    assert len(idx.people()) == 1


def test_old_schema_migrates(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "people.db")
    db.executescript("CREATE TABLE event_people (event_id TEXT PRIMARY KEY, person_id TEXT, similarity REAL, created REAL);")
    db.commit()
    db.close()
    idx = make(tmp_path, {1: unit(80)})
    feed(idx, [("cam-1-e1", 1)])
    assert idx.person_of("cam-1-e1") is not None


def test_survives_restart(tmp_path):
    a = unit(20)
    feed(make(tmp_path, {1: a}), [("e1", 1)])
    idx = make(tmp_path, {1: near(a, 0.8)})
    feed(idx, [("e2", 1)])
    assert len(idx.people()) == 1 and idx.people()[0]["n"] == 2


def test_queue_full_drops_silently(tmp_path):
    idx = make(tmp_path, {1: unit(30)}, queue_size=1)
    for i in range(50):
        idx.submit(f"e{i}", frame(1), BOX)                  # не бросает и не блокирует
    assert idx.wait_idle(5)
    assert 1 <= idx.people()[0]["n"] <= 50


def test_disabled_without_models(tmp_path):
    idx = PeopleIndex(tmp_path / "people.db", tmp_path / "media", tmp_path / "nomodels")
    assert not idx.enabled
    idx.submit("e1", frame(1), BOX)
    assert idx.wait_idle(1)
    assert idx.people() == [] and idx.person_of("e1") is None and idx.get("x") is None
    assert idx.events_of("x") == [] and not idx.rename("x", "y") and not idx.merge("x", "y")


def test_similarity():
    a = unit(40)
    assert similarity(a, a) == pytest.approx(1.0)
    assert similarity(a, near(a, 0.5)) == pytest.approx(0.5, abs=1e-5)


@pytest.mark.skipif(not FaceEngine.available(REAL_MODELS), reason="нет моделей YuNet/SFace")
def test_real_engine_no_face():
    eng = FaceEngine(REAL_MODELS)
    img = np.random.default_rng(0).integers(0, 255, (480, 640, 3), np.uint8)
    assert eng.embed(img, [200, 50, 320, 400]) is None
    assert eng.embed(img, [0, 0, 10, 10]) is None           # слишком мелкая рамка
