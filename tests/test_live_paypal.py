"""LivePayPal against a local fake of PayPal's REST endpoints (httpx.MockTransport).

No keys and no network: every URL, header and body the real client sends is
checked here, so the first sandbox run tests the keys, not the code.
"""

import json
from datetime import date

import httpx
import pytest

from mandate.brain import OfflineBrain
from mandate.engine import Engine
from mandate.paypal import SANDBOX_BASE, LivePayPal, PayPalError
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store

TODAY = date(2026, 11, 10)


class FakePayPalServer:
    """Answers like the sandbox and records every request."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict, dict]] = []
        self.token_calls = 0
        self.fail_next: dict | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content and path != "/v1/oauth2/token" else {}
        if path == "/v1/oauth2/token":
            self.token_calls += 1
            assert request.headers["Authorization"].startswith("Basic ")
            assert request.content == b"grant_type=client_credentials"
            return httpx.Response(200, json={"access_token": "TOKEN-1", "expires_in": 32400})
        assert request.headers["Authorization"] == "Bearer TOKEN-1"
        self.requests.append((request.method, path, dict(request.headers), body))
        if self.fail_next:
            failure, self.fail_next = self.fail_next, None
            return httpx.Response(422, json=failure)
        if request.method == "GET" and path.startswith("/v1/payments/payouts/"):
            return httpx.Response(200, json={"batch_header": {"payout_batch_id": path.rsplit("/", 1)[-1],
                                                              "batch_status": "SUCCESS"},
                                             "items": [{"payout_item_id": "ITEM-1", "transaction_status": "UNCLAIMED"}]})
        if path == "/v1/payments/payouts":
            return httpx.Response(201, json={"batch_header": {"payout_batch_id": "BATCH-1", "batch_status": "PENDING"}})
        if path.startswith("/v2/payments/captures/") and path.endswith("/refund"):
            return httpx.Response(201, json={"id": "REFUND-1", "status": "COMPLETED"})
        if path == "/v2/invoicing/invoices":
            return httpx.Response(201, json={"id": "INV2-ABCD", "status": "DRAFT",
                                             "detail": {"invoice_number": body["detail"]["invoice_number"]}})
        if path.endswith("/send"):
            return httpx.Response(200, json={"href": "https://www.sandbox.paypal.com/invoice/p/#INV2-ABCD"})
        if path.endswith("/remind"):
            return httpx.Response(204)
        if path == "/v2/checkout/orders":
            return httpx.Response(201, json={"id": "ORDER-1", "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "self", "href": f"{SANDBOX_BASE}/v2/checkout/orders/ORDER-1"},
                {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1"}]})
        if path.endswith("/capture"):
            return httpx.Response(201, json={"id": "ORDER-1", "status": "COMPLETED", "purchase_units": [
                {"payments": {"captures": [{"id": "CAPTURE-1", "status": "COMPLETED"}]}}]})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": path})


@pytest.fixture
def server():
    return FakePayPalServer()


@pytest.fixture
def paypal(server):
    return LivePayPal("client-id", "client-secret", http=httpx.Client(transport=httpx.MockTransport(server)))


def test_refuses_anything_but_the_sandbox():
    with pytest.raises(PayPalError, match="only talks to the PayPal sandbox"):
        LivePayPal("id", "secret", base_url="https://api-m.paypal.com")


def test_refuses_missing_keys():
    with pytest.raises(PayPalError, match="must both be set"):
        LivePayPal("", "")


def test_payout_request(paypal, server):
    out = paypal.payout("run1-E1", "priya@illustrates.example", 420, "GBP", note="Thanks", subject="Payment")
    assert out == {"id": "BATCH-1", "status": "PENDING"}
    method, path, headers, body = server.requests[0]
    assert (method, path) == ("POST", "/v1/payments/payouts")
    assert headers["paypal-request-id"] == "payout-run1-E1"
    assert body["sender_batch_header"]["sender_batch_id"] == "mandate-run1-E1"
    item = body["items"][0]
    assert item["recipient_type"] == "EMAIL" and item["receiver"] == "priya@illustrates.example"
    assert item["amount"] == {"value": "420.00", "currency": "GBP"}


def test_token_is_reused(paypal, server):
    paypal.payout("a", "x@example.com", 1, "GBP", "n", "s")
    paypal.payout("b", "x@example.com", 1, "GBP", "n", "s")
    assert server.token_calls == 1


def test_refund_request(paypal, server):
    out = paypal.refund("run1-E2", "CAPTURE-9", 39, "GBP", note="Sorry")
    assert out == {"id": "REFUND-1", "status": "COMPLETED"}
    _, path, headers, body = server.requests[0]
    assert path == "/v2/payments/captures/CAPTURE-9/refund"
    assert headers["paypal-request-id"] == "refund-run1-E2"
    assert body == {"amount": {"value": "39.00", "currency_code": "GBP"}, "note_to_payer": "Sorry"}


def test_empty_refund_note_is_left_out(paypal, server):
    paypal.refund("run1-E2", "CAPTURE-9", 39, "GBP", note="  ")
    assert server.requests[0][3] == {"amount": {"value": "39.00", "currency_code": "GBP"}}


def test_invoice_terms_are_valid_for_paypal(tmp_path, paypal, server):
    """Checked against PayPal's invoicing v2 spec: payment_term_type has no NET_14."""
    valid = {"DUE_ON_RECEIPT", "DUE_ON_DATE_SPECIFIED", "NET_10", "NET_15", "NET_30", "NET_45", "NET_60",
             "NET_90", "NO_DUE_DATE"}
    store = Store.open(tmp_path / "state.json", TODAY)
    for e in store.state["inbox"]:
        if e["id"] != "E3":
            e["status"] = "skip"
    Engine(store, OfflineBrain(SAMPLE_MANDATE), paypal, today=lambda: TODAY).run_inbox()
    term = server.requests[0][3]["detail"]["payment_term"]
    assert term["term_type"] in valid and term["due_date"] == "2026-11-24"


def test_invoice_is_created_then_sent(paypal, server):
    out = paypal.create_and_send_invoice("run1-E3", {"detail": {"invoice_number": "LL-0042"}})
    assert out["id"] == "INV2-ABCD" and out["number"] == "LL-0042" and out["status"] == "SENT"
    paths = [r[1] for r in server.requests]
    assert paths == ["/v2/invoicing/invoices", "/v2/invoicing/invoices/INV2-ABCD/send"]
    assert server.requests[1][3] == {"send_to_invoicer": False, "send_to_recipient": True}


def test_reminder_accepts_an_empty_204(paypal, server):
    assert paypal.remind("run1-E4", "INV2-ABCD", "Reminder", "Please pay")["status"] == "REMINDER_SENT"
    assert server.requests[0][1] == "/v2/invoicing/invoices/INV2-ABCD/remind"


def test_order_then_capture(paypal):
    order_id, link = paypal.create_order("S-2001", "Brand Kit template", 39, "GBP", "http://127.0.0.1:8600/")
    assert order_id == "ORDER-1" and "checkoutnow" in link
    assert paypal.capture_order(order_id) == "CAPTURE-1"


def test_errors_are_readable_and_hold_no_secrets(paypal, server):
    server.fail_next = {"name": "UNPROCESSABLE_ENTITY", "message": "The requested action could not be performed.",
                        "details": [{"issue": "CURRENCY_NOT_SUPPORTED"}], "debug_id": "f00d"}
    with pytest.raises(PayPalError) as err:
        paypal.payout("x", "x@example.com", 1, "GBP", "n", "s")
    text = str(err.value)
    assert "422 UNPROCESSABLE_ENTITY" in text and "CURRENCY_NOT_SUPPORTED" in text and "f00d" in text
    assert "client-secret" not in text and "TOKEN-1" not in text


def test_bad_keys_give_a_plain_message():
    refusing = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_client"}))
    paypal = LivePayPal("id", "wrong", http=httpx.Client(transport=refusing))
    with pytest.raises(PayPalError, match="SANDBOX keys"):
        paypal.payout("x", "x@example.com", 1, "GBP", "n", "s")


def test_whole_inbox_through_the_live_client(tmp_path, paypal, server):
    """The engine in sandbox mode, with the seeded ids a real seed would store."""
    store = Store.open(tmp_path / "state.json", TODAY)
    store.state["sales"]["S-2001"]["capture_id"] = "CAPTURE-2001"
    store.state["sales"]["S-1873"]["capture_id"] = "CAPTURE-1873"
    store.state["invoices"]["INV-0041"]["paypal_id"] = "INV2-0041"
    engine = Engine(store, OfflineBrain(SAMPLE_MANDATE), paypal, today=lambda: TODAY)

    assert engine.run_inbox() == {"done": 4, "waiting": 4, "nothing": 0}
    paths = [r[1] for r in server.requests]
    assert paths == ["/v1/payments/payouts", "/v2/payments/captures/CAPTURE-2001/refund",
                     "/v2/invoicing/invoices", "/v2/invoicing/invoices/INV2-ABCD/send",
                     "/v2/invoicing/invoices/INV2-0041/remind"]
    invoice = server.requests[2][3]
    assert invoice["detail"]["invoice_number"].startswith("LL-0042-")  # run tag keeps sandbox numbers unique
    assert invoice["detail"]["currency_code"] == "EUR" and "tax" not in invoice["items"][0]

    engine.approve("Q-E5")
    assert server.requests[-1][1] == "/v1/payments/payouts"
    assert server.requests[-1][3]["items"][0]["amount"] == {"value": "750.00", "currency": "EUR"}


def test_sandbox_mode_without_seed_waits_instead_of_guessing(tmp_path, paypal, server):
    store = Store.open(tmp_path / "state.json", TODAY)
    engine = Engine(store, OfflineBrain(SAMPLE_MANDATE), paypal, today=lambda: TODAY)
    engine.run_inbox()
    held = {q["event_id"]: q["checks"][0]["label"] for q in engine.s["queue"]}
    assert "run the sandbox seed" in held["E2"] and "run the sandbox seed" in held["E4"]
    assert all("/refund" not in r[1] and "/remind" not in r[1] for r in server.requests)
