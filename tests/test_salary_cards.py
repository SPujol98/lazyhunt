"""Auto-triage card carries the salary line (salary-estimate T2 wiring)."""

import asyncio
from pathlib import Path

from test_auto_triage import (
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    FetchResponse,
    _bridged,
    _extraction,
    _RecordingNotify,
)

BODY = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "AI Engineer", '
    '"hiringOrganization": {"name": "Acme"}, '
    '"description": "Imprescindible experiencia con Python. '
    'Salario: 30.000 - 35.000 € brutos/año. Barcelona."}'
    "</script></head></html>"
)


def test_bridge_card_shows_published_salary_and_button(tmp_path: Path) -> None:
    service, _repo, offer_id = _bridged(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, BODY, OFFER_URL)),
    )
    notify = _RecordingNotify()
    asyncio.run(
        service.analyze_new_bridge_job(
            offer_id, url=OFFER_URL, title="ML Engineer", company="Acme", notify=notify
        )
    )
    text, keyboard = notify.calls[0]
    assert "💶 Salario publicado: 30.000–35.000 € brutos/año" in text
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"sal:{offer_id}" in data
