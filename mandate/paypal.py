"""PayPal: the sandbox REST client Mandate acts through, and an offline stand-in.

LivePayPal talks only to the PayPal sandbox. Every call that moves money
sends a PayPal-Request-Id built from Mandate's own action id, so a retried
request can never pay or refund twice.

APIs used:
  Payouts      POST /v1/payments/payouts
  Refunds      POST /v2/payments/captures/{capture_id}/refund
  Invoicing    POST /v2/invoicing/invoices, /{id}/send, /{id}/remind
  Orders       POST /v2/checkout/orders, /{id}/capture   (seeding sales only)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

import httpx

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


class PayPalError(RuntimeError):
    """A PayPal call failed. The message is safe to show: it never holds keys."""


class PayPal(Protocol):
    mode: str

    def payout(self, action_id: str, email: str, amount: float, currency: str,
               note: str, subject: str) -> dict: ...

    def payout_status(self, batch_id: str) -> str: ...

    def refund(self, action_id: str, capture_id: str, amount: float, currency: str,
               note: str) -> dict: ...

    def create_and_send_invoice(self, action_id: str, invoice: dict) -> dict: ...

    def remind(self, action_id: str, invoice_id: str, subject: str, note: str) -> dict: ...


def _money(amount: float) -> str:
    return f"{amount:.2f}"


class LivePayPal:
    """PayPal REST client, sandbox only."""

    mode = "sandbox"

    def __init__(self, client_id: str, secret: str, base_url: str = SANDBOX_BASE,
                 http: Optional[httpx.Client] = None):
        if base_url.rstrip("/") != SANDBOX_BASE:
            raise PayPalError("This build only talks to the PayPal sandbox.")
        if not client_id or not secret:
            raise PayPalError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must both be set.")
        self._id, self._secret = client_id, secret
        self._base = SANDBOX_BASE
        self._http = http or httpx.Client(timeout=30.0)
        self._token: Optional[str] = None
        self._token_expires = 0.0

    @classmethod
    def from_env(cls) -> "LivePayPal":
        return cls(os.environ.get("PAYPAL_CLIENT_ID", ""), os.environ.get("PAYPAL_CLIENT_SECRET", ""))

    # -- plumbing ---------------------------------------------------------
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        try:
            r = self._http.post(f"{self._base}/v1/oauth2/token", auth=(self._id, self._secret),
                                data={"grant_type": "client_credentials"},
                                headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise PayPalError(f"Could not reach PayPal ({exc.__class__.__name__}).") from exc
        if r.status_code != 200:
            raise PayPalError(f"PayPal refused the app keys (HTTP {r.status_code}). "
                              "Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are the SANDBOX keys.")
        body = r.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + float(body.get("expires_in", 300))
        return self._token

    def _call(self, method: str, path: str, body: Optional[dict] = None,
              request_id: Optional[str] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}",
                   "Content-Type": "application/json",
                   "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            r = self._http.request(method, f"{self._base}{path}", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PayPalError(f"Could not reach PayPal ({exc.__class__.__name__}).") from exc
        if r.status_code >= 400:
            raise PayPalError(_describe_error(r))
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # -- money out --------------------------------------------------------
    def payout(self, action_id, email, amount, currency, note, subject) -> dict:
        body = {
            "sender_batch_header": {"sender_batch_id": f"mandate-{action_id}",
                                    "email_subject": subject, "email_message": note},
            "items": [{"recipient_type": "EMAIL", "receiver": email,
                       "amount": {"value": _money(amount), "currency": currency},
                       "note": note, "sender_item_id": action_id}],
        }
        out = self._call("POST", "/v1/payments/payouts", body, request_id=f"payout-{action_id}")
        header = out.get("batch_header", {})
        return {"id": header.get("payout_batch_id", ""), "status": header.get("batch_status", "")}

    def payout_status(self, batch_id: str) -> str:
        """A new batch is always PENDING; the item's transaction_status is the real
        outcome (SUCCESS, UNCLAIMED for an email with no PayPal account, FAILED...)."""
        out = self._call("GET", f"/v1/payments/payouts/{batch_id}")
        items = out.get("items") or []
        if items and items[0].get("transaction_status"):
            return str(items[0]["transaction_status"])
        # No item yet: a batch still in progress says so, but a batch SUCCESS only means the
        # batch was processed, not that the payee got paid, so report "unknown" ("") instead.
        batch = str(out.get("batch_header", {}).get("batch_status", ""))
        return batch if batch in ("PENDING", "PROCESSING", "NEW") else ""

    def refund(self, action_id, capture_id, amount, currency, note) -> dict:
        body: dict = {"amount": {"value": _money(amount), "currency_code": currency}}
        if note.strip():  # PayPal rejects an empty note_to_payer
            body["note_to_payer"] = note[:255]
        out = self._call("POST", f"/v2/payments/captures/{capture_id}/refund", body,
                         request_id=f"refund-{action_id}")
        return {"id": out.get("id", ""), "status": out.get("status", "")}

    # -- money in ---------------------------------------------------------
    def create_and_send_invoice(self, action_id, invoice) -> dict:
        draft = self._call("POST", "/v2/invoicing/invoices", invoice, request_id=f"invoice-{action_id}")
        invoice_id = draft.get("id") or _id_from_href(draft.get("href", ""))
        if not invoice_id:
            raise PayPalError("PayPal created the invoice but returned no id.")
        sent = self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                          {"send_to_invoicer": False, "send_to_recipient": True},
                          request_id=f"invoice-send-{action_id}")
        return {"id": invoice_id, "number": draft.get("detail", {}).get("invoice_number", ""),
                "status": "SENT", "url": sent.get("href", "")}

    def remind(self, action_id, invoice_id, subject, note) -> dict:
        self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                   {"subject": subject, "note": note, "send_to_invoicer": False,
                    "send_to_recipient": True}, request_id=f"remind-{action_id}")
        return {"id": invoice_id, "status": "REMINDER_SENT"}

    # -- seeding the sandbox (sales to refund later) ----------------------
    def create_order(self, reference: str, description: str, amount: float, currency: str,
                     return_url: str) -> tuple[str, str]:
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": reference, "description": description,
                                    "amount": {"currency_code": currency, "value": _money(amount)}}],
                # The sample sales are digital (a template, a course): NO_SHIPPING stops PayPal asking
                # the buyer for a delivery address at checkout.
                "payment_source": {"paypal": {"experience_context": {
                    "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                    "return_url": return_url, "cancel_url": return_url}}}}
        out = self._call("POST", "/v2/checkout/orders", body, request_id=f"order-{reference}-{int(time.time())}")
        link = next((ln["href"] for ln in out.get("links", []) if ln.get("rel") in ("payer-action", "approve")), "")
        return out.get("id", ""), link

    def capture_order(self, order_id: str) -> str:
        out = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {},
                         request_id=f"capture-{order_id}")
        try:
            return out["purchase_units"][0]["payments"]["captures"][0]["id"]
        except (KeyError, IndexError, TypeError):
            raise PayPalError(f"Order {order_id} has no capture yet: approve it as the sandbox buyer first.")


def _id_from_href(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1] if href else ""


def _describe_error(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"PayPal returned HTTP {r.status_code}."
    name = body.get("name") or body.get("error") or "ERROR"
    message = body.get("message") or body.get("error_description") or ""
    details = "; ".join(d.get("issue", "") for d in body.get("details", []) if isinstance(d, dict))
    debug = body.get("debug_id", "")
    parts = [f"PayPal {r.status_code} {name}", message, details, f"debug_id {debug}" if debug else ""]
    return " | ".join(p for p in parts if p)


@dataclass
class FakePayPal:
    """Offline stand-in with the same surface. Records every call so tests
    can assert exactly what would have been sent to PayPal."""

    mode: str = "offline"
    calls: list[dict] = field(default_factory=list)

    def _record(self, kind: str, **kw) -> str:
        n = sum(1 for c in self.calls if c["kind"] == kind) + 1
        ref = f"OFFLINE-{kind.upper()}-{n:04d}"
        self.calls.append({"kind": kind, "id": ref, **kw})
        return ref

    def payout(self, action_id, email, amount, currency, note, subject) -> dict:
        return {"id": self._record("payout", action_id=action_id, email=email, amount=amount,
                                   currency=currency), "status": "SUCCESS"}

    def payout_status(self, batch_id: str) -> str:
        return "SUCCESS"

    def refund(self, action_id, capture_id, amount, currency, note) -> dict:
        return {"id": self._record("refund", action_id=action_id, capture_id=capture_id,
                                   amount=amount, currency=currency), "status": "COMPLETED"}

    def create_and_send_invoice(self, action_id, invoice) -> dict:
        ref = self._record("invoice", action_id=action_id, invoice=invoice)
        return {"id": ref, "number": invoice.get("detail", {}).get("invoice_number", ""),
                "status": "SENT", "url": ""}

    def remind(self, action_id, invoice_id, subject, note) -> dict:
        self._record("reminder", action_id=action_id, invoice_id=invoice_id)
        return {"id": invoice_id, "status": "REMINDER_SENT"}
