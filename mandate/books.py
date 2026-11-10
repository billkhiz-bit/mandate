"""Double-entry journal lines for every action Mandate carries out.

Nominal codes and names come from the UK chart of accounts in the
`uk-chart-of-accounts` library (package `uk_coa`). The firm's PayPal balance
gets its own bank-type account, 1260, because the standard chart has none.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from uk_coa import ChartOfAccounts

from .money import to_gbp

PAYPAL_ACCOUNT = (1260, "PayPal Account")
DEBTORS, SALES_VAT, SALES, SUBCONTRACTORS = 1100, 2200, 4000, 6002

_COA = ChartOfAccounts()


@dataclass(frozen=True)
class Line:
    date: str
    ref: str
    code: int
    account: str
    debit: float
    credit: float
    memo: str

    def to_dict(self) -> dict:
        return asdict(self)


def _name(code: int) -> str:
    if code == PAYPAL_ACCOUNT[0]:
        return PAYPAL_ACCOUNT[1]
    account = _COA.get(code)
    if account is None:
        raise KeyError(f"nominal code {code} is not in the chart")
    return account.name


def _line(date: str, ref: str, code: int, debit: float, credit: float, memo: str) -> Line:
    return Line(date, ref, code, _name(code), round(debit, 2), round(credit, 2), memo)


def payout_lines(date: str, ref: str, payee: str, amount: float, currency: str) -> list[Line]:
    """A freelancer paid from PayPal: cost of sub-contractors, no VAT reclaimed
    (the freelancers in the demo are not VAT registered)."""
    gbp = to_gbp(amount, currency)
    memo = f"Payout to {payee}"
    return [
        _line(date, ref, SUBCONTRACTORS, gbp, 0, memo),
        _line(date, ref, PAYPAL_ACCOUNT[0], 0, gbp, memo),
    ]


def refund_lines(date: str, ref: str, customer: str, amount: float, currency: str,
                 vat_percent: float) -> list[Line]:
    """A refund of a VAT-inclusive sale: reverse the sale and its output VAT."""
    gross = to_gbp(amount, currency)
    vat = round(gross * vat_percent / (100 + vat_percent), 2)
    net = round(gross - vat, 2)
    memo = f"Refund to {customer}"
    lines = [_line(date, ref, SALES, net, 0, memo)]
    if vat:
        lines.append(_line(date, ref, SALES_VAT, vat, 0, memo))
    lines.append(_line(date, ref, PAYPAL_ACCOUNT[0], 0, gross, memo))
    return lines


def invoice_lines(date: str, ref: str, client: str, net: float, currency: str,
                  vat_percent: float) -> list[Line]:
    """An invoice raised: debtor for the gross, sales and output VAT."""
    net_gbp = to_gbp(net, currency)
    vat_gbp = round(net_gbp * vat_percent / 100, 2)
    memo = f"Invoice to {client}"
    lines = [_line(date, ref, DEBTORS, net_gbp + vat_gbp, 0, memo),
             _line(date, ref, SALES, 0, net_gbp, memo)]
    if vat_gbp:
        lines.append(_line(date, ref, SALES_VAT, 0, vat_gbp, memo))
    return lines
