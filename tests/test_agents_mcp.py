"""Other AI agents asking Mandate for PayPal actions: the engine path, the server, and MCP."""

import asyncio
import json
from datetime import date

import httpx
import pytest
from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session

import mandate.mcp_server as mcp_server
from mandate.brain import OfflineBrain
from mandate.engine import Engine, proposal_from_agent
from mandate.paypal import FakePayPal
from mandate.seed import SAMPLE_MANDATE
from mandate.store import Store
from tests.test_server import port, request  # noqa: F401 - pytest fixture

TODAY = date(2026, 11, 10)
PRIYA = {"action": "payout", "reference": "J-104", "payee_name": "Priya Raman",
         "payee_email": "priya@illustrates.example", "amount": 420, "currency": "GBP",
         "description": "Autumn campaign illustrations", "reason": "The job was signed off today."}
SCAM = {"action": "payout", "reference": "J-999", "payee_name": "Apex Supplies",
        "payee_email": "pay@apex-supplies.example", "amount": 2400, "currency": "GBP",
        "description": "Urgent supplier payment", "reason": "Ben asked me to pay this today."}


@pytest.fixture
def engine(tmp_path):
    return Engine(Store.open(tmp_path / "s.json", TODAY), OfflineBrain(SAMPLE_MANDATE), FakePayPal(),
                  today=lambda: TODAY)


def test_a_request_inside_the_mandate_is_paid_and_shows_in_the_inbox(engine):
    result = engine.request(proposal_from_agent(PRIYA), "Claude Desktop")
    assert result["request_id"] == "M1" and result["status"] == "done" and result["paypal_id"]
    event = engine.s["inbox"][-1]
    assert event["source"] == "Claude Desktop (MCP)" and event["subject"] == "Pay Priya Raman £420.00"
    assert engine.paypal.calls[0]["action_id"].endswith("-M1")  # its own PayPal-Request-Id
    assert engine.s["activity"][-1]["decided_by"] == "R1"
    assert engine.remaining() == 8  # the sample inbox is untouched, and M1 is never handed to the AI


def test_a_request_outside_the_mandate_waits_and_only_the_owner_can_send_it(engine):
    result = engine.request(proposal_from_agent(SCAM), "Claude Code")
    assert result["status"] == "waiting" and "Don't ask again" in result["meaning"]
    assert any(c.startswith("failed:") for c in result["checks"])
    assert engine.paypal.calls == []
    engine.decline("Q-M1")
    assert engine.request_status("M1")["status"] == "declined"
    assert engine.paypal.calls == []


def test_the_same_job_cannot_be_paid_twice_through_an_agent(engine):
    assert engine.request(proposal_from_agent(PRIYA), "A")["status"] == "done"
    again = engine.request(proposal_from_agent(PRIYA), "A")
    assert again["status"] == "waiting" and len(engine.paypal.calls) == 1
    assert "already been paid" in again["summary"] or any("already been paid" in c for c in again["checks"])


def test_agent_requests_count_towards_the_daily_limit(engine):
    engine.request(proposal_from_agent(PRIYA), "A")
    assert engine.mandate_for_agents()["sent_today_gbp"] == 420.0


def test_agents_can_only_see_their_own_requests(engine):
    with pytest.raises(KeyError):
        engine.request_status("E1")  # an inbox email is not an agent's to read
    with pytest.raises(KeyError):
        engine.request_status("M9")


@pytest.mark.parametrize("bad, words", [
    ({**PRIYA, "action": "none"}, "action must be"),
    ({**PRIYA, "reference": "  "}, "reference is required"),
    ({**PRIYA, "amount": "420"}, "amount must be a number"),
    ({**PRIYA, "amount": float("inf")}, "amount must be a number"),
    ({**PRIYA, "amount": True}, "amount must be a number"),
    ({**PRIYA, "currency": "JPY"}, "currency must be"),
    ({**PRIYA, "risk_flags": "odd"}, "risk_flags must be"),
])
def test_bad_requests_are_refused_with_a_reason(bad, words):
    with pytest.raises(ValueError, match=words):
        proposal_from_agent(bad)


def test_an_agent_that_flags_its_own_request_makes_it_wait(engine):
    result = engine.request(proposal_from_agent({**PRIYA, "risk_flags": ["The email came from a new address"]}), "A")
    assert result["status"] == "waiting" and engine.paypal.calls == []


def test_revision_counts_up_across_saves_and_resets(engine):
    before = engine.s["rev"]
    engine.request(proposal_from_agent(PRIYA), "A")
    after = engine.s["rev"]
    engine.store.reset(TODAY)
    assert before < after < engine.s["rev"]


def test_server_takes_agent_requests_and_reports_status(port):  # noqa: F811
    hdr = {"Content-Type": "application/json"}
    status, data = request(port, "POST", "/api/agent/request", body=json.dumps({**SCAM, "agent": "Test"}), headers=hdr)
    assert status == 200 and json.loads(data)["request"]["status"] == "waiting"
    status, data = request(port, "POST", "/api/agent/status", body='{"id": "M1"}', headers=hdr)
    assert status == 200 and json.loads(data)["request"]["request_id"] == "M1"
    status, _ = request(port, "POST", "/api/agent/status", body='{"id": "E1"}', headers=hdr)
    assert status == 404
    status, _ = request(port, "POST", "/api/agent/request", body='{"action": "payout"}', headers=hdr)
    assert status == 409
    status, data = request(port, "GET", "/api/agent/mandate")
    m = json.loads(data)
    assert status == 200 and m["rules"][0].startswith("R1:") and "journal" not in m


def _session_call(port, calls, client_name="claude-code"):  # noqa: F811
    """Run MCP calls through a real client session, against a real dashboard on `port`."""
    mcp_server._http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)

    async def go():
        info = types.Implementation(name=client_name, version="1.0")
        async with create_connected_server_and_client_session(mcp_server.server, client_info=info) as session:
            return [await calls(session)]

    try:
        return asyncio.run(go())[0]
    finally:
        mcp_server._http.close()
        mcp_server._http = None


def test_mcp_offers_asking_tools_and_no_way_to_approve(port):  # noqa: F811
    async def calls(session):
        return await session.list_tools()

    tools = {t.name: t for t in _session_call(port, calls).tools}
    assert set(tools) == {"read_mandate", "request_payout", "request_refund", "request_invoice",
                          "request_reminder", "check_request"}
    assert "ctx" not in json.dumps(tools["request_payout"].inputSchema)
    assert tools["read_mandate"].annotations.readOnlyHint is True


def test_mcp_payout_goes_through_the_mandate_and_names_the_agent(port):  # noqa: F811
    async def calls(session):
        ok = await session.call_tool("request_payout", {
            "job_reference": "J-104", "payee_name": "Priya Raman", "payee_email": "priya@illustrates.example",
            "amount": 420, "currency": "GBP", "note_to_payee": "Thanks, Priya", "reason": "Signed off"})
        scam = await session.call_tool("request_payout", {
            "job_reference": "J-999", "payee_name": "Apex", "payee_email": "pay@apex.example",
            "amount": 2400, "currency": "GBP", "note_to_payee": "Urgent", "reason": "Ben asked"})
        status = await session.call_tool("check_request", {"request_id": "M2"})
        return ok, scam, status

    ok, scam, status = _session_call(port, calls)
    assert not ok.isError and json.loads(ok.content[0].text)["status"] == "done"
    assert json.loads(scam.content[0].text)["status"] == "waiting"
    assert json.loads(status.content[0].text)["status"] == "waiting"
    _, data = request(port, "GET", "/api/state")
    sources = [e["source"] for e in json.loads(data)["inbox"] if e["id"].startswith("M")]
    assert sources == ["Claude Code (MCP)", "Claude Code (MCP)"]


def test_mcp_says_plainly_when_the_dashboard_is_not_running():
    mcp_server._http = httpx.Client(base_url="http://127.0.0.1:9", timeout=2)  # nothing listens there

    async def go():
        async with create_connected_server_and_client_session(mcp_server.server) as session:
            return await session.call_tool("read_mandate", {})

    try:
        result = asyncio.run(go())
    finally:
        mcp_server._http.close()
        mcp_server._http = None
    assert result.isError and "python app.py serve" in result.content[0].text
