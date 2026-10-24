from api.store import EventStore


def make_store(tmp_path):
    s = EventStore(tmp_path / "events.db")
    rows = [  # камера, зона, тип, время начала
        ("cam-01-site", "frame", "no_helmet", 1000.0),
        ("cam-01-site", "zone-a", "zone_intrusion", 2000.0),
        ("cam-03-shop", "frame", "running", 3000.0),
        ("cam-03-shop", "zone-b", "no_helmet", 4000.0),
    ]
    for i, (cam, zone, typ, t) in enumerate(rows):
        s.insert({"id": f"e{i}", "camera": cam, "camera_title": cam, "zone": zone, "zone_title": zone.upper(),
                  "type": typ, "title": typ, "severity": "high", "started_at": t, "duration_s": 1.0, "status": "new"})
    return s


def ids(rows):
    return sorted(r["id"] for r in rows)


def test_query_by_zone_and_camera(tmp_path):
    s = make_store(tmp_path)
    assert ids(s.query(zone="frame")) == ["e0", "e2"]
    assert ids(s.query(camera="cam-03-shop", zone="frame")) == ["e2"]
    assert ids(s.query(camera="cam-01-site", zone="zone-b")) == []


def test_query_time_range_is_inclusive(tmp_path):
    s = make_store(tmp_path)
    assert ids(s.query(since=2000, until=3000)) == ["e1", "e2"]
    assert ids(s.query(until=1999)) == ["e0"]
    assert ids(s.query(since=0)) == ["e0", "e1", "e2", "e3"]          # 0 — «с начала журнала», а не «без фильтра»
    assert ids(s.query(type_="no_helmet", since=1500)) == ["e3"]


def test_stats_respects_range(tmp_path):
    s = make_store(tmp_path)
    assert s.stats(since=1500, until=3500)["total"] == 2
    assert s.stats(since=None)["total"] == 4
