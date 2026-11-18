"""Generic personal-data patterns the public suite can state without naming anyone.

The owner's own identifiers never appear in public tests: they are read at
runtime from the owner's claims by ``tests/owner/test_pii_owner.py``.
"""

import re
from typing import Final

_EMAIL: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})"
)
# International (+34 600 00 00 00) or a 9+ digit run with separators.
_PHONE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[ .-]?\d(?:[ .-]?\d){7,12}|\d{3}[ .-]\d{2,3}[ .-]\d{2,3}[ .-]\d{2,3})(?![\w])"
)
_TRACKING: Final[re.Pattern[str]] = re.compile(
    r"utm_[a-z]+|\b(?:gclid|fbclid|msclkid)\b|[?&;](?:trk|trackingid|tracking_id|clickid)=",
    re.IGNORECASE,
)
_ALLOWED_EMAIL_DOMAIN: Final[str] = "example.com"
# The demo persona's obviously fictional placeholder number (+34 600 000 000).
_ALLOWED_PHONE_DIGITS: Final[str] = "600000000"


def generic_pii_hits(text: str) -> list[str]:
    """Emails other than ``@example.com``, phone numbers and tracking ids in ``text``."""
    hits: list[str] = []
    for match in _EMAIL.finditer(text):
        if match.group(1).lower() != _ALLOWED_EMAIL_DOMAIN:
            hits.append(f"email: {match.group(0)}")
    for match in _PHONE.finditer(text):
        if re.sub(r"\D", "", match.group(0)).endswith(_ALLOWED_PHONE_DIGITS):
            continue
        hits.append(f"phone: {match.group(0)}")
    hits += [f"tracking id: {m.group(0)}" for m in _TRACKING.finditer(text)]
    return hits
