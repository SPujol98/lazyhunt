"""Render an interview prep pack as ONE self-contained, mobile-friendly HTML.

No external assets, no scripts: every dynamic string goes through
``html.escape`` and source links are emitted only for http(s) URLs.
"""

import html
import re
from typing import Final

from cva.i18n import LEGACY_LANG, Lang, t
from cva.match.interview_prep import PrepResult
from cva.star import field_label

_CSS: Final[str] = (
    ":root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;"
    "--card:#f4f4f6;--acc:#0a5bd6;--warn:#8a4b00;--warnbg:#fff3d6}"
    "@media(prefers-color-scheme:dark){:root{--bg:#111114;--fg:#ececf0;"
    "--mut:#9a9aa3;--card:#1c1c21;--acc:#6aa5ff;--warn:#ffcf7a;--warnbg:#3a2c0e}}"
    "*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);"
    "color:var(--fg);font:16px/1.5 system-ui,sans-serif;max-width:46rem;"
    "margin-inline:auto}h1{font-size:1.3rem;margin:0 0 4px}"
    "h2{font-size:1.1rem;margin:24px 0 8px}.muted{color:var(--mut);font-size:.9rem}"
    ".card{background:var(--card);border-radius:10px;padding:10px 12px;"
    "margin:8px 0}.warn{background:var(--warnbg);color:var(--warn);"
    "border-radius:10px;padding:10px 12px;margin:12px 0;font-weight:600}"
    "a{color:var(--acc);word-break:break-all}ul{padding-left:1.2rem}"
    "li{margin:4px 0}b{font-weight:650}"
)


def prep_filename(offer_id: int, stage: str, lang: Lang = LEGACY_LANG) -> str:
    return t("panel.prep.file", lang, id=offer_id, stage=stage)


def _stage_label(stage: str, lang: Lang) -> str:
    key = f"panel.prep.stage.{stage}"
    try:
        return t(key, lang)
    except KeyError:
        return stage


def _flag_line(flags: list[str], lang: Lang) -> str:
    return t("panel.prep.flags", lang, flags=", ".join(flags))


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _link(url: str) -> str:
    if re.match(r"https?://", url):
        return f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(url)}</a>'
    return _e(url)


def _section(number: int, title: str, body: str) -> str:
    return f"<section><h2>{number}. {_e(title)}</h2>{body}</section>"


def _cards(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div class="card"><b>{_e(head)}</b><br>{_e(text)}</div>'
        for head, text in items
    )


def render_prep_html(
    result: PrepResult,
    *,
    offer_id: int,
    company: str,
    position: str,
    created_at: str,
    lang: Lang = LEGACY_LANG,
) -> str:
    """The whole pack as one HTML document (in the UI language ``lang``)."""
    pack = result.pack
    label = _stage_label(result.stage, lang)
    parts: list[str] = []
    parts.append(
        _section(
            1,
            t("panel.prep.s1", lang),
            _cards([(i.topic, i.detail) for i in pack.explain]),
        )
    )
    parts.append(
        _section(
            2,
            t("panel.prep.s2", lang),
            f'<p class="muted">{t("panel.prep.topics_note", lang)}</p><ul>'
            + "".join(
                f"<li><b>{_e(t.topic)}</b>{' — ' + _e(t.why) if t.why else ''}</li>"
                for t in pack.topics
            )
            + "</ul>",
        )
    )
    gap_cards = "".join(
        '<div class="card">'
        f"<b>{_e(g.gap)}</b><br>"
        f"{t('panel.prep.acknowledge', lang)}: {_e(g.acknowledge)}"
        + (
            f"<br>{t('panel.prep.adjacent', lang)}: {_e(g.adjacent)}"
            if g.adjacent
            else ""
        )
        + (
            f"<br>{t('panel.prep.learning', lang)}: {_e(g.learning)}"
            if g.learning
            else ""
        )
        + "</div>"
        for g in pack.gaps
    )
    parts.append(
        _section(
            3,
            t("panel.prep.s3", lang),
            gap_cards or f'<p class="muted">{t("panel.prep.no_gaps", lang)}</p>',
        )
    )
    story_cards = []
    for s in result.stories:
        note = (
            ""
            if s.complete
            else (
                f" <i>({t('panel.prep.incomplete', lang)}"
                + (
                    _e(
                        t(
                            "panel.prep.missing",
                            lang,
                            fields=", ".join(field_label(m, lang) for m in s.missing),
                        )
                    )
                    if s.missing
                    else ""
                )
                + f"{t('panel.prep.finish', lang)})</i>"
            )
        )
        why = f"<br>{_e(s.why)}" if s.why else ""
        story_cards.append(f'<div class="card"><b>{_e(s.title)}</b>{note}{why}</div>')
    parts.append(
        _section(
            4,
            t("panel.prep.s4", lang),
            "".join(story_cards)
            or f'<p class="muted">{t("panel.prep.no_stories", lang)}</p>',
        )
    )
    salary = f"<p>{_e(result.salary_text).replace(chr(10), '<br>')}</p>"
    if pack.salary_notes:
        salary += f"<p>{_e(pack.salary_notes)}</p>"
    parts.append(_section(5, t("panel.prep.s5", lang), salary))
    parts.append(
        _section(
            6,
            t("panel.prep.s6", lang),
            "<ul>" + "".join(f"<li>{_e(q)}</li>" for q in pack.questions) + "</ul>",
        )
    )
    if result.facts:
        facts = (
            "<ul>"
            + "".join(
                f'<li>{_e(f.fact)}<br><span class="muted">{_link(f.source_url)}</span></li>'
                for f in result.facts
            )
            + "</ul>"
        )
    else:
        facts = f"<p>{_e(t('panel.prep.no_facts', lang))}</p>"
    parts.append(_section(7, t("panel.prep.s7", lang), facts))
    warn = (
        f'<div class="warn">{_e(_flag_line(result.flags, lang))}</div>'
        if result.flags
        else ""
    )
    head = (
        f"<h1>{_e(t('panel.prep.heading', lang, label=label, id=offer_id))}</h1>"
        f'<p class="muted">{_e(company)} · {_e(position)} · {_e(created_at)}</p>'
    )
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(t('panel.prep.doc_title', lang, id=offer_id, label=label))}</title>"
        f"<style>{_CSS}</style></head><body>{head}{warn}{''.join(parts)}"
        f'<p class="muted">{_e(t("panel.prep.footer", lang))}</p></body></html>'
    )


def prep_summary(
    result: PrepResult,
    *,
    offer_id: int,
    company: str,
    position: str,
    lang: Lang = LEGACY_LANG,
) -> str:
    """Short Telegram message: stage, 3 key topics, flags."""
    label = _stage_label(result.stage, lang)
    topics = "\n".join(f"• {t.topic}" for t in result.pack.topics[:3])
    lines = [
        t("panel.prep.summary_head", lang, id=offer_id, label=label),
        f"{company} · {position}",
        t("panel.prep.summary_topics", lang),
        topics,
    ]
    if result.flags:
        lines.append(_flag_line(result.flags, lang))
    lines.append(t("panel.prep.summary_tail", lang))
    return "\n".join(lines)[:4096]
