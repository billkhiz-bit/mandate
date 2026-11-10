from datetime import date

import pytest

from mandate.brain import OfflineBrain
from mandate.engine import Engine, summary_of
from mandate.paypal import FakePayPal, PayPalError
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store

TODAY = date(2026, 11, 10)


@pytest.fixture
def engine(tmp_path):
    store = Store.open(tmp_path / "state.json", TODAY)
    return Engine(store, OfflineBrain(SAMPLE_MANDATE), FakePayPal(), today=lambda: TODAY)


def outcomes(engine):
    return {e["id"]: e["status"] for e in engine.s["inbox"]}


def test_sample_inbox_outcomes(engine):
    counts = engine.run_inbox()
    assert counts == {"done": 4, "waiting": 4, "nothing": 0}
    assert outcomes(engine) == {"E1": "done", "E2": "done", "E3": "done", "E4": "done",
                                "E5": "waiting", "E6": "waiting", "E7": "waiting", "E8": "waiting"}


def test_only_allowed_actions_reach_paypal(engine):
    engine.run_inbox()
    calls = [(c["kind"], c.get("amount")) for c in engine.paypal.calls]
    assert calls == [("payout", 420.0), ("refund", 39.0), ("invoice", None), ("reminder", None)]


def test_reverse_charge_invoice_sent_to_paypal(engine):
    engine.run_inbox()
    invoice = next(c["invoice"] for c in engine.paypal.calls if c["kind"] == "invoice")
    assert invoice["detail"]["currency_code"] == "EUR"
    assert "Reverse charge" in invoice["detail"]["note"]
    assert "tax" not in invoice["items"][0]
    assert invoice["items"][0]["quantity"] == "10"


def test_every_decision_names_its_rule(engine):
    engine.run_inbox()
    assert [a["decided_by"] for a in engine.s["activity"]] == ["R1", "R2", "R3", "R4"]


def test_scam_waits_with_reasons(engine):
    engine.run_inbox()
    scam = next(q for q in engine.s["queue"] if q["event_id"] == "E8")
    failed = [c["label"] for c in scam["checks"] if not c["ok"]]
    assert "No job in the tracker for this payment" in failed
    assert any("not on record" in f for f in scam["risk_flags"])


def test_rules_pass_but_ai_flag_still_holds(engine):
    for e in engine.s["inbox"]:
        if e["id"] != "E1":
            e["status"] = "skip"
    engine.s["inbox"][0]["demo_proposal"]["risk_flags"] = ["Bank details changed last week"]
    engine.run_inbox()
    assert outcomes(engine)["E1"] == "waiting"
    assert engine.paypal.calls == []


def test_books_balance(engine):
    engine.run_inbox()
    debit = round(sum(line["debit"] for line in engine.s["journal"]), 2)
    credit = round(sum(line["credit"] for line in engine.s["journal"]), 2)
    assert debit == credit > 0


def test_approve_pays_and_records_new_payee(engine):
    engine.run_inbox()
    entry = engine.approve("Q-E5")
    assert entry["decided_by"] == "You approved"
    assert engine.s["payees"]["tomas@ruizmotion.example"]["payouts"] == 1
    assert engine.s["jobs"]["J-105"]["paid"] is True


def test_approve_twice_is_refused(engine):
    engine.run_inbox()
    engine.approve("Q-E5")
    with pytest.raises(KeyError):
        engine.approve("Q-E5")


def test_duplicate_payment_request_is_stopped(engine):
    engine.run_inbox()
    engine.s["inbox"].append({**engine.s["inbox"][0], "id": "E9", "status": "new"})
    engine.run_inbox()
    held = next(q for q in engine.s["queue"] if q["event_id"] == "E9")
    assert held["checks"][0] == {"label": "Job J-104 has already been paid", "ok": False}
    assert sum(1 for c in engine.paypal.calls if c["kind"] == "payout") == 1


def test_owner_can_approve_a_payout_with_no_job(engine):
    engine.run_inbox()
    entry = engine.approve("Q-E8")
    assert entry["decided_by"] == "You approved" and entry["amount"] == 2400.0
    assert engine.s["payees"]["payments@quickprint-uk.example"]["payouts"] == 1


def test_negative_amount_never_passes(engine):
    for e in engine.s["inbox"]:
        if e["id"] != "E2":
            e["status"] = "skip"
    demo = engine.s["inbox"][1]["demo_proposal"]
    demo["amount"] = demo["unit_amount"] = -39.0
    engine.run_inbox()
    held = next(q for q in engine.s["queue"] if q["event_id"] == "E2")
    assert held["checks"][0] == {"label": "The amount must be more than zero", "ok": False}
    assert engine.paypal.calls == []


def test_invoice_total_must_match_quantity_times_price(engine):
    for e in engine.s["inbox"]:
        if e["id"] != "E3":
            e["status"] = "skip"
    engine.s["inbox"][2]["demo_proposal"]["unit_amount"] = 700.0
    engine.run_inbox()
    held = next(q for q in engine.s["queue"] if q["event_id"] == "E3")
    assert "doesn't match" in held["checks"][0]["label"]


def test_decline_moves_nothing(engine):
    engine.run_inbox()
    engine.decline("Q-E8")
    assert outcomes(engine)["E8"] == "declined"
    assert not any(c.get("email") == "payments@quickprint-uk.example" for c in engine.paypal.calls)


def test_running_again_does_nothing_new(engine):
    engine.run_inbox()
    before = len(engine.paypal.calls)
    assert engine.run_inbox() == {"done": 0, "waiting": 0, "nothing": 0}
    assert len(engine.paypal.calls) == before


def test_paypal_failure_leaves_item_waiting(engine):
    class Failing(FakePayPal):
        def payout(self, *a, **k):
            raise PayPalError("PayPal 422 UNPROCESSABLE_ENTITY")

    engine.paypal = Failing()
    engine.run_inbox()
    held = next(q for q in engine.s["queue"] if q["event_id"] == "E1")
    assert "PayPal didn't accept it" in held["summary"]


def test_dashboard_summary_hides_offline_answers(engine):
    summary = summary_of(engine.s, "Offline", "offline", TODAY)
    assert all("demo_proposal" not in e for e in summary["inbox"])


def test_compile_offline_accepts_sample_only(engine):
    same = engine.compile(SAMPLE_MANDATE.text)
    assert same["unclear"] == [] and len(same["mandate"]["rules"]) == 4
    other = engine.compile("Pay anyone anything.")
    assert other["unclear"] and other["mandate"]["rules"] == SAMPLE_MANDATE.to_dict()["rules"]
