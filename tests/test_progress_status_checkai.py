"""Item-by-item runs, real payout statuses, and the check-ai scorer."""

import json
from datetime import date

import httpx

from mandate.brain import OfflineBrain, Proposal
from mandate.check_ai import score
from mandate.engine import Engine
from mandate.paypal import FakePayPal, LivePayPal
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store
from tests.test_live_paypal import FakePayPalServer
from tests.test_server import port, request  # noqa: F401 - pytest fixture

TODAY = date(2026, 11, 10)


def test_one_item_at_a_time(tmp_path):
    engine = Engine(Store.open(tmp_path / "s.json", TODAY), OfflineBrain(SAMPLE_MANDATE), FakePayPal(),
                    today=lambda: TODAY)
    assert engine.remaining() == 8
    assert engine.run_inbox(limit=1) == {"done": 1, "waiting": 0, "nothing": 0}
    assert engine.remaining() == 7
    assert [e.get("status", "new") for e in engine.s["inbox"][:2]] == ["done", "new"]


def test_payout_status_moves_from_pending_to_the_real_outcome(tmp_path):
    server = FakePayPalServer()
    paypal = LivePayPal("id", "secret", http=httpx.Client(transport=httpx.MockTransport(server)))
    store = Store.open(tmp_path / "s.json", TODAY)
    engine = Engine(store, OfflineBrain(SAMPLE_MANDATE), paypal, today=lambda: TODAY)
    for e in store.state["inbox"][1:]:
        e["status"] = "skip"
    engine.run_inbox()
    assert engine.s["activity"][0]["paypal_status"] == "PENDING"
    assert engine.refresh_payouts() == 1
    assert engine.s["activity"][0]["paypal_status"] == "UNCLAIMED"
    assert server.requests[-1][:2] == ("GET", "/v1/payments/payouts/BATCH-1")
    # UNCLAIMED isn't final (PayPal returns unclaimed money after 30 days), so it is checked again
    assert engine.refresh_payouts() == 0 and len(server.requests) == 3


def _sandbox_engine(tmp_path, final_status):
    class Server(FakePayPalServer):
        def __call__(self, request):
            if request.method == "GET" and request.url.path.startswith("/v1/payments/payouts/"):
                self.requests.append(("GET", request.url.path, {}, {}))
                return httpx.Response(200, json={"items": [{"transaction_status": final_status}]})
            return super().__call__(request)

    paypal = LivePayPal("id", "secret", http=httpx.Client(transport=httpx.MockTransport(Server())))
    store = Store.open(tmp_path / "s.json", TODAY)
    for e in store.state["inbox"][1:]:
        e["status"] = "skip"
    engine = Engine(store, OfflineBrain(SAMPLE_MANDATE), paypal, today=lambda: TODAY)
    engine.run_inbox()
    return engine


def test_failed_payout_is_undone_in_the_records(tmp_path):
    engine = _sandbox_engine(tmp_path, "FAILED")
    assert engine.refresh_payouts() == 1
    entry = engine.s["activity"][0]
    assert entry["outcome"] == "Failed" and entry["paypal_status"] == "FAILED"
    assert engine.s["jobs"]["J-104"]["paid"] is False
    assert engine.s["payees"]["priya@illustrates.example"]["payouts"] == 3  # back to before
    by_code: dict = {}
    for line in engine.s["journal"]:
        by_code[line["code"]] = by_code.get(line["code"], 0) + line["debit"] - line["credit"]
    assert by_code == {6002: 0, 1260: 0}  # the payout and its reversal cancel out
    facts = engine._facts(Proposal(**engine.s["inbox"][0]["demo_proposal"]))
    assert facts.money_out_today_gbp == 0 and not facts.blocking  # can be paid again


def test_batch_success_without_items_is_not_taken_as_paid():
    server = httpx.MockTransport(lambda r: httpx.Response(200, json={"batch_header": {"batch_status": "SUCCESS"}})
                                 if r.method == "GET" else httpx.Response(200, json={"access_token": "T",
                                                                                     "expires_in": 3600}))
    paypal = LivePayPal("id", "secret", http=httpx.Client(transport=server))
    assert paypal.payout_status("BATCH-1") == ""


def test_server_runs_one_item_and_validates_limit(port):  # noqa: F811
    hdr = {"Content-Type": "application/json"}
    status, data = request(port, "POST", "/api/run", body=json.dumps({"limit": 1}), headers=hdr)
    assert status == 200 and json.loads(data)["remaining"] == 7
    for bad in ('{"limit": "x"}', '{"limit": 0}', '{"limit": true}'):
        status, _ = request(port, "POST", "/api/run", body=bad, headers=hdr)
        assert status == 400
    status, data = request(port, "POST", "/api/refresh-payouts", body="{}", headers=hdr)
    assert status == 200 and json.loads(data)["changed"] == 0


def test_check_ai_scores_the_expected_answers_as_a_full_pass():
    rows = score(OfflineBrain(SAMPLE_MANDATE), TODAY)
    assert len(rows) == 8 and all(r["ok"] for r in rows)
    assert [r["expected"] for r in rows] == ["done"] * 4 + ["waiting"] * 4


class Overcautious(OfflineBrain):
    """Flags a routine payout and misses the scam: both must show as misses."""

    def propose(self, item, records):
        p = super().propose(item, records)
        if item["id"] == "E1":
            return Proposal(**{**p.model_dump(), "risk_flags": ["Large-ish amount"]})
        if item["id"] == "E8":
            return Proposal(**{**p.model_dump(), "risk_flags": []})
        if item["id"] == "E2":
            raise RuntimeError("timeout")
        return p


def test_check_ai_reports_misses_and_errors():
    rows = {r["id"]: r for r in score(Overcautious(SAMPLE_MANDATE), TODAY)}
    assert not rows["E1"]["ok"] and "flagged a routine item" in rows["E1"]["differences"]
    assert rows["E1"]["got"] == "waiting"
    assert not rows["E8"]["ok"] and "missed a risk" in rows["E8"]["differences"]
    assert rows["E8"]["got"] == "waiting"  # the rules still stop it: no job, over limits
    assert rows["E2"]["got"] == "error"
    assert rows["E3"]["ok"]
