import json
import time

import pytest

from api.review import ReviewManager


def test_verdict_person_and_delete(tmp_path):
    media, videos, uploads = tmp_path / "media", tmp_path / "videos", tmp_path / "uploads"
    for d in (media, videos):
        d.mkdir()
    rm = ReviewManager(tmp_path / "review.db", media, videos, uploads, detectors={})
    rm.store.exec("INSERT INTO review_jobs (id, title, source, preset, status, progress, created) VALUES (?,?,?,?,?,?,?)",
                  ("j1", "Смена", "x.mp4", "universal", "done", 1.0, time.time()))
    (media / "rv-e1.mp4").write_bytes(b"clip")
    (media / "rv-e1.jpg").write_bytes(b"jpg")
    for i, rank in (("e1", 2.5), ("e2", 0.7)):
        rm.store.exec("INSERT INTO review_items (id, job, type, title, severity, start_s, end_s, score, rank, clip, snapshot, details)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (i, "j1", "fall", "Падение", "critical", 10, 15, 0.9, rank, f"/media/rv-{i}.mp4", f"/media/rv-{i}.jpg", json.dumps({})))
    assert [r["id"] for r in rm.items("j1")] == ["e1", "e2"]             # сначала самые опасные и уверенные
    item = rm.set_verdict("e1", "confirmed", " Иванов И. ", None)
    assert item["verdict"] == "confirmed" and item["person"] == "Иванов И."
    with pytest.raises(ValueError):
        rm.set_verdict("e1", "maybe", None, None)
    job = rm.jobs_list()[0]
    assert (job["items"], job["reviewed"], job["confirmed"]) == (2, 1, 1)
    assert rm.delete("j1")
    assert rm.items("j1") == [] and rm.jobs_list() == []
    assert not (media / "rv-e1.mp4").exists() and not (media / "rv-e1.jpg").exists()
