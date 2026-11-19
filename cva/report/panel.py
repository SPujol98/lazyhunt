"""``/panel``: ONE self-contained HTML dashboard of the tracker (pure renderer).

``render_panel(rows, now)`` has no I/O: the same rows as the synced xlsx go in,
a single HTML string comes out (inline CSS/JS/SVG only; no external request of
any kind, so it works offline from a phone or a synced folder). Offer data is
third-party text: server-side pieces (charts, KPIs, alerts) go through
``html.escape``; the table is built client-side from a JSON blob with
``textContent`` only (never ``innerHTML``), and the blob escapes every ``<`` so
it cannot close its ``<script>`` element.
"""

import contextlib
import html
import json
import os
import stat
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.i18n import LEGACY_LANG, Lang, t
from cva.match.gates import gate_reason
from cva.match.stack_wall import stack_wall_display
from cva.tracker.repo import OfferRow
from cva.tracker.xlsx import ESTADO_FILLS
from cva.tracker.xlsx import _is_sent as is_sent

PANEL_FILENAME: Final[str] = "panel_candidaturas.html"
DEADLINE_DAYS: Final[int] = 7
SILENT_DAYS: Final[int] = 10
WEEKS_SHOWN: Final[int] = 8

_PRE_SEND: Final[frozenset[str]] = frozenset(
    {"pendiente_analisis", "analizada", "preparada"}
)
# Funnel stages are cumulative by estado: an offer that reached a later stage
# counts in every earlier one.
_RESPONDED: Final[frozenset[str]] = frozenset(
    {
        "screening",
        "entrevista_tecnica",
        "entrevista_final",
        "oferta",
        "aceptada",
        "rechazada",
    }
)
_INTERVIEW: Final[frozenset[str]] = frozenset(
    {"entrevista_tecnica", "entrevista_final", "oferta", "aceptada"}
)
_OFFER: Final[frozenset[str]] = frozenset({"oferta", "aceptada"})
_MATCH_BUCKETS: Final[tuple[tuple[str, int, int], ...]] = (
    ("0-49", 0, 49),
    ("50-64", 50, 64),
    ("65-79", 65, 79),
    ("80-100", 80, 100),
)
_NO_VALUE: Final[str] = "—"
DISCARDED_DAYS: Final[int] = 7
NEAR_THRESHOLD_POINTS: Final[int] = 10
REASON_MAX: Final[int] = 120


@dataclass(frozen=True)
class DiscardedOffer:
    """One offer the bot analysed this week but never sent a card for."""

    offer_id: int
    title: str
    company: str
    portal: str
    url: str | None
    location: str | None
    kind: Literal["below", "walled"]
    score: int
    threshold: int
    profile: str
    reason: str
    days_ago: int


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _safe_url(url: str | None) -> str:
    """Only http(s) links survive (blocks ``javascript:`` and friends)."""
    text = (url or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def _bar_chart(
    items: Sequence[tuple[str, int, str | None]],
    title: str,
    lang: Lang = LEGACY_LANG,
) -> str:
    """Horizontal inline-SVG bars: (label, value, fill colour or None)."""
    if not items:
        return f'<p class="muted">{t("panel.no_data", lang)}</p>'
    peak = max(value for _, value, _ in items) or 1
    row_h, label_w, bar_w, width = 24, 112, 150, 320
    parts = [
        (
            f'<svg viewBox="0 0 {width} {row_h * len(items)}" role="img" '
            f'aria-label="{html.escape(title)}">'
        )
    ]
    for index, (label, value, colour) in enumerate(items):
        y = index * row_h
        w = max(2, round(bar_w * value / peak)) if value else 0
        fill = f"#{colour}" if colour else "var(--accent)"
        shown = label if len(label) <= 16 else label[:15] + "…"
        parts.append(
            f'<text x="0" y="{y + 16}" class="lbl">{html.escape(shown)}</text>'
            f'<rect x="{label_w}" y="{y + 4}" width="{w}" height="16" rx="3" '
            f'fill="{fill}" class="bar"/>'
            f'<text x="{label_w + w + 6}" y="{y + 16}" class="val">{value}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else _NO_VALUE


def _estado_label(estado: str, lang: Lang = LEGACY_LANG) -> str:
    key = f"panel.estado.{estado}"
    try:
        return t(key, lang)
    except KeyError:
        return estado.replace("_", " ")


def _week_key(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def compute_stats(rows: list[OfferRow], now: datetime) -> dict[str, object]:
    """Aggregates + alerts shown by the panel (kept separate for testing)."""
    today = now.date()
    analyzed = [r for r in rows if r.estado != "pendiente_analisis"]
    sent = [r for r in rows if is_sent(r)]
    responded = [r for r in sent if r.estado in _RESPONDED]
    interviews = [r for r in sent if r.estado in _INTERVIEW]
    offers = [r for r in sent if r.estado in _OFFER]

    by_estado = Counter(r.estado for r in rows)
    by_origen = Counter((r.origen or "").strip() or _NO_VALUE for r in rows)
    week_counts: Counter[str] = Counter()
    for r in sent:
        if (sent_day := _day(r.fecha_envio)) is not None:
            week_counts[_week_key(sent_day)] += 1
    weeks = [
        _week_key(today - timedelta(weeks=n)) for n in range(WEEKS_SHOWN - 1, -1, -1)
    ]
    buckets = {label: 0 for label, _, _ in _MATCH_BUCKETS}
    for r in rows:
        if r.match is None:
            continue
        for label, low, high in _MATCH_BUCKETS:
            if low <= r.match <= high:
                buckets[label] += 1
                break

    deadlines: list[tuple[int, OfferRow]] = []
    for r in rows:
        due = _day(r.fecha_limite)
        if r.estado in _PRE_SEND and due is not None:
            left = (due - today).days
            if 0 <= left <= DEADLINE_DAYS:
                deadlines.append((left, r))
    deadlines.sort(key=lambda pair: (pair[0], pair[1].id))
    silent: list[tuple[int, OfferRow]] = []
    for r in rows:
        if r.estado != "enviada":
            continue
        last = _day(r.fecha_update) or _day(r.fecha_envio)
        if last is not None and (today - last).days >= SILENT_DAYS:
            silent.append(((today - last).days, r))
    silent.sort(key=lambda pair: (-pair[0], pair[1].id))

    return {
        "analyzed": len(analyzed),
        "sent": len(sent),
        "responded": len(responded),
        "interviews": len(interviews),
        "offers": len(offers),
        "response_rate": _pct(len(responded), len(sent)),
        "by_estado": dict(by_estado),
        "by_origen": dict(by_origen),
        "by_week": {week: week_counts.get(week, 0) for week in weeks},
        "by_match": buckets,
        "deadline_alerts": deadlines,
        "silent_alerts": silent,
    }


def _deadline_text(days: int, lang: Lang) -> str:
    key = "today" if days == 0 else "one" if days == 1 else "many"
    return t(f"panel.deadline_{key}", lang, n=days)


def _alert_items(
    pairs: list[tuple[int, OfferRow]], render: Callable[[int], str]
) -> str:
    items = []
    for number, r in pairs:
        who = f"#{r.id} {r.empresa or _NO_VALUE} — {r.posicion or _NO_VALUE}"
        items.append(f"<li>{html.escape(who)} · {render(number)}</li>")
    return "".join(items)


def _family_tag(
    profile: str, families: FamilyRegistry, lang: Lang = LEGACY_LANG
) -> str:
    """The family label shown on the panel (unknown: the default family).

    In Spanish the label ``AI`` (the built-in ``ai``) reads "IA".
    """
    family = families.get(families.normalize(profile)) or families.default
    return t("panel.family_ai", lang) if family.label == "AI" else family.label


def _json_blob(rows: list[OfferRow], default_profile: str = "ai") -> str:
    data = [
        {
            "id": r.id,
            "empresa": r.empresa or "",
            "posicion": r.posicion or "",
            "perfil": r.perfil or default_profile,
            "portal": (r.origen or "").strip() or _NO_VALUE,
            "estado": r.estado,
            "match": r.match,
            "envio": (r.fecha_envio or "")[:10],
            "update": (r.fecha_update or "")[:10],
            "url": _safe_url(r.url_oferta),
        }
        for r in rows
    ]
    # ensure_ascii covers U+2028/9; escaping every "<" (and "&", ">") makes a
    # closing </script> or <!-- impossible inside the blob.
    return (
        json.dumps(data, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _estado_css() -> str:
    return "".join(
        f".e-{name}{{background:#{colour}}}" for name, colour in ESTADO_FILLS.items()
    )


_CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#1c1f24;--card:#fff;--line:#d9dde3;
--muted:#667085;--accent:#3b82f6}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e8eaed;--card:#1d2127;
--line:#333a44;--muted:#9aa4b2;--accent:#6ea8fe}}
*{box-sizing:border-box}
body{margin:0;padding:12px;background:var(--bg);color:var(--fg);
font:15px/1.4 system-ui,sans-serif}
h1{font-size:1.25rem;margin:0 0 4px}h2{font-size:1rem;margin:0 0 8px}
.muted{color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:12px 0}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px}
.tile b{display:block;font-size:1.6rem}
.grid{display:grid;grid-template-columns:1fr;gap:8px;margin-bottom:12px}
svg{width:100%;height:auto;display:block}
svg .lbl,svg .val{fill:var(--fg);font-size:11px}
svg .bar{stroke:var(--line);stroke-width:1}
.alerts li{margin:2px 0}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.filters input,.filters select{flex:1 1 140px;padding:8px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--fg);font:inherit}
.tablewrap{overflow-x:auto}
summary{cursor:pointer}details[open]>summary{margin-bottom:8px}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
white-space:nowrap}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;color:#1c1f24;
border:1px solid rgba(0,0,0,.15);font-size:.8rem}
a{color:var(--accent)}
@media (min-width:720px){.kpis{grid-template-columns:repeat(5,1fr)}
.grid{grid-template-columns:repeat(2,1fr)}body{padding:20px;max-width:1100px;margin:auto}}
"""

_JS = """
(function(){
var rows=JSON.parse(document.getElementById('panel-data').textContent);
var body=document.getElementById('rows');
var q=document.getElementById('q'),fe=document.getElementById('f-estado'),
fp=document.getElementById('f-perfil'),fo=document.getElementById('f-portal');
function fill(sel,key){var seen={};rows.forEach(function(r){seen[r[key]]=1});
Object.keys(seen).sort().forEach(function(v){var o=document.createElement('option');
o.value=v;o.textContent=@@LABEL:v@@;sel.appendChild(o)})}
fill(fe,'estado');fill(fp,'perfil');fill(fo,'portal');
function cell(tr,text){var td=document.createElement('td');td.textContent=text;
tr.appendChild(td);return td}
function draw(){var t=q.value.trim().toLowerCase();body.textContent='';var n=0;
rows.forEach(function(r){
if(fe.value&&r.estado!==fe.value)return;if(fp.value&&r.perfil!==fp.value)return;
if(fo.value&&r.portal!==fo.value)return;
if(t&&(r.empresa+' '+r.posicion+' '+r.portal+' '+r.estado).toLowerCase().indexOf(t)<0)return;
n++;var tr=document.createElement('tr');cell(tr,'#'+r.id);cell(tr,r.empresa);
var td=cell(tr,'');
if(r.url){var a=document.createElement('a');a.textContent=r.posicion||'@@OFFER@@';
a.setAttribute('href',r.url);a.setAttribute('rel','noopener noreferrer');
a.setAttribute('target','_blank');td.appendChild(a)}else{td.textContent=r.posicion}
cell(tr,r.perfil);cell(tr,r.portal);
var s=cell(tr,'');var c=document.createElement('span');
c.className='chip e-'+r.estado.replace(/[^a-z_]/g,'');
c.textContent=@@LABEL:r.estado@@;s.appendChild(c);
cell(tr,r.match===null?'—':String(r.match));cell(tr,r.envio);cell(tr,r.update);
body.appendChild(tr)});
document.getElementById('count').textContent=n+'@@OF@@'+rows.length}
[q,fe,fp,fo].forEach(function(el){el.addEventListener('input',draw)});draw()})();
"""


def _js(lang: Lang) -> str:
    """The table script; estado labels follow ``lang`` (Spanish keeps the raw
    value with underscores replaced, as before)."""
    labels = {
        name: _estado_label(name, lang)
        for name in ESTADO_FILLS
        if _estado_label(name, lang) != name.replace("_", " ")
    }
    prefix = f"var EL={json.dumps(labels, ensure_ascii=True)};" if labels else ""

    def label(expr: str) -> str:
        plain = f"{expr}.replace(/_/g,' ')"
        return f"(EL[{expr}]||{plain})" if labels else plain

    script = (
        _JS.replace("@@LABEL:v@@", label("v"))
        .replace("@@LABEL:r.estado@@", label("r.estado"))
        .replace("@@OFFER@@", t("panel.js.offer", lang))
        .replace("@@OF@@", t("panel.js.of", lang))
    )
    return (
        script.replace("(function(){\n", "(function(){\n" + prefix, 1)
        if prefix
        else script
    )


def render_panel(
    rows: list[OfferRow],
    now: datetime,
    discarded: Sequence[DiscardedOffer] = (),
    families: FamilyRegistry = BUILTIN_REGISTRY,
    *,
    lang: Lang = LEGACY_LANG,
) -> str:
    """The whole dashboard as one HTML document (no I/O, deterministic).

    ``families`` (the profile's role families) label the discarded offers and
    give legacy NULL profiles their default family. ``lang`` is the UI
    language of every label (and of ``<html lang>``).
    """
    stats = compute_stats(rows, now)
    sent = int(stats["sent"])  # type: ignore[call-overload]
    analyzed = int(stats["analyzed"])  # type: ignore[call-overload]
    responded = int(stats["responded"])  # type: ignore[call-overload]
    interviews = int(stats["interviews"])  # type: ignore[call-overload]
    offers = int(stats["offers"])  # type: ignore[call-overload]

    tiles = [
        (t("panel.tile.analyzed", lang), str(analyzed)),
        (t("panel.tile.sent", lang), str(sent)),
        (t("panel.tile.response_rate", lang), str(stats["response_rate"])),
        (t("panel.tile.interviews", lang), str(interviews)),
        (t("panel.tile.offers", lang), str(offers)),
    ]
    tiles_html = "".join(
        f'<div class="tile"><span class="muted">{html.escape(name)}</span>'
        f"<b>{html.escape(value)}</b></div>"
        for name, value in tiles
    )
    funnel = _bar_chart(
        [
            (t("panel.funnel.analyzed", lang), analyzed, None),
            (t("panel.funnel.sent", lang), sent, None),
            (t("panel.funnel.response", lang), responded, None),
            (t("panel.funnel.interview", lang), interviews, None),
            (t("panel.funnel.offer", lang), offers, None),
        ],
        t("panel.funnel.title", lang),
        lang,
    )
    by_estado: dict[str, int] = stats["by_estado"]  # type: ignore[assignment]
    estado_items = [
        (_estado_label(name, lang), count, ESTADO_FILLS.get(name))
        for name, count in sorted(by_estado.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    by_origen: dict[str, int] = stats["by_origen"]  # type: ignore[assignment]
    origen_items = [
        (name, count, None)
        for name, count in sorted(by_origen.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    by_week: dict[str, int] = stats["by_week"]  # type: ignore[assignment]
    by_match: dict[str, int] = stats["by_match"]  # type: ignore[assignment]

    deadline = _alert_items(
        stats["deadline_alerts"],  # type: ignore[arg-type]
        lambda days: _deadline_text(days, lang),
    )
    silent = _alert_items(
        stats["silent_alerts"],  # type: ignore[arg-type]
        lambda days: t("panel.silent_since", lang, n=days),
    )
    alerts_html = ""
    if deadline:
        alerts_html += f"<h3>{t('panel.deadlines', lang)}</h3><ul>{deadline}</ul>"
    if silent:
        alerts_html += f"<h3>{t('panel.silent', lang)}</h3><ul>{silent}</ul>"
    if not alerts_html:
        alerts_html = f'<p class="muted">{t("panel.no_alerts", lang)}</p>'

    stamp = html.escape(now.strftime("%Y-%m-%d %H:%M"))
    title = t("panel.title", lang)
    by_status = t("panel.by_status", lang)
    by_portal = t("panel.by_portal", lang)
    sent_per_week = t("panel.sent_per_week", lang)
    match_title = t("panel.match", lang)
    columns = "".join(
        f"<th>{t(f'panel.col.{col}', lang)}</th>"
        for col in ("company", "role", "profile", "portal", "status", "match")
    ) + "".join(
        f"<th>{t(f'panel.col.{col}', lang)}</th>" for col in ("sent", "updated")
    )
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title>"
        f"<style>{_CSS}{_estado_css()}</style></head><body>"
        f'<h1>{title}</h1><p class="muted">{t("panel.generated", lang, stamp=stamp)}</p>'
        f'<div class="kpis">{tiles_html}</div>'
        f'<div class="card alerts"><h2>{t("panel.alerts", lang)}</h2>{alerts_html}</div>'
        '<div class="grid">'
        f'<div class="card"><h2>{t("panel.funnel.title", lang)}</h2>{funnel}</div>'
        f'<div class="card"><h2>{by_status}</h2>{_bar_chart(estado_items, by_status, lang)}</div>'
        f'<div class="card"><h2>{by_portal}</h2>{_bar_chart(origen_items, by_portal, lang)}</div>'
        f'<div class="card"><h2>{sent_per_week}</h2>'
        f"{_bar_chart([(k, v, None) for k, v in by_week.items()], t('panel.by_week', lang), lang)}</div>"
        f'<div class="card"><h2>{match_title}</h2>'
        f"{_bar_chart([(k, v, None) for k, v in by_match.items()], t('panel.by_match', lang), lang)}</div>"
        "</div>"
        f"{_discarded_html(discarded, families, lang)}"
        f'<div class="card"><h2>{t("panel.offers", lang)} <span id="count" class="muted"></span></h2>'
        '<div class="filters">'
        f'<input id="q" type="search" placeholder="{t("panel.search", lang)}">'
        f'<select id="f-estado"><option value="">{t("panel.filter.status", lang)}</option></select>'
        f'<select id="f-perfil"><option value="">{t("panel.filter.profile", lang)}</option></select>'
        f'<select id="f-portal"><option value="">{t("panel.filter.portal", lang)}</option></select></div>'
        f'<div class="tablewrap"><table><thead><tr><th>#</th>{columns}</tr></thead>'
        '<tbody id="rows"></tbody></table></div></div>'
        f'<script id="panel-data" type="application/json">{_json_blob(rows, families.default.id)}</script>'
        f"<script>{_js(lang)}</script></body></html>"
    )


def _cut(text: str, limit: int = REASON_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ago(days: int, lang: Lang = LEGACY_LANG) -> str:
    if days <= 0:
        return t("panel.ago_today", lang)
    if days == 1:
        return t("panel.ago_one", lang)
    return t("panel.ago_many", lang, days=days)


def _discarded_cells(item: DiscardedOffer, lang: Lang = LEGACY_LANG) -> tuple[str, str]:
    """The shared ``#id``/title/company/portal cells and the trailing hint."""
    label = html.escape(item.title or t("panel.js.offer", lang))
    url = _safe_url(item.url)
    title = (
        f'<a href="{html.escape(url)}" rel="noopener noreferrer" '
        f'target="_blank">{label}</a>'
        if url
        else label
    )
    head = (
        f"<td>#{item.offer_id}</td><td>{title}</td>"
        f"<td>{html.escape(item.company or _NO_VALUE)}</td>"
        f"<td>{html.escape(item.portal or _NO_VALUE)}</td>"
    )
    # ``reason`` is stored in Spanish (gate or stack-wall text): translate for display.
    reason = html.escape(
        _cut(stack_wall_display(gate_reason(item.reason, lang), lang)) or _NO_VALUE
    )
    tail = (
        f"<td>{reason}</td>"
        f"<td>{_ago(item.days_ago, lang)}</td>"
        f"<td><code>{t('panel.rescue_cmd', lang, id=item.offer_id)}</code></td>"
    )
    return head, tail


def _discarded_html(
    items: Sequence[DiscardedOffer],
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> str:
    """Two cards: discarded by score (open) and by wall (collapsed)."""
    below = sorted(
        (i for i in items if i.kind == "below"),
        key=lambda i: (i.threshold - i.score, -i.offer_id),
    )
    walled = [i for i in items if i.kind == "walled"]
    near = sum(1 for i in below if i.threshold - i.score < NEAR_THRESHOLD_POINTS)
    common = "".join(
        f"<th>{t(f'panel.col.{col}', lang)}</th>"
        for col in ("role", "company", "portal")
    )

    def trailing(first: str) -> str:
        return "".join(
            f"<th>{t(f'panel.col.{col}', lang)}</th>"
            for col in (first, "seen", "rescue")
        )

    if below:
        rows = ""
        for item in below:
            head, tail = _discarded_cells(item, lang)
            tag = html.escape(_family_tag(item.profile, families, lang))
            rows += (
                f"<tr>{head}<td>{item.score} / {item.threshold} ({tag})</td>{tail}</tr>"
            )
        summary = t(
            "panel.discarded.summary",
            lang,
            count=len(below),
            near=near,
            points=NEAR_THRESHOLD_POINTS,
        )
        below_html = (
            f'<p class="muted">{summary}</p>'
            f'<div class="tablewrap"><table><thead><tr><th>#</th>{common}'
            f"<th>{t('panel.col.score_threshold', lang)}</th>"
            f"{trailing('reason')}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        below_html = f'<p class="muted">{t("panel.discarded.none_score", lang)}</p>'
    if walled:
        rows = ""
        for item in walled:
            head, tail = _discarded_cells(item, lang)
            rows += f"<tr>{head}<td>{html.escape(item.location or _NO_VALUE)}</td>{tail}</tr>"
        walled_html = (
            f'<div class="tablewrap"><table><thead><tr><th>#</th>{common}'
            f"<th>{t('panel.col.location', lang)}</th>"
            f"{trailing('wall')}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        walled_html = f'<p class="muted">{t("panel.discarded.none_wall", lang)}</p>'
    return (
        f'<div class="card"><h2>{t("panel.discarded.score", lang)} '
        f'<span class="muted">{t("panel.discarded.last_days", lang, days=DISCARDED_DAYS)}</span></h2>'
        f"{below_html}</div>"
        f'<details class="card"><summary><b>{t("panel.discarded.wall", lang)}</b> '
        f'<span class="muted">({len(walled)})</span></summary>{walled_html}</details>'
    )


def write_panel_atomic(content: str, path: Path) -> bool:
    """Atomic best-effort write (temp file + ``os.replace``); never raises."""
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        # mkstemp creates 0600: keep the previous file's mode, else 0644.
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
        return True
    except Exception:  # noqa: BLE001 — a sync-folder copy must never break /panel
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        return False
