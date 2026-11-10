"""Score a brain's answers on the sample inbox against the stored expected answers.

Used by `python app.py check-ai` to see whether live Claude reads the sample
inbox the way the demo needs, before anything touches PayPal. It runs the real
engine, in order, on a throwaway copy of the sample records with the offline
PayPal stand-in, so the daily limit and paid flags behave exactly as in a real
run, and nothing here can drift from the engine's own logic. Your records are
never touched.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from .brain import Brain, OfflineBrain, Proposal
from .engine import Engine
from .paypal import FakePayPal
from .seed import SAMPLE_MANDATE, build_state
from .store import Store


class _Recorder:
    """Passes calls through to a brain and keeps what it proposed for each item."""

    def __init__(self, inner: Brain):
        self.inner = inner
        self.name = inner.name
        self.proposals: dict[str, Proposal] = {}
        self.errors: dict[str, str] = {}

    def propose(self, item: dict, records: dict) -> Proposal:
        try:
            p = self.inner.propose(item, records)
        except Exception as exc:
            self.errors[item["id"]] = f"{exc.__class__.__name__}: {exc}"
            raise
        self.proposals[item["id"]] = p
        return p

    def compile_mandate(self, text, current):
        return self.inner.compile_mandate(text, current)


def _run(brain: Brain, today: date, tmp: Path, name: str) -> dict[str, str]:
    state = build_state(today)
    state["run_id"] = "check"
    engine = Engine(Store(tmp / f"{name}.json", state), brain, FakePayPal(), today=lambda: today)
    inner = brain.inner if isinstance(brain, _Recorder) else brain
    if isinstance(inner, OfflineBrain):  # the engine only binds the lookup for a bare OfflineBrain
        inner.lookup = lambda event_id: next(
            (e.get("demo_proposal") for e in state["inbox"] if e["id"] == event_id), None)
    engine.run_inbox()
    return {e["id"]: e["status"] for e in state["inbox"]}


def score(brain: Brain, today: date) -> list[dict]:
    """One row per sample item: the outcome a real run gives with this brain (done, waiting or
    nothing) against the outcome with the expected answers, plus what differs in the proposal.
    A routine item the AI flags counts as a miss; so does a risk it misses."""
    recorder = _Recorder(brain)
    expected_answers = {e["id"]: Proposal(**e["demo_proposal"]) for e in build_state(today)["inbox"]}
    subjects = {e["id"]: e["subject"] for e in build_state(today)["inbox"]}
    with tempfile.TemporaryDirectory() as tmp:
        want = _run(OfflineBrain(SAMPLE_MANDATE), today, Path(tmp), "expected")
        have = _run(recorder, today, Path(tmp), "actual")

    rows = []
    for event_id, expected in expected_answers.items():
        got = recorder.proposals.get(event_id)
        differences: list[str] = []
        if event_id in recorder.errors:
            differences.append(recorder.errors[event_id])
        elif got is not None:
            if got.action != expected.action:
                differences.append(f"action {got.action}, expected {expected.action}")
            if got.reference != expected.reference:
                differences.append(f"reference {got.reference!r}, expected {expected.reference!r}")
            if expected.action not in ("reminder", "none") and (
                    got.currency != expected.currency or abs(got.amount - expected.amount) > 0.01):
                differences.append(f"amount {got.amount:,.2f} {got.currency}, expected "
                                   f"{expected.amount:,.2f} {expected.currency}")
            if bool(got.risk_flags) != bool(expected.risk_flags):
                differences.append("flagged a routine item" if got.risk_flags else "missed a risk")
        outcome = "error" if event_id in recorder.errors else _label(have.get(event_id, "new"))
        rows.append({"id": event_id, "subject": subjects[event_id], "expected": _label(want[event_id]),
                     "got": outcome, "ok": outcome == _label(want[event_id]) and not differences,
                     "differences": differences, "reasoning": got.reasoning if got else "",
                     "flags": list(got.risk_flags) if got else []})
    return rows


def _label(status: str) -> str:
    return {"done": "done", "waiting": "waiting", "declined": "waiting"}.get(status, status)
