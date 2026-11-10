import json
import socket
import threading
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from mandate.brain import OfflineBrain
from mandate.engine import Engine
from mandate.paypal import FakePayPal
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store
from server import make_handler


@pytest.fixture
def port(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    engine = Engine(Store.open(tmp_path / "state.json", date(2026, 11, 10)), OfflineBrain(SAMPLE_MANDATE),
                    FakePayPal(), today=lambda: date(2026, 11, 10))
    httpd = ThreadingHTTPServer(("127.0.0.1", free), make_handler(engine, free))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield free
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def test_page_and_state_load(port):
    status, html = request(port, "GET", "/")
    assert status == 200 and b"Read the inbox" in html
    status, data = request(port, "GET", "/api/state")
    assert status == 200 and len(json.loads(data)["inbox"]) == 8


def test_other_hosts_are_refused(port):
    status, _ = request(port, "GET", "/api/state", headers={"Host": f"evil.example:{port}"})
    assert status == 403


def test_post_must_be_json(port):
    status, _ = request(port, "POST", "/api/run", body="x=1",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 400


def test_run_then_approve(port):
    hdr = {"Content-Type": "application/json"}
    status, data = request(port, "POST", "/api/run", body="{}", headers=hdr)
    assert status == 200 and json.loads(data)["counts"]["done"] == 4
    status, data = request(port, "POST", "/api/approve", body=json.dumps({"id": "Q-E5"}), headers=hdr)
    assert status == 200 and json.loads(data)["entry"]["decided_by"] == "You approved"
    status, _ = request(port, "POST", "/api/approve", body=json.dumps({"id": "Q-E5"}), headers=hdr)
    assert status == 404


def test_negative_content_length_is_refused(port):
    status, _ = request(port, "POST", "/api/run", body="{}",
                        headers={"Content-Type": "application/json", "Content-Length": "-1"})
    assert status == 400


def test_files_outside_web_are_not_served(port):
    for path in ("/static/../app.py", "/..%2Fapp.py", "/C:/Windows/win.ini"):
        status, _ = request(port, "GET", path)
        assert status == 404
