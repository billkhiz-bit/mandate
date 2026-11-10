"""The loop: read the inbox, propose, check the mandate, act on PayPal, keep the books.

Money moves only when the mandate's rules allow it AND the AI raised no
risk flag. Either can stop a payment; neither can push one through alone.
Everything that stops waits in the owner's approval queue.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import books
from .brain import Brain, BrainUnavailable, OfflineBrain, Proposal, declined as _for_a_person
from .money import fmt, to_gbp
from .paypal import PayPal, PayPalError
from .rules import MONEY_OUT, Decision, Facts, Mandate, evaluate
from .store import Store
from .vat import Client, treatment_for

INVOICE_TERMS_DAYS = 14


class Engine:
    def __init__(self, store: Store, brain: Brain, paypal: PayPal,
                 today: Callable[[], date] = date.today):
        self.store = store
        self.brain = brain
        self.paypal = paypal
        self.today = today
        if isinstance(brain, OfflineBrain):
            brain.lookup = lambda event_id: next(
                (e.get("demo_proposal") for e in self.s["inbox"] if e["id"] == event_id), None)

    @property
    def s(self) -> dict:
        return self.store.state

    def mandate(self) -> Mandate:
        return Mandate.from_dict(self.s["mandate"])

    # -- the inbox ---------------------------------------------------------
    def run_inbox(self) -> dict:
        counts = {"done": 0, "waiting": 0, "nothing": 0}
        for event in self.s["inbox"]:
            if event.get("status", "new") != "new":
                continue
            counts[self._process(event)] += 1
            self.store.save()
        return counts

    def _process(self, event: dict) -> str:
        item = {k: v for k, v in event.items() if k not in ("demo_proposal", "status", "note")}
        try:
            proposal = self.brain.propose(item, self._records_for(event))
        except BrainUnavailable:
            raise  # bad key, no network: stop the run and leave the item new, to retry once fixed
        except Exception as exc:  # noqa: BLE001 - anything else leaves this item for a person
            proposal = _for_a_person(f"The AI couldn't read this item ({exc.__class__.__name__}), so it waits for you.")

        if proposal.action == "none":
            if proposal.risk_flags:  # declined, cut off or unreadable: never let it vanish
                note = proposal.reasoning or proposal.risk_flags[0]
                facts = Facts(amount_gbp=0.0, record_found=False, record_note="Nothing to send: read this one yourself")
                self._queue(event, proposal, facts, Decision(False, None, "", [], note))
                event["status"], event["note"] = "waiting", note
                return "waiting"
            event["status"] = "done"
            event["note"] = proposal.reasoning or "No money action needed."
            return "nothing"

        facts = self._facts(proposal)
        decision = evaluate(proposal.action, facts, self.mandate())
        if decision.allowed and proposal.risk_flags:
            decision.allowed = False
            decision.summary = ("The rules allow this, but the AI flagged it, so it waits for you: "
                                + "; ".join(proposal.risk_flags) + ".")

        if decision.allowed:
            try:
                self._execute(event, proposal, facts, decision, decided_by=decision.rule_id or "Mandate")
                event["status"] = "done"
                return "done"
            except PayPalError as exc:
                decision.summary = f"PayPal didn't accept it, so it waits for you: {exc}"

        self._queue(event, proposal, facts, decision)
        event["status"] = "waiting"
        return "waiting"

    def _records_for(self, event: dict) -> dict:
        refs = event.get("refs", {})
        records: dict = {"owner_on_record": self.s["owner"],
                         "payees_on_record": [{"email": e, **p} for e, p in self.s["payees"].items()]}
        if job := self.s["jobs"].get(refs.get("job", "")):
            records["job"] = {"id": refs["job"], **job}
        if sale := self.s["sales"].get(refs.get("sale", "")):
            records["sale"] = {"id": refs["sale"], **{k: v for k, v in sale.items() if k != "capture_id"}}
        if client := self.s["clients"].get(refs.get("client", "")):
            records["client"] = {"id": refs["client"], **client}
        if inv := self.s["invoices"].get(refs.get("invoice", "")):
            records["invoice"] = {"id": refs["invoice"], **inv,
                                  "client_name": self.s["clients"].get(inv["client"], {}).get("name", "")}
        return records

    # -- facts come from records, never from the AI ------------------------
    def _facts(self, p: Proposal) -> Facts:
        today = self.today()
        amount_gbp = to_gbp(p.amount, p.currency)
        out_today = sum(a["amount_gbp"] for a in self.s["activity"]
                        if a["kind"] in MONEY_OUT and a["outcome"] == "Done" and a["date"] == today.isoformat())
        base = dict(amount_gbp=amount_gbp, money_out_today_gbp=out_today)
        email = p.payee_email.strip().lower()
        live = self.paypal.mode != "offline"

        def stop(note: str, **kw) -> Facts:  # cannot be done at all, approval or not
            return Facts(**base, record_found=False, record_note=note, blocking=note, **kw)

        if p.action != "reminder" and p.amount <= 0:
            return stop("The amount must be more than zero")

        if p.action == "payout":
            paid_before = self.s["payees"].get(email, {}).get("payouts", 0)
            job = self.s["jobs"].get(p.reference)
            if job is None:  # the owner may still approve a payment with no job
                return Facts(**base, record_found=False, record_note="No job in the tracker for this payment",
                             payee_paid_before=paid_before)
            agreed = fmt(job["fee"], job["currency"])
            if job["paid"]:
                return stop(f"Job {p.reference} has already been paid", payee_paid_before=paid_before)
            if email != job["freelancer"]:
                found, note = False, f"Payee differs from the freelancer on job {p.reference}"
            elif p.currency != job["currency"] or abs(p.amount - job["fee"]) > 0.005:
                found, note = False, f"Amount differs from job {p.reference} ({agreed} agreed)"
            else:
                found, note = True, f"Matches job {p.reference} in the tracker ({agreed} agreed)"
            return Facts(**base, record_found=found, record_note=note, payee_paid_before=paid_before,
                         signed_off=bool(job["signed_off"]))

        if p.action == "refund":
            sale = self.s["sales"].get(p.reference)
            if sale is None:
                return stop("No sale on record for this refund")
            remaining = round(sale["amount"] - sale["refunded"], 2)
            days = (today - date.fromisoformat(sale["date"])).days
            sale_facts = dict(days_since_sale=days, sale_amount_gbp=to_gbp(max(remaining, 0), sale["currency"]))
            if remaining <= 0:
                return stop(f"Sale {p.reference} has already been refunded", **sale_facts)
            if p.currency != sale["currency"]:
                return stop(f"Refund is in {p.currency} but sale {p.reference} was in {sale['currency']}",
                            **sale_facts)
            if live and not sale["capture_id"]:
                return stop(f"Sale {p.reference} has no PayPal capture yet (run the sandbox seed)", **sale_facts)
            blocking = (f"Refund is more than the {fmt(remaining, sale['currency'])} left on sale {p.reference}"
                        if p.amount > remaining + 0.005 else "")
            if email != sale["email"].lower():
                found, note = False, f"Refund goes to someone other than the buyer of {p.reference}"
            else:
                found, note = True, f"Matches sale {p.reference}: {sale['item']}, {fmt(sale['amount'], sale['currency'])}"
            return Facts(**base, record_found=found, record_note=note, blocking=blocking, **sale_facts)

        if p.action == "invoice":
            client = self.s["clients"].get(p.reference)
            if client is None:
                return stop("Not an existing client")
            if abs(p.quantity * p.unit_amount - p.amount) > 0.01:
                return stop("Quantity times price doesn't match the invoice total")
            if email and email != client["email"].lower():  # the invoice still goes to the address on record
                return Facts(**base, record_found=False,
                             record_note=f"Email differs from {client['name']}'s address on record")
            return Facts(**base, record_found=True,
                         record_note=f"Existing client {p.reference}: {client['name']} ({client['country']})")

        if p.action == "reminder":
            base["amount_gbp"] = 0.0
            inv = self.s["invoices"].get(p.reference)
            if inv is None:
                return stop("No invoice on record to chase")
            days = (today - date.fromisoformat(inv["due"])).days
            if inv["status"] != "SENT":
                return stop(f"Invoice {inv['number']} is {inv['status'].lower()}, not unpaid", days_overdue=days)
            if live and not inv["paypal_id"]:
                return stop(f"Invoice {inv['number']} isn't in PayPal yet (run the sandbox seed)", days_overdue=days)
            return Facts(**base, record_found=True, record_note=f"Matches unpaid invoice {inv['number']}",
                         days_overdue=days)

        return stop(f"Unknown action {p.action}")

    # -- acting on PayPal --------------------------------------------------
    def _execute(self, event: dict, p: Proposal, facts: Facts, decision: Decision, decided_by: str) -> dict:
        s, today = self.s, self.today().isoformat()
        action_id = f"{s['run_id']}-{event['id']}"
        firm = s["firm"]
        vat_label = ""

        if p.action == "payout":
            result = self.paypal.payout(action_id, p.payee_email, p.amount, p.currency,
                                        note=p.description, subject=f"Payment from {firm}")
            if job := s["jobs"].get(p.reference):  # an owner-approved payout may have no job
                job["paid"] = True
            payee = s["payees"].setdefault(p.payee_email.lower(), {"name": p.payee_name, "payouts": 0})
            payee["payouts"] += 1
            lines = books.payout_lines(today, p.reference, p.payee_name, p.amount, p.currency)

        elif p.action == "refund":
            sale = s["sales"].get(p.reference)
            if sale is None:
                raise PayPalError("Not sent: there's no sale on record to refund.")
            capture_id = sale["capture_id"] or (f"OFFLINE-CAPTURE-{p.reference}" if self.paypal.mode == "offline" else "")
            if not capture_id:
                raise PayPalError(f"Not sent: sale {p.reference} has no PayPal capture yet.")
            result = self.paypal.refund(action_id, capture_id, p.amount, p.currency, note=p.description)
            sale["refunded"] = round(sale["refunded"] + p.amount, 2)
            lines = books.refund_lines(today, p.reference, p.payee_name, p.amount, p.currency,
                                       sale["vat_percent"])

        elif p.action == "invoice":
            c = s["clients"].get(p.reference)
            if c is None:
                raise PayPalError("Not sent: invoices go only to clients on record.")
            client = Client(p.reference, c["name"], c["email"], c["country"], c["is_business"], c.get("vat_number"))
            vat = treatment_for(client)
            vat_label = vat.label
            number = f"LL-{s['next_invoice']:04d}"
            if self.paypal.mode != "offline":
                number += f"-{s['run_id'][:4].upper()}"  # PayPal rejects a reused invoice number
            body = self._invoice_body(number, client, p, vat.percent, vat.invoice_note)
            result = self.paypal.create_and_send_invoice(action_id, body)
            s["next_invoice"] += 1
            vat_amount = round(p.amount * vat.percent / 100, 2)
            s["invoices"][f"INV-{number}"] = {
                "client": p.reference, "number": number, "net": p.amount, "vat": vat_amount,
                "currency": p.currency, "issued": today, "status": "SENT",
                "due": (self.today() + timedelta(days=INVOICE_TERMS_DAYS)).isoformat(),
                "paypal_id": result.get("id", ""), "reminders": 0, "vat_treatment": vat.code}
            lines = books.invoice_lines(today, number, client.name, p.amount, p.currency, vat.percent)

        elif p.action == "reminder":
            inv = s["invoices"].get(p.reference)
            if inv is None:
                raise PayPalError("Not sent: there's no invoice on record to chase.")
            result = self.paypal.remind(action_id, inv["paypal_id"] or p.reference,
                                        subject=f"Reminder: invoice {inv['number']} from {firm}",
                                        note=p.description)
            inv["reminders"] += 1
            lines = []
        else:
            raise PayPalError(f"Unknown action {p.action}")

        for line in lines:  # a unique id per line: identical lines (two equal refunds) stay distinct
            s["journal"].append({"id": f"L{len(s['journal']) + 1:05d}", **line.to_dict()})
        entry = self._entry(event, p, facts, decision, outcome="Done", decided_by=decided_by,
                            paypal=result, vat_label=vat_label)
        s["activity"].append(entry)
        return entry

    def _invoice_body(self, number: str, client: Client, p: Proposal, vat_percent: float, vat_note: str) -> dict:
        item = {"name": p.description[:200] or "Professional services",
                "quantity": f"{p.quantity:g}",
                "unit_amount": {"currency_code": p.currency, "value": f"{p.unit_amount:.2f}"},
                "unit_of_measure": "QUANTITY"}
        if vat_percent:
            item["tax"] = {"name": "VAT", "percent": f"{vat_percent:g}"}
        return {
            "detail": {"invoice_number": number, "invoice_date": self.today().isoformat(),
                       "currency_code": p.currency,
                       # PayPal has no NET_14; 14-day terms are a specified due date
                       "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED",
                                        "due_date": (self.today() + timedelta(days=INVOICE_TERMS_DAYS)).isoformat()},
                       "note": vat_note or "Thank you for your business."},
            "invoicer": {"business_name": self.s["firm"]},
            "primary_recipients": [{"billing_info": {"business_name": client.name,
                                                     "email_address": client.email}}],
            "items": [item],
        }

    # -- the owner's queue ---------------------------------------------------
    def _queue(self, event: dict, p: Proposal, facts: Facts, decision: Decision) -> None:
        entry = self._entry(event, p, facts, decision, outcome="Waiting", decided_by="", paypal={})
        entry["proposal"] = p.model_dump()
        self.s["queue"].append(entry)

    def approve(self, queue_id: str) -> dict:
        item = self._pending(queue_id)
        event = self._event(item["event_id"])
        p = Proposal(**item["proposal"])
        if p.action == "none":
            raise ValueError("There's nothing to send for this item. Read it, then mark it as handled.")
        facts = self._facts(p)  # re-checked now: the records may have changed since it was queued
        if facts.blocking:
            raise PayPalError(f"Not sent: {facts.blocking}.")
        decision = Decision(True, None, "", [], "You approved this.")
        entry = self._execute(event, p, facts, decision, decided_by="You approved")
        item["outcome"] = "Approved"
        event["status"] = "done"
        self.store.save()
        return entry

    def decline(self, queue_id: str) -> dict:
        item = self._pending(queue_id)
        handled = item["kind"] == "none"  # nothing to decline: the owner has dealt with it
        item["outcome"] = "Handled" if handled else "Declined"
        event = self._event(item["event_id"])
        event["status"] = "done" if handled else "declined"
        declined = {**{k: v for k, v in item.items() if k != "proposal"},
                    "outcome": item["outcome"], "decided_by": "You" if handled else "You declined",
                    "date": self.today().isoformat(), "time": datetime.now().strftime("%H:%M")}
        self.s["activity"].append(declined)
        self.store.save()
        return declined

    def _pending(self, queue_id: str) -> dict:
        for item in self.s["queue"]:
            if item["id"] == queue_id and item["outcome"] == "Waiting":
                return item
        raise KeyError(f"No waiting item {queue_id}")

    def _event(self, event_id: str) -> dict:
        return next(e for e in self.s["inbox"] if e["id"] == event_id)

    def _entry(self, event: dict, p: Proposal, facts: Facts, decision: Decision, outcome: str,
               decided_by: str, paypal: dict, vat_label: str = "") -> dict:
        prefix = "Q" if outcome == "Waiting" else "A"
        return {
            "id": f"{prefix}-{event['id']}", "event_id": event["id"], "date": self.today().isoformat(),
            "time": datetime.now().strftime("%H:%M"), "kind": p.action, "payee": p.payee_name,
            "email": p.payee_email, "amount": p.amount, "currency": p.currency,
            "amount_gbp": facts.amount_gbp, "outcome": outcome, "decided_by": decided_by,
            "rule_text": decision.rule_text, "summary": decision.summary, "checks": decision.to_dict()["checks"],
            "reasoning": p.reasoning, "risk_flags": list(p.risk_flags), "reference": p.reference,
            "paypal_id": paypal.get("id", ""), "paypal_status": paypal.get("status", ""), "vat": vat_label,
            "subject": event.get("subject", ""),
        }

    # -- the mandate itself --------------------------------------------------
    def compile(self, text: str) -> dict:
        mandate, unclear = self.brain.compile_mandate(text, self.mandate())
        return {"mandate": mandate.to_dict(), "unclear": unclear}

    def use_mandate(self, data: dict) -> Mandate:
        mandate = Mandate.from_dict(data)
        if not mandate.rules:
            raise ValueError("A mandate needs at least one rule.")
        self.s["mandate"] = mandate.to_dict()
        self.store.save()
        return mandate


def summary_of(state: dict, brain_name: str, paypal_mode: str, today: Optional[date] = None) -> dict:
    """What the dashboard shows: everything but the offline demo answers."""
    inbox = [{k: v for k, v in e.items() if k != "demo_proposal"} for e in state["inbox"]]
    return {
        "firm": state["firm"], "owner": state["owner"], "mandate": state["mandate"], "inbox": inbox,
        "activity": state["activity"], "queue": [q for q in state["queue"] if q["outcome"] == "Waiting"],
        "journal": state["journal"], "ai": brain_name, "paypal": paypal_mode,
        "today": (today or date.today()).isoformat(),
    }
