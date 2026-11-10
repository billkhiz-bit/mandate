from mandate import books
from mandate.vat import Client, treatment_for


def client(country, business=True, vat=None):
    return Client("C", "Client", "c@example.com", country, business, vat)


def test_uk_client_pays_uk_vat():
    t = treatment_for(client("GB"))
    assert (t.code, t.percent) == ("uk_standard", 20.0)


def test_eu_business_is_reverse_charge_with_wording():
    t = treatment_for(client("de", vat="DE999999999"))
    assert (t.code, t.percent) == ("reverse_charge_eu", 0.0)
    assert "Reverse charge" in t.invoice_note and "DE999999999" in t.invoice_note


def test_non_eu_business_is_outside_scope():
    t = treatment_for(client("US"))
    assert (t.code, t.percent) == ("outside_scope", 0.0)


def test_overseas_consumer_pays_uk_vat_for_general_services():
    assert treatment_for(client("FR", business=False)).percent == 20.0


def balanced(lines):
    return round(sum(x.debit for x in lines), 2) == round(sum(x.credit for x in lines), 2)


def test_payout_lines():
    lines = books.payout_lines("2026-11-10", "J-104", "Priya", 420, "GBP")
    assert balanced(lines)
    assert [(x.code, x.debit, x.credit) for x in lines] == [(6002, 420, 0), (1260, 0, 420)]
    assert lines[0].account == "Sub-Contractors"


def test_refund_reverses_sale_and_vat():
    lines = books.refund_lines("2026-11-10", "S-2001", "Sam", 39, "GBP", 20)
    assert balanced(lines)
    assert [(x.code, x.debit) for x in lines[:2]] == [(4000, 32.5), (2200, 6.5)]


def test_invoice_converts_to_gbp_and_skips_vat_when_zero():
    lines = books.invoice_lines("2026-11-10", "LL-0042", "Nordwerk", 6000, "EUR", 0)
    assert balanced(lines)
    assert [x.code for x in lines] == [1100, 4000]
    assert lines[0].debit == 5100.0


def test_uk_invoice_has_output_vat():
    lines = books.invoice_lines("2026-11-10", "LL-0043", "Harbour", 1000, "GBP", 20)
    assert balanced(lines)
    assert [(x.code, x.credit) for x in lines[1:]] == [(4000, 1000), (2200, 200)]
