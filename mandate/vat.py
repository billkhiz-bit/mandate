"""VAT treatment for the invoices Mandate raises.

Covers general-rule services (consultancy, design, strategy) supplied by a
UK VAT-registered business. Digital downloads to overseas consumers, goods,
land and the other special place-of-supply rules are deliberately out of
scope: Mandate leaves those invoices for a person.

The treatment is decided here by plain code, from the client record. The AI
may describe the work, but it never chooses the VAT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

UK_STANDARD_RATE = 20.0

EU_MEMBER_STATES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    email: str
    country: str  # ISO 3166-1 alpha-2, "GB" for the UK
    is_business: bool
    vat_number: Optional[str] = None


@dataclass(frozen=True)
class VatTreatment:
    code: str  # uk_standard | reverse_charge_eu | outside_scope
    percent: float
    label: str
    invoice_note: str  # printed on the invoice


def treatment_for(client: Client) -> VatTreatment:
    """Place of supply for general-rule services, from the UK supplier's side.

    UK customer: UK VAT at the standard rate.
    Overseas business customer: the supply is made where the customer belongs,
      so it is outside the scope of UK VAT. An EU business accounts for VAT in
      its own country under the reverse charge, and the invoice says so.
    Overseas consumer: general-rule B2C services are supplied where the
      supplier belongs, so UK VAT applies.
    """
    country = client.country.strip().upper()
    if country == "GB":
        return VatTreatment("uk_standard", UK_STANDARD_RATE, "UK VAT at 20%", "")
    if client.is_business and country in EU_MEMBER_STATES:
        return VatTreatment(
            "reverse_charge_eu", 0.0, "Outside the scope of UK VAT (reverse charge)",
            "Outside the scope of UK VAT: the place of supply is the customer's country. "
            "Reverse charge: the customer accounts for any VAT due."
            + (f" Customer VAT number: {client.vat_number}." if client.vat_number else ""),
        )
    if client.is_business:
        return VatTreatment(
            "outside_scope", 0.0, "Outside the scope of UK VAT",
            "Outside the scope of UK VAT: the place of supply is outside the UK.",
        )
    return VatTreatment("uk_standard", UK_STANDARD_RATE, "UK VAT at 20% (overseas consumer)", "")
