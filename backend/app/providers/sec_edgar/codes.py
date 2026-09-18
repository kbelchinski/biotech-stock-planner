"""Form 3/4/5 transaction codes, verbatim from the SEC's official list (checked 2026-09-18):
https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html

The category is a coarse grouping for filtering and counts; the official label is always shown with the raw code.
Note that P covers open market *or private* purchases: the code alone never proves an open-market trade.
"""

from __future__ import annotations

CODES: dict[str, tuple[str, str]] = {
    # General transaction codes
    "P": ("Open market or private purchase of non-derivative or derivative security", "purchase_open_market_or_private"),
    "S": ("Open market or private sale of non-derivative or derivative security", "sale_open_market_or_private"),
    "V": ("Transaction voluntarily reported earlier than required", "other_coded"),
    # Rule 16b-3 transaction codes
    "A": ("Grant, award or other acquisition pursuant to Rule 16b-3(d)", "grant_or_award"),
    "D": ("Disposition to the issuer of issuer equity securities pursuant to Rule 16b-3(e)", "disposition_to_issuer"),
    "F": (
        "Payment of exercise price or tax liability by delivering or withholding securities incident to the receipt, "
        "exercise or vesting of a security issued in accordance with Rule 16b-3",
        "tax_or_exercise_price_withholding",
    ),
    "I": (
        "Discretionary transaction in accordance with Rule 16b-3(f) resulting in acquisition or disposition of issuer securities",
        "other_coded",
    ),
    "M": ("Exercise or conversion of derivative security exempted pursuant to Rule 16b-3", "derivative_exercise_or_conversion"),
    # Derivative securities codes
    "C": ("Conversion of derivative security", "derivative_exercise_or_conversion"),
    "E": ("Expiration of short derivative position", "other_coded"),
    "H": ("Expiration (or cancellation) of long derivative position with value received", "other_coded"),
    "O": ("Exercise of out-of-the-money derivative security", "derivative_exercise_or_conversion"),
    "X": ("Exercise of in-the-money or at-the-money derivative security", "derivative_exercise_or_conversion"),
    # Other Section 16(b) exempt transaction and small acquisition codes
    "G": ("Bona fide gift", "gift"),
    "L": ("Small acquisition under Rule 16a-6", "other_coded"),
    "W": ("Acquisition or disposition by will or the laws of descent and distribution", "other_coded"),
    "Z": ("Deposit into or withdrawal from voting trust", "other_coded"),
    # Other transaction codes
    "J": ("Other acquisition or disposition (describe transaction)", "other_coded"),
    "K": ("Transaction in equity swap or instrument with similar characteristics", "other_coded"),
    "U": ("Disposition pursuant to a tender of shares in a change of control transaction", "other_coded"),
}


def describe(code: str | None) -> tuple[str | None, str]:
    """(official label, category) for a raw code. Unknown codes keep their raw letter and are not guessed."""
    if not code:
        return None, "unknown"
    code = code.strip().upper()
    if code in CODES:
        return CODES[code]
    return f"Code {code} (not in the SEC code list)", "unknown"
