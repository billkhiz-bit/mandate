from mandate.rules import Facts, Mandate, Rule, evaluate
from mandate.seed import SAMPLE_MANDATE


def facts(**kw) -> Facts:
    base = dict(amount_gbp=100.0, record_found=True, record_note="Matches a record")
    base.update(kw)
    return Facts(**base)


def test_routine_payout_is_allowed_by_r1():
    d = evaluate("payout", facts(amount_gbp=420, payee_paid_before=3, signed_off=True), SAMPLE_MANDATE)
    assert d.allowed and d.rule_id == "R1"
    assert all(c.ok for c in d.checks)


def test_payout_to_new_payee_waits_and_says_why():
    d = evaluate("payout", facts(amount_gbp=420, payee_paid_before=0, signed_off=True), SAMPLE_MANDATE)
    assert not d.allowed
    assert [c.label for c in d.checks if not c.ok] == ["Payee has never been paid before"]


def test_unsigned_job_waits():
    d = evaluate("payout", facts(amount_gbp=300, payee_paid_before=1, signed_off=False), SAMPLE_MANDATE)
    assert not d.allowed
    assert [c.label for c in d.checks if not c.ok] == ["Job not signed off in the job tracker"]
    assert d.summary == "Outside R1, so it waits for you. 1 of 5 checks failed."


def test_record_comes_first_and_limits_last():
    d = evaluate("payout", facts(amount_gbp=600, payee_paid_before=0, signed_off=True,
                                 money_out_today_gbp=900), SAMPLE_MANDATE)
    labels = [c.label for c in d.checks]
    assert labels[0] == "Matches a record"
    assert labels[1].endswith("over the £500.00 limit")
    assert labels[-1].startswith("Money out today")


def test_limit_is_inclusive():
    d = evaluate("payout", facts(amount_gbp=500.0, payee_paid_before=1, signed_off=True), SAMPLE_MANDATE)
    assert d.allowed
    d = evaluate("payout", facts(amount_gbp=500.01, payee_paid_before=1, signed_off=True), SAMPLE_MANDATE)
    assert not d.allowed


def test_daily_cap_counts_money_already_out_today():
    ok = facts(amount_gbp=400, payee_paid_before=1, signed_off=True, money_out_today_gbp=600)
    assert evaluate("payout", ok, SAMPLE_MANDATE).allowed
    over = facts(amount_gbp=400, payee_paid_before=1, signed_off=True, money_out_today_gbp=600.01)
    d = evaluate("payout", over, SAMPLE_MANDATE)
    assert not d.allowed and "daily limit" in [c.label for c in d.checks if not c.ok][0]


def test_daily_cap_ignores_invoices():
    assert evaluate("invoice", facts(amount_gbp=50_000, money_out_today_gbp=999), SAMPLE_MANDATE).allowed


def test_missing_record_always_stops_money():
    d = evaluate("payout", facts(amount_gbp=10, record_found=False, record_note="No job",
                                 payee_paid_before=5, signed_off=True), SAMPLE_MANDATE)
    assert not d.allowed


def test_refund_window_and_limit():
    assert evaluate("refund", facts(amount_gbp=39, days_since_sale=1, sale_amount_gbp=39), SAMPLE_MANDATE).allowed
    late = evaluate("refund", facts(amount_gbp=39, days_since_sale=31, sale_amount_gbp=39), SAMPLE_MANDATE)
    assert not late.allowed
    big = evaluate("refund", facts(amount_gbp=149, days_since_sale=2, sale_amount_gbp=149), SAMPLE_MANDATE)
    assert not big.allowed


def test_refund_can_never_exceed_the_sale():
    d = evaluate("refund", facts(amount_gbp=45, days_since_sale=1, sale_amount_gbp=39), SAMPLE_MANDATE)
    assert not d.allowed


def test_reminder_needs_enough_days_overdue():
    assert evaluate("reminder", facts(amount_gbp=0, days_overdue=21), SAMPLE_MANDATE).allowed
    assert not evaluate("reminder", facts(amount_gbp=0, days_overdue=13), SAMPLE_MANDATE).allowed


def test_action_with_no_rule_waits():
    mandate = Mandate(rules=(Rule("R1", "invoice", "Invoice clients."),))
    d = evaluate("payout", facts(), mandate)
    assert not d.allowed and d.rule_id is None


def test_any_matching_rule_is_enough():
    mandate = Mandate(rules=(
        Rule("R1", "payout", "Known payees up to £100.", max_amount_gbp=100, known_payees_only=True),
        Rule("R2", "payout", "Anyone up to £20.", max_amount_gbp=20),
    ))
    d = evaluate("payout", facts(amount_gbp=15, payee_paid_before=0), mandate)
    assert d.allowed and d.rule_id == "R2"


def test_mandate_round_trips_and_rejects_unknown_actions():
    again = Mandate.from_dict(SAMPLE_MANDATE.to_dict())
    assert again == SAMPLE_MANDATE
    bad = SAMPLE_MANDATE.to_dict()
    bad["rules"][0]["action"] = "transfer"
    try:
        Mandate.from_dict(bad)
    except ValueError as exc:
        assert "unknown action" in str(exc)
    else:
        raise AssertionError("an unknown action must be rejected")
