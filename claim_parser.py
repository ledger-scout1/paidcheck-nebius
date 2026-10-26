"""Deterministic parsing of natural-language "I paid you" claims.

PaidCheck's demo path is **rules/regex based on purpose**: it needs no API
key, no paid LLM call, and behaves identically on every machine — which is
what a judge re-running the demo wants. An optional LLM hook exists behind
the ``PAIDCHECK_LLM_PARSER=1`` env flag and only *fills gaps* the rules
could not resolve; any failure falls back silently to the rules result.

What we extract (see :class:`Claim`):
amount, currency, payer name, payer email, claimed date (+ search window),
item/service description, and an explicit PayPal transaction reference
when the claim happens to include one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

# When a claim names a date, we search +/- this many days around it.
# When it does not, we search the trailing window below.
WINDOW_RADIUS_DAYS = 3
DEFAULT_LOOKBACK_DAYS = 30

_SYMBOL_TO_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_WORD_TO_CURRENCY = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "buck": "USD",
    "bucks": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "quid": "GBP",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# PayPal transaction ids are 17 alphanumerics. Require a letter AND a digit
# so order numbers / zip codes do not get mistaken for one.
_TXREF_RE = re.compile(r"\b(?=[A-Z0-9]{17}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)"
                       r"[A-Z0-9]{17}\b")

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class Claim:
    raw_text: str
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    payer_name: Optional[str] = None
    payer_email: Optional[str] = None
    claimed_date: Optional[datetime] = None
    item: Optional[str] = None
    transaction_ref: Optional[str] = None
    notes: list = field(default_factory=list)

    @property
    def claim_hash(self) -> str:
        """Stable fingerprint of the normalized claim text (dedup key)."""
        normalized = " ".join(self.raw_text.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def window(self, now: Optional[datetime] = None) -> tuple:
        """(start, end) datetimes to search PayPal within, both UTC-aware."""
        now = now or datetime.now(timezone.utc)
        if self.claimed_date is not None:
            start = self.claimed_date - timedelta(days=WINDOW_RADIUS_DAYS)
            end = self.claimed_date + timedelta(days=WINDOW_RADIUS_DAYS)
            return start, min(end, now + timedelta(days=1))
        return now - timedelta(days=DEFAULT_LOOKBACK_DAYS), \
            now + timedelta(days=1)


# --------------------------------------------------------------------------
# amount / currency
# --------------------------------------------------------------------------

def _parse_number(text: str) -> Optional[Decimal]:
    """Parse '1,200.50' (US) and '1.200,50' (EU) style numbers."""
    text = text.strip()
    try:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", text):
            return Decimal(text.replace(".", "").replace(",", "."))
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


_NUM = r"\d[\d,]*(?:\.\d{1,2})?|\d{1,3}(?:\.\d{3})+,\d{1,2}"


def _extract_amount(text: str) -> tuple:
    """Return (amount, currency) — either may be None."""
    # 1) symbol-prefixed:  $180   € 75.00   £45.50
    m = re.search(r"([$€£])\s*(" + _NUM + r")", text)
    if m:
        return _parse_number(m.group(2)), _SYMBOL_TO_CURRENCY[m.group(1)]
    # 2) ISO code before/after:  USD 180 | 180 USD
    m = re.search(r"\b(USD|EUR|GBP)\s*(" + _NUM + r")", text, re.I)
    if m:
        return _parse_number(m.group(2)), m.group(1).upper()
    m = re.search(r"(" + _NUM + r")\s*(USD|EUR|GBP)\b", text, re.I)
    if m:
        return _parse_number(m.group(1)), m.group(2).upper()
    # 3) number + currency word:  180 dollars | 75 euros
    m = re.search(r"(" + _NUM + r")\s+"
                  r"(dollars?|bucks?|euros?|pounds?|quid)\b", text, re.I)
    if m:
        return _parse_number(m.group(1)), \
            _WORD_TO_CURRENCY[m.group(2).lower()]
    # 4) keyword-led bare amount:  paid 180 | amount: 250.00 | sent you 60
    m = re.search(r"(?:paid|sent|payment|amount|total|transferred)"
                  r"(?:\s+you)?\s*[:\-]?\s*(" + _NUM + r")", text, re.I)
    if m:
        return _parse_number(m.group(1)), None
    # 5) last resort: first standalone decimal number
    m = re.search(r"\b(" + _NUM + r")\b", text)
    if m and "." in m.group(1):
        return _parse_number(m.group(1)), None
    return None, None


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

def _mk_date(year: int, month: int, day: int,
             now: datetime) -> Optional[datetime]:
    try:
        dt = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt


def _extract_date(text: str, now: datetime) -> Optional[datetime]:
    lower = text.lower()

    m = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        return _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        now)
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if m:  # assume day/month/year when the first field can't be a month
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day, month = (a, b) if a > 12 else (b, a) if b > 12 else (a, b)
        return _mk_date(y, month, day, now)
    # "October 5, 2026" / "5 October 2026" / "Oct 5" (year = current)
    month_alt = "|".join(sorted(_MONTHS, key=len, reverse=True))
    m = re.search(r"\b(" + month_alt + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                  r"(?:,?\s+(\d{4}))?\b", lower)
    if m:
        year = int(m.group(3)) if m.group(3) else now.year
        return _mk_date(year, _MONTHS[m.group(1)], int(m.group(2)), now)
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + month_alt + r")\.?"
                  r"(?:\s+(\d{4}))?\b", lower)
    if m:
        year = int(m.group(3)) if m.group(3) else now.year
        return _mk_date(year, _MONTHS[m.group(2)], int(m.group(1)), now)
    # relative expressions
    if re.search(r"\btoday\b", lower):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\byesterday\b", lower):
        return (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"\b(\d+)\s+days?\s+ago\b", lower)
    if m:
        return (now - timedelta(days=int(m.group(1)))).replace(
            hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\blast\s+week\b", lower):
        return (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\blast\s+month\b", lower):
        return (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"\b(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday)\b", lower)
    if m:  # most recent past occurrence of that weekday
        target = _WEEKDAYS[m.group(1)]
        delta = (now.weekday() - target) % 7
        return (now - timedelta(days=delta)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    return None


# --------------------------------------------------------------------------
# payer / item
# --------------------------------------------------------------------------

_NAME = r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ.'-]*(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ.'-]*){0,2})"
_TRAILING_JUNK = re.compile(
    r"\s+(?:for|on|via|through|using|yesterday|today|last|the|a|an)$", re.I)


def _clean_name(name: str) -> Optional[str]:
    name = name.strip().strip(",.;:")
    prev = None
    while prev != name:  # strip trailing filler words, e.g. "John Smith for"
        prev = name
        name = _TRAILING_JUNK.sub("", name).strip()
    if not name or len(name) < 2:
        return None
    if name.lower() in {"paypal", "venmo", "cash", "card", "bank"}:
        return None
    return name


def _extract_payer_name(text: str) -> Optional[str]:
    patterns = [
        r"(?:payment|paid|sent|transfer)\s+(?:from|by)\s+" + _NAME,
        r"(?:from|by|sender)[:\s]\s*" + _NAME,
        r"^" + _NAME + r"\s+(?:paid|sent|here)",
        r"[-—]\s*" + _NAME + r"\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.M)
        if m:
            cleaned = _clean_name(m.group(1))
            if cleaned:
                return cleaned
    return None


def _extract_item(text: str) -> Optional[str]:
    m = re.search(r"(?:payment\s+)?for\s+(?:the\s+)?(.+?)(?:\.|;|\n|"
                  r" from | on \d| - |$)", text, re.I)
    if m:
        item = m.group(1).strip().strip(",")
        if 2 <= len(item) <= 80:
            return item
    return None


# --------------------------------------------------------------------------
# optional LLM gap-filler (off by default)
# --------------------------------------------------------------------------

def _llm_fill_gaps(claim: Claim) -> None:  # pragma: no cover - opt-in path
    """Best-effort LLM assist; mutates claim only where fields are missing.

    Activated only with PAIDCHECK_LLM_PARSER=1 and OPENAI_API_KEY set.
    Any error leaves the deterministic result untouched.
    """
    import json
    import os

    if os.environ.get("PAIDCHECK_LLM_PARSER") != "1":
        return
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return
    try:
        import requests
        prompt = (
            "Extract payment-claim fields as JSON with keys amount, "
            "currency, payer_name, payer_email, item. Use null when "
            f"absent. Text:\n{claim.raw_text}")
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "gpt-4o-mini",
                  "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}},
            timeout=20)
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
        if claim.amount is None and data.get("amount") is not None:
            claim.amount = Decimal(str(data["amount"]))
            claim.notes.append("amount filled by optional LLM assist")
        if claim.currency is None and data.get("currency"):
            claim.currency = str(data["currency"]).upper()
        if claim.payer_name is None and data.get("payer_name"):
            claim.payer_name = str(data["payer_name"])
        if claim.item is None and data.get("item"):
            claim.item = str(data["item"])
    except Exception:
        pass  # the deterministic parse stands on its own


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def parse_claim(text: str, *, now: Optional[datetime] = None,
                use_llm: Optional[bool] = None) -> Claim:
    """Parse a pasted claim message into a structured :class:`Claim`."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    claim = Claim(raw_text=text or "")

    amount, currency = _extract_amount(claim.raw_text)
    claim.amount = amount
    claim.currency = currency
    if amount is None:
        claim.notes.append("no amount found")
    if currency is None and amount is not None:
        claim.notes.append("no currency found (matcher will not assume one)")

    m = _EMAIL_RE.search(claim.raw_text)
    if m:
        claim.payer_email = m.group(0)
    claim.payer_name = _extract_payer_name(claim.raw_text)
    claim.claimed_date = _extract_date(claim.raw_text, now)
    if claim.claimed_date is None:
        claim.notes.append("no date found (searching the default window)")
    claim.item = _extract_item(claim.raw_text)
    m = _TXREF_RE.search(claim.raw_text.upper())
    if m:
        claim.transaction_ref = m.group(0)

    import os
    if use_llm is None:
        use_llm = os.environ.get("PAIDCHECK_LLM_PARSER") == "1"
    if use_llm:
        _llm_fill_gaps(claim)
    return claim
