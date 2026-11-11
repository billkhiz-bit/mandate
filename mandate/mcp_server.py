"""Mandate as an MCP server, so any AI agent can ask it to move money on PayPal.

Every request goes through the owner's mandate. If the rules allow it, PayPal acts at once;
if not, it waits in the owner's queue on the dashboard. The tools let an agent read the
rules, ask and check what happened. None of them can approve, decline or change the
mandate: those stay with the owner.

Start the dashboard first (`python app.py serve`), then point an MCP client at
`python app.py mcp`. This server talks to the dashboard over HTTP on this machine, so the
dashboard stays the only thing that writes the records.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

DASHBOARD = os.environ.get("MANDATE_URL", "http://127.0.0.1:8600").rstrip("/")
Currency = Literal["GBP", "EUR", "USD"]
# How a few MCP clients name themselves, and how the owner's inbox should show them.
KNOWN_CLIENTS = {"claude-ai": "Claude Desktop", "claude-code": "Claude Code", "mcp": "An AI agent"}

server = FastMCP(
    "Mandate",
    instructions=(
        "Mandate moves a small firm's money on PayPal, but only within rules the owner wrote. "
        "Call read_mandate first. Then ask for one action at a time with the request tools, using "
        "the job, sale, client or invoice id from the firm's records. If a request comes back "
        "waiting, tell the user it is with the owner; asking again will not change the answer."),
    log_level="WARNING",  # stderr stays quiet unless something goes wrong
)
READS = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ASKS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

_http: Optional[httpx.Client] = None  # tests swap in a client for their own server


def _client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(base_url=DASHBOARD, timeout=90)  # a payout can take a while
    return _http


def _call(method: str, path: str, body: Optional[dict] = None) -> dict:
    try:
        r = _client().request(method, path, json=body)
    except httpx.HTTPError:
        raise RuntimeError("Mandate's dashboard isn't running. Ask the owner to start it with: "
                           "python app.py serve") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise RuntimeError(data.get("error") or f"Mandate refused the request ({r.status_code}).")
    return data


def _agent_name(ctx: Optional[Context]) -> str:
    if name := os.environ.get("MANDATE_AGENT_NAME", "").strip():
        return name
    try:
        info = ctx.session.client_params.clientInfo
    except AttributeError:  # no context, or the client sent no details
        return "An AI agent"
    return info.title or KNOWN_CLIENTS.get(info.name, info.name) or "An AI agent"


def _ask(ctx: Optional[Context], **request) -> dict:
    return _call("POST", "/api/agent/request", {**request, "agent": _agent_name(ctx)})["request"]


@server.tool(annotations=READS)
def read_mandate() -> dict:
    """Read the owner's mandate: what Mandate may do on PayPal without asking, the daily
    limit, and how much has gone out today. Read it before asking, so you can tell the
    user which requests will need the owner."""
    return _call("GET", "/api/agent/mandate")


@server.tool(annotations=ASKS)
def request_payout(job_reference: str, payee_name: str, payee_email: str, amount: float,
                   currency: Currency, note_to_payee: str, reason: str, ctx: Context) -> dict:
    """Ask Mandate to pay a freelancer or supplier through PayPal Payouts.

    job_reference is the job's id in the firm's job tracker (for example J-104). Mandate
    checks the payee, amount and sign-off against that job and the owner's limits. If the
    mandate allows it, PayPal pays at once; otherwise it waits for the owner. reason tells
    the owner why you are asking."""
    return _ask(ctx, action="payout", reference=job_reference, payee_name=payee_name,
                payee_email=payee_email, amount=amount, currency=currency,
                description=note_to_payee, reason=reason)


@server.tool(annotations=ASKS)
def request_refund(sale_reference: str, customer_name: str, customer_email: str, amount: float,
                   currency: Currency, note_to_customer: str, reason: str, ctx: Context) -> dict:
    """Ask Mandate to refund a customer through PayPal, against a sale it has on record.

    sale_reference is the sale's id (for example S-2001); customer_email must be the buyer's.
    Mandate checks the amount and the days since the sale against the owner's rules."""
    return _ask(ctx, action="refund", reference=sale_reference, payee_name=customer_name,
                payee_email=customer_email, amount=amount, currency=currency,
                description=note_to_customer, reason=reason)


@server.tool(annotations=ASKS)
def request_invoice(client_reference: str, client_name: str, line_description: str, quantity: float,
                    unit_price: float, currency: Currency, reason: str, ctx: Context) -> dict:
    """Ask Mandate to send a PayPal invoice to an existing client, with the right UK VAT.

    client_reference is the client's id (for example C-02). Prices are before VAT: Mandate
    works out the VAT treatment from the client's country and status."""
    return _ask(ctx, action="invoice", reference=client_reference, payee_name=client_name,
                amount=round(quantity * unit_price, 2), quantity=quantity, unit_amount=unit_price,
                currency=currency, description=line_description, reason=reason)


@server.tool(annotations=ASKS)
def request_reminder(invoice_reference: str, reason: str, ctx: Context) -> dict:
    """Ask Mandate to send PayPal's reminder for an unpaid invoice (for example INV-0041).
    PayPal writes the reminder itself and sends at most two a day per invoice."""
    return _ask(ctx, action="reminder", reference=invoice_reference, reason=reason)


@server.tool(annotations=READS)
def check_request(request_id: str) -> dict:
    """See what became of a request you made (its id looks like M1): done, waiting for the
    owner, declined, or failed at PayPal."""
    return _call("POST", "/api/agent/status", {"id": request_id})["request"]
