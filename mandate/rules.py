"""The mandate: the owner's rules, checked by plain code before any money moves.

The AI reads the inbox and proposes actions. This module decides whether each
one may go ahead. Nothing here calls a model, so every decision can be traced,
tested and repeated exactly, and the facts it checks come from the firm's own
records, never from the AI's reading of an email.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

ActionKind = Literal["payout", "refund", "invoice", "reminder"]
ACTION_KINDS: tuple[str, ...] = ("payout", "refund", "invoice", "reminder")
MONEY_OUT: frozenset[str] = frozenset({"payout", "refund"})


@dataclass(frozen=True)
class Rule:
    """One line of the mandate, in a form code can check."""

    id: str
    action: ActionKind
    text: str  # the owner's own words, shown beside every decision it makes
    max_amount_gbp: Optional[float] = None
    known_payees_only: bool = False  # the payee must have been paid before
    requires_sign_off: bool = False  # the job must be signed off in the tracker
    within_days_of_sale: Optional[int] = None
    min_days_overdue: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Rule":
        if data.get("action") not in ACTION_KINDS:
            raise ValueError(f"unknown action in rule {data.get('id')!r}: {data.get('action')!r}")
        return cls(
            id=str(data["id"]),
            action=data["action"],
            text=str(data.get("text", "")),
            max_amount_gbp=_positive_or_none(data.get("max_amount_gbp")),
            known_payees_only=bool(data.get("known_payees_only", False)),
            requires_sign_off=bool(data.get("requires_sign_off", False)),
            within_days_of_sale=_positive_int_or_none(data.get("within_days_of_sale")),
            min_days_overdue=_positive_int_or_none(data.get("min_days_overdue")),
        )


@dataclass(frozen=True)
class Mandate:
    rules: tuple[Rule, ...]
    daily_cap_gbp: Optional[float] = None  # money out per day, across every rule
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "daily_cap_gbp": self.daily_cap_gbp,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Mandate":
        return cls(
            rules=tuple(Rule.from_dict(r) for r in data.get("rules", [])),
            daily_cap_gbp=_positive_or_none(data.get("daily_cap_gbp")),
            text=str(data.get("text", "")),
        )


@dataclass(frozen=True)
class Facts:
    """What the records say about a proposed action. Gathered by the engine
    from the job tracker, sales and invoices: never taken from the AI."""

    amount_gbp: float
    record_found: bool
    record_note: str  # e.g. "matches job J-104 (£420.00 agreed)"
    payee_paid_before: int = 0
    signed_off: bool = False
    days_since_sale: Optional[int] = None
    sale_amount_gbp: Optional[float] = None
    days_overdue: Optional[int] = None
    money_out_today_gbp: float = 0.0
    # Set when the action cannot be carried out correctly at all (already paid,
    # wrong currency, no PayPal capture...). Rules can be overridden by the
    # owner's approval; a blocking reason cannot.
    blocking: str = ""


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool


@dataclass
class Decision:
    allowed: bool
    rule_id: Optional[str]
    rule_text: str
    checks: list[Check] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "rule_id": self.rule_id,
            "rule_text": self.rule_text,
            "summary": self.summary,
            "checks": [{"label": c.label, "ok": c.ok} for c in self.checks],
        }


def evaluate(kind: str, facts: Facts, mandate: Mandate) -> Decision:
    """Decide one proposed action against the mandate.

    An action is allowed only when every check of at least one rule for that
    action passes, together with the checks that apply to every action. When
    none passes, the decision reports the rule that came closest, so the owner
    sees exactly what stopped it.
    """
    if kind not in ACTION_KINDS:
        return Decision(False, None, "", [Check(f"Unknown action '{kind}'", False)],
                        "Mandate doesn't know this kind of action, so it asks you.")

    # Order matters for the owner: the record first, then the rule's own
    # conditions, then the limits that apply to every action.
    record = [Check(facts.record_note, facts.record_found)]
    limits = _limit_checks(kind, facts, mandate)
    candidates = [r for r in mandate.rules if r.action == kind]
    if not candidates:
        return Decision(False, None, "No line of the mandate covers this.", record + limits,
                        f"No rule covers a {kind}, so it waits for you.")

    best: Optional[tuple[int, Rule, list[Check]]] = None
    for rule in candidates:
        checks = record + _rule_checks(rule, facts) + limits
        if all(c.ok for c in checks):
            return Decision(True, rule.id, rule.text, checks,
                            f"Allowed by {rule.id}: {rule.text}")
        passed = sum(c.ok for c in checks)
        if best is None or passed > best[0]:
            best = (passed, rule, checks)

    assert best is not None
    _, rule, checks = best
    failed = sum(not c.ok for c in checks)
    return Decision(False, rule.id, rule.text, checks,
                    f"Outside {rule.id}, so it waits for you. "
                    f"{failed} of {len(checks)} check{'s' if len(checks) != 1 else ''} failed.")


def _limit_checks(kind: str, facts: Facts, mandate: Mandate) -> list[Check]:
    checks: list[Check] = []
    if kind in MONEY_OUT and mandate.daily_cap_gbp is not None:
        total = facts.money_out_today_gbp + facts.amount_gbp
        checks.append(Check(
            f"Money out today would be {_gbp(total)} of the {_gbp(mandate.daily_cap_gbp)} daily limit",
            total <= mandate.daily_cap_gbp + 1e-9,
        ))
    if kind == "refund" and facts.sale_amount_gbp is not None:
        checks.append(Check(
            f"Refund of {_gbp(facts.amount_gbp)} is no more than the original sale ({_gbp(facts.sale_amount_gbp)})",
            facts.amount_gbp <= facts.sale_amount_gbp + 1e-9,
        ))
    return checks


def _rule_checks(rule: Rule, facts: Facts) -> list[Check]:
    checks: list[Check] = []
    if rule.max_amount_gbp is not None:
        within = facts.amount_gbp <= rule.max_amount_gbp + 1e-9
        checks.append(Check(
            f"{_gbp(facts.amount_gbp)} is {'within' if within else 'over'} the {_gbp(rule.max_amount_gbp)} limit",
            within,
        ))
    if rule.known_payees_only:
        n = facts.payee_paid_before
        checks.append(Check(
            f"Payee paid before: {n} time{'s' if n != 1 else ''}" if n else "Payee has never been paid before",
            n > 0,
        ))
    if rule.requires_sign_off:
        checks.append(Check(
            "Job signed off in the job tracker" if facts.signed_off else "Job not signed off in the job tracker",
            facts.signed_off,
        ))
    if rule.within_days_of_sale is not None:
        days = facts.days_since_sale
        ok = days is not None and days <= rule.within_days_of_sale
        checks.append(Check(
            f"Sold {days} days ago (limit {rule.within_days_of_sale})" if days is not None
            else "No sale date on record",
            ok,
        ))
    if rule.min_days_overdue is not None:
        days = facts.days_overdue
        ok = days is not None and days >= rule.min_days_overdue
        checks.append(Check(
            f"{days} days overdue (chase after {rule.min_days_overdue})" if days is not None
            else "No due date on record",
            ok,
        ))
    return checks


def _gbp(value: float) -> str:
    return f"£{value:,.2f}"


def _positive_or_none(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int_or_none(value) -> Optional[int]:
    number = _positive_or_none(value)
    return int(number) if number is not None else None
