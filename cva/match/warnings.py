"""Offer-card warnings from the ``[search]`` preferences (onboarding-v2 S4).

"Avisar en la tarjeta": an offer whose work mode or published salary does not
fit what the profile accepts still arrives; its card carries one ⚠️ line per
mismatch with the reason. Pure and deterministic: the cards recompute the
lines from the stored offer and the live preferences each time they render,
so nothing is persisted and neither the score nor the gates change.

- Work mode: the location gate's reading (:func:`cva.match.gates.work_mode`,
  benefits like "teletrabajo" among perks do not make an office job remote).
  Only a clear remote/hybrid/on-site mode outside ``work_modes`` warns; mixed
  or unknown wording never does.
- Salary: only a salary the offer states (:func:`extract_published_salary`,
  euros gross per year) whose upper bound is below ``min_salary``, and only
  when the profile's currency is EUR (the extractor reads euros, and amounts
  without a currency mark are taken as euros). No salary: no warning.
"""

from collections.abc import Sequence

from cva.i18n import Lang, join_list, t
from cva.match.gates import work_mode
from cva.match.salary import extract_published_salary
from cva.match.salary_reference import eur_text

_CLEAR_MODES = ("remote", "hybrid", "onsite")
_SALARY_CURRENCY = "EUR"


def offer_warnings(
    text: str | None,
    location: str | None,
    *,
    work_modes: Sequence[str],
    min_salary: int | None,
    currency: str,
    lang: Lang,
) -> list[str]:
    """The ⚠️ card lines (UI language ``lang``) for ``text``; ``[]`` when the
    offer fits or nothing can be told."""
    if not text:
        return []
    lines: list[str] = []
    mode = work_mode(text, location)
    if mode in _CLEAR_MODES and mode not in work_modes:
        accepted = [
            t(f"notifier.warn.mode_name.{m}", lang)
            for m in _CLEAR_MODES
            if m in work_modes
        ]
        lines.append(
            t(
                "notifier.warn.work_mode",
                lang,
                mode=t(f"notifier.warn.mode.{mode}", lang),
                accepted=join_list(accepted, lang),
            )
        )
    if min_salary is not None and currency == _SALARY_CURRENCY:
        published = extract_published_salary(text, lang=lang)
        if published is not None and published.max_eur < min_salary:
            lines.append(
                t(
                    "notifier.warn.salary",
                    lang,
                    minimum=eur_text(min_salary),
                    amount=eur_text(published.max_eur),
                )
            )
    return lines
