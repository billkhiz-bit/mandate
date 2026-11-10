"""One test per finding from the 10 Nov code review, so none of them can come back."""

import json
from datetime import date, timedelta

import pytest

from mandate.brain import BrainUnavailable, OfflineBrain
from mandate.engine import Engine
from mandate.paypal import FakePayPal, PayPalError
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store, StoreError

TODAY = date(2026, 11, 10)


def make(tmp_path, paypal=None, brain=None) -> Engine:
    store = Store.open(tmp_path / "state.json", TODAY)
    return Engine(store, brain or OfflineBrain(SAMPLE_MANDATE), paypal or FakePayPal(), today=lambda: TODAY)


def only(engine, event_id):
    for e in engine.s["inbox"]:
        if e["id"] != event_id:
            e["status"] = "skip"


def test_an_item_the_ai_cannot_handle_reaches_the_owner(tmp_path):
    engine = make(tmp_path)
    engine.s["inbox"].append({"id": "E9", "received": "2026-11-10 12:00", "source": "Email", "from": "x@mail.example",
                              "subject": "Something new", "refs": {}, "body": "Hello"})
    only(engine, "E9")
    assert engine.run_inbox() == {"done": 0, "waiting": 1, "nothing": 0}
    item = engine.s["queue"][0]
    assert item["kind"] == "none" and item["outcome"] == "Waiting"
    with pytest.raises(ValueError, match="nothing to send"):
        engine.approve(item["id"])
    entry = engine.decline(item["id"])
    assert entry["outcome"] == "Handled" and engine.s["inbox"][-1]["status"] == "done"
    assert engine.paypal.calls == []


def test_unexpected_ai_error_queues_the_item(tmp_path):
    class Broken(OfflineBrain):
        def propose(self, item, records):
            raise RuntimeError("boom")

    engine = make(tmp_path, brain=Broken(SAMPLE_MANDATE))
    only(engine, "E1")
    engine.run_inbox()
    assert engine.s["queue"][0]["kind"] == "none"


def test_bad_key_stops_the_run_and_leaves_items_new(tmp_path):
    class NoKey(OfflineBrain):
        def propose(self, item, records):
            raise BrainUnavailable("The Anthropic credentials were refused.")

    engine = make(tmp_path, brain=NoKey(SAMPLE_MANDATE))
    with pytest.raises(BrainUnavailable):
        engine.run_inbox()
    assert all(e.get("status", "new") == "new" for e in engine.s["inbox"])


def test_refund_in_another_currency_is_blocked_even_with_approval(tmp_path):
    engine = make(tmp_path)
    only(engine, "E2")
    engine.s["inbox"][1]["demo_proposal"]["currency"] = "USD"
    engine.run_inbox()
    item = engine.s["queue"][0]
    assert item["checks"][0] == {"label": "Refund is in USD but sale S-2001 was in GBP", "ok": False}
    with pytest.raises(PayPalError, match="was in GBP"):
        engine.approve(item["id"])
    assert engine.paypal.calls == []


def test_refund_above_what_is_left_cannot_be_approved(tmp_path):
    engine = make(tmp_path)
    only(engine, "E6")
    engine.s["sales"]["S-1873"]["refunded"] = 100.0  # £49 left
    engine.run_inbox()
    with pytest.raises(PayPalError, match="£49.00 left"):
        engine.approve("Q-E6")


def test_sandbox_refund_without_a_capture_cannot_be_approved(tmp_path):
    class Sandbox(FakePayPal):
        mode = "sandbox"

    engine = make(tmp_path, paypal=Sandbox(mode="sandbox"))
    only(engine, "E6")
    engine.run_inbox()
    with pytest.raises(PayPalError, match="no PayPal capture"):
        engine.approve("Q-E6")
    assert engine.paypal.calls == []


def test_unreadable_records_are_never_overwritten(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"half": ', encoding="utf-8")
    with pytest.raises(StoreError, match="left as it is"):
        Store.open(path, TODAY)
    assert path.read_text(encoding="utf-8") == '{"half": '
    Store.fresh(path, TODAY)
    assert (tmp_path / "state.json.bak").read_text(encoding="utf-8") == '{"half": '
    assert "run_id" in json.loads(path.read_text(encoding="utf-8"))


def test_new_invoice_is_due_after_its_terms(tmp_path):
    engine = make(tmp_path)
    only(engine, "E3")
    engine.run_inbox()
    new = next(v for k, v in engine.s["invoices"].items() if k != "INV-0041")
    assert new["due"] == (TODAY + timedelta(days=14)).isoformat()
    sent = next(c["invoice"] for c in engine.paypal.calls if c["kind"] == "invoice")
    # PayPal's spec has NET_10/15/30... but no NET_14, so the due date is given explicitly
    assert sent["detail"]["payment_term"] == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": new["due"]}


def test_journal_lines_have_unique_ids(tmp_path):
    engine = make(tmp_path)
    engine.run_inbox()
    ids = [line["id"] for line in engine.s["journal"]]
    assert len(ids) == len(set(ids)) > 0
