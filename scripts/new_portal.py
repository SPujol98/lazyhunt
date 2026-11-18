"""Scaffold a new job-portal adapter (portal-adapters T4).

    uv run python scripts/new_portal.py <id> <label> <host>
    uv run python scripts/new_portal.py tecnoempleo Tecnoempleo www.tecnoempleo.com

Creates, without ever overwriting anything:

* ``extension/portals/<id>.js``: adapter skeleton implementing the contract
  documented in ``extension/portals/registry.js`` (TODOs to fill in);
* ``tests/fixtures/portals/<id>.html`` + ``<id>.expected.json``: placeholders
  for the sanitized saved results page and the cards it must yield;
* ``tests/test_portal_<id>.py``: pytest file over the generic fixture harness
  (RED until the fixture holds 3+ cards and the adapter extracts them);

and edits the registry (import + ``PORTALS`` entry). Every target is checked
up front, so a refusal leaves the tree untouched. The skill that
drives this is ``.claude/skills/add-portal/SKILL.md``; the guide is
``docs/portals.md``. Standard library only.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

_ID_RE = re.compile(r"^[a-z][a-z0-9]*$")
_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
_IMPORT_RE = re.compile(r"^import \w+ from '\./\w+\.js';$", re.MULTILINE)
_PORTALS_RE = re.compile(r"^(export const PORTALS = \[)([^\]]*)(\];)$", re.MULTILINE)

_ADAPTER = """\
// {label} portal adapter (scaffolded by scripts/new_portal.py; add-portal skill).
// Contract: see portals/registry.js. Fill every TODO from the owner's saved
// results page (tests/fixtures/portals/{pid}.html), tests first.

// TODO: card extractor. It is injected with chrome.scripting.executeScript, so
// it must stay self-contained (no imports, no module-scope bindings, no
// chrome.*) and keep this function name. Return
// Promise<[{{id, title, company, location, link}}]>; wait for the page to
// settle (setTimeout) when the portal renders late.
function extract{cls}Cards() {{
  return new Promise((resolve) => {{
    const results = [];
    // TODO: card container, offer id + absolute link, title/company/location.
    resolve(results);
  }});
}}

// TODO: the portal's own "no results" copy (regex sources, matched
// case-insensitively against the page text).
export const NO_RESULTS_MARKERS = ['TODO: no-results copy'];

function hostMatch(url) {{
  try {{
    const host = new URL(String(url).includes('://') ? url : 'https:' + url).hostname;
    return host === {base} || host.endsWith({dotbase});
  }} catch (_) {{
    return false;
  }}
}}

// TODO: search URL params for keywords / location / recency, as observed in
// the search URL the owner gave. `extra` overrides or adds params (an empty
// value drops one). Use slugify() from ../slug.js for path-style locations.
function buildSearchUrl({{ keywords = '', location = '', extra = {{}} }} = {{}}) {{
  const params = new URLSearchParams();
  const kw = String(keywords).trim();
  if (kw) params.set('q', kw); // TODO: real keyword param
  // TODO: location + recency params
  for (const [k, v] of Object.entries(extra || {{}})) {{
    if (v === null || v === undefined || v === '') params.delete(k);
    else params.set(k, String(v));
  }}
  return 'https://' + {host} + '/' + '?' + params.toString(); // TODO: real search path
}}

export default {{
  id: '{pid}',
  label: {label_js},
  linkHost: {host},
  settleMs: 2500,
  noResultsMarkers: NO_RESULTS_MARKERS,
  hostMatch,
  buildSearchUrl,
  extractCards: extract{cls}Cards,
  // Optional: extractDetail (page function returning the offer description),
  // isSessionExpired(tabUrl) (login-wall guard).
  // TODO: offer URL derived from the id, or null when the id alone is not enough.
  canonicalUrl: () => null,
}};
"""

_TEST = '''\
"""{label} portal adapter over the owner's saved results page (scaffolded).

RED until ``tests/fixtures/portals/{pid}.html`` holds a SANITIZED copy of the
saved page (3+ cards, no personal data), ``{pid}.expected.json`` lists the
cards it must yield, and ``extension/portals/{pid}.js`` extracts them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
HOST = "{host}"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_{pid}_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "{pid}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{{result.stdout}}\\n{{result.stderr}}"


def test_{pid}_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import {pid} from './{pid}.js';" in registry
    assert "{pid}" in registry.split("PORTALS = [", 1)[1]


def test_{pid}_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{{HOST}}/*" in manifest["optional_host_permissions"]
'''

_EXPECTED = {
    "cards": [],
    "no_results_text": "",
    "urls": [],
}

_FIXTURE = """\
<!-- Placeholder for {label}: replace with a SANITIZED copy of the owner's saved
     results page (Ctrl+S). Keep 3+ cards and the portal's markup; strip names,
     emails, tokens, cookies and any personal data. Never commit the raw page. -->
<main></main>
"""

_NEXT_STEPS = """\
Created portal '{pid}' ({label}, {host}).

Next steps
 1. Put a sanitized copy of the saved results page in tests/fixtures/portals/{pid}.html
    (3+ cards) and the cards it must yield in {pid}.expected.json (plus
    no_results_text and urls).
 2. Run `uv run pytest -q tests/test_portal_{pid}.py`: it must be RED.
 3. Fill in extension/portals/{pid}.js (extractCards, noResultsMarkers,
    buildSearchUrl, canonicalUrl) until the test is GREEN.
 4. Add "https://{host}/*" to extension/manifest.json optional_host_permissions
    (the owner reloads the unpacked extension, then enables the portal in Options).
 5. Add one SourceSpec for the portal to SOURCES in cva/ingest/sources.py (hosts,
    job-id regex, optional canonical_host) with tests in
    tests/test_ingest_sources.py. Nothing else bot-side: origen, the bridge,
    the retry and the fetch routing read that registry.
 6. Run the full verification: uv run pytest -q; uv run mypy --strict cva;
    uv run ruff check cva tests bot.py cli.py scripts; node --check on changed JS.
 7. Never commit the raw saved page. See docs/portals.md.
"""


class ScaffoldError(Exception):
    """The scaffold refused; nothing was written."""


def _validate(portal_id: str, label: str, host: str) -> None:
    if not _ID_RE.fullmatch(portal_id):
        raise ScaffoldError(
            f"invalid id {portal_id!r}: lowercase letters and digits, starting with a letter"
        )
    if not label.strip():
        raise ScaffoldError("the label must not be empty")
    if not _HOST_RE.fullmatch(host):
        raise ScaffoldError(
            f"invalid host {host!r}: a bare hostname such as www.example.com (no scheme or path)"
        )


def _with_registry_entry(registry: str, portal_id: str) -> str:
    imports = list(_IMPORT_RE.finditer(registry))
    portals = _PORTALS_RE.search(registry)
    if not imports or portals is None:
        raise ScaffoldError(
            "registry.js has no `import x from './x.js';` lines or `export const PORTALS = [...]`"
        )
    if f"import {portal_id} from" in registry:
        raise ScaffoldError(f"portal {portal_id!r} is already in the registry")
    last = imports[-1]
    line = f"import {portal_id} from './{portal_id}.js';"
    registry = registry[: last.end()] + "\n" + line + registry[last.end() :]
    portals = _PORTALS_RE.search(registry)
    assert portals is not None
    items = portals.group(2).strip()
    owner = "...OWNER_PORTALS"
    if items.endswith(owner):
        # Owner-only portals stay last: the public export empties that list.
        public = items[: -len(owner)].rstrip().rstrip(",").strip()
        joined = (
            f"{public}, {portal_id}, {owner}" if public else f"{portal_id}, {owner}"
        )
    else:
        joined = f"{items}, {portal_id}" if items else portal_id
    return (
        registry[: portals.start()]
        + portals.group(1)
        + joined
        + portals.group(3)
        + registry[portals.end() :]
    )


def scaffold(root: Path, portal_id: str, label: str, host: str) -> list[Path]:
    """Create the portal files under ``root`` and edit the registry; return created paths."""
    _validate(portal_id, label, host)
    ext = root / "extension"
    fixtures = root / "tests" / "fixtures" / "portals"
    adapter = ext / "portals" / f"{portal_id}.js"
    html = fixtures / f"{portal_id}.html"
    expected = fixtures / f"{portal_id}.expected.json"
    test = root / "tests" / f"test_portal_{portal_id}.py"
    created = [adapter, html, expected, test]
    existing = [p for p in created if p.exists()]
    if existing:
        raise ScaffoldError(
            "refusing to overwrite, already exists: "
            + ", ".join(str(p.relative_to(root)) for p in existing)
        )
    registry_path = ext / "portals" / "registry.js"
    if not registry_path.is_file():
        raise ScaffoldError(
            f"missing {registry_path.relative_to(root)}: not a repo root?"
        )

    # Compute every edit in memory first, then write: a refusal leaves no trace.
    new_registry = _with_registry_entry(
        registry_path.read_text(encoding="utf-8"), portal_id
    )

    base = host.removeprefix("www.")
    adapter_src = _ADAPTER.format(
        label=label,
        pid=portal_id,
        cls=portal_id.capitalize(),
        base=json.dumps(base),
        dotbase=json.dumps("." + base),
        host=json.dumps(host),
        label_js=json.dumps(label),
    )
    adapter.parent.mkdir(parents=True, exist_ok=True)
    fixtures.mkdir(parents=True, exist_ok=True)
    adapter.write_text(adapter_src, encoding="utf-8")
    html.write_text(_FIXTURE.format(label=label), encoding="utf-8")
    expected.write_text(json.dumps(_EXPECTED, indent=2) + "\n", encoding="utf-8")
    test.write_text(
        _TEST.format(label=label, pid=portal_id, host=host), encoding="utf-8"
    )
    registry_path.write_text(new_registry, encoding="utf-8")
    return created


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new job-portal adapter.")
    parser.add_argument("id", help="portal id: lowercase, e.g. tecnoempleo")
    parser.add_argument("label", help="display name, e.g. Tecnoempleo")
    parser.add_argument("host", help="bare hostname, e.g. www.tecnoempleo.com")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: this checkout)",
    )
    args = parser.parse_args(argv)
    try:
        scaffold(args.root, args.id, args.label, args.host)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_NEXT_STEPS.format(pid=args.id, label=args.label, host=args.host))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
