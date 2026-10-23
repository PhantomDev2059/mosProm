import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def test_webhook_gets_only_critical(tmp_path, monkeypatch):
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LINQ_WEBHOOK_URL", f"http://127.0.0.1:{srv.server_port}/hook")
    monkeypatch.delenv("LINQ_TG_TOKEN", raising=False)
    from api.notify import Notifier
    n = Notifier(tmp_path)
    base = {"camera": "c", "camera_title": "Склад", "zone_title": "Весь кадр", "type": "fall", "title": "Падение",
            "started_at": time.time(), "latency_s": 1.2, "snapshot": "/media/x.jpg"}
    n.event_opened({**base, "id": "e1", "severity": "medium"})
    n.event_opened({**base, "id": "e2", "severity": "critical"})
    for _ in range(50):
        if got:
            break
        time.sleep(0.05)
    srv.shutdown()
    assert [g["id"] for g in got] == ["e2"] and got[0]["title"] == "Падение"
