"""Currency conversion for the mandate's GBP limits and the books.

The rates are fixed demo rates, not market data, so a run is repeatable and
every figure on screen can be checked by hand. Swap in a rates feed before
using this for real money.
"""

from __future__ import annotations

DEMO_GBP_PER_UNIT: dict[str, float] = {"GBP": 1.0, "EUR": 0.85, "USD": 0.78}
SUPPORTED = tuple(DEMO_GBP_PER_UNIT)


def to_gbp(amount: float, currency: str) -> float:
    try:
        rate = DEMO_GBP_PER_UNIT[currency.upper()]
    except KeyError:
        raise ValueError(f"unsupported currency {currency!r}; expected one of {SUPPORTED}") from None
    return round(amount * rate, 2)


def fmt(amount: float, currency: str) -> str:
    symbol = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency.upper(), currency + " ")
    return f"{symbol}{amount:,.2f}"
