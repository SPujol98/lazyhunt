"""scripts/new_portal.py: scaffold a portal adapter, its fixture test and registry entry."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "new_portal", _ROOT / "scripts" / "new_portal.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


new_portal = _load()

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

REGISTRY = """\
import linkedin from './linkedin.js';
import infojobs from './infojobs.js';

export const PORTALS = [linkedin, infojobs];
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal repo layout the scaffold edits."""
    (tmp_path / "extension" / "portals").mkdir(parents=True)
    (tmp_path / "extension" / "portals" / "registry.js").write_text(
        REGISTRY, encoding="utf-8"
    )
    (tmp_path / "tests" / "fixtures" / "portals").mkdir(parents=True)
    return tmp_path


def test_scaffold_creates_the_portal_files(tree: Path) -> None:
    created = new_portal.scaffold(
        tree, "tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com"
    )
    adapter = tree / "extension" / "portals" / "tecnoempleo.js"
    assert adapter.is_file()
    assert (tree / "tests" / "fixtures" / "portals" / "tecnoempleo.html").is_file()
    assert (
        tree / "tests" / "fixtures" / "portals" / "tecnoempleo.expected.json"
    ).is_file()
    assert (tree / "tests" / "test_portal_tecnoempleo.py").is_file()
    assert adapter in created
    source = adapter.read_text(encoding="utf-8")
    assert "id: 'tecnoempleo'" in source
    assert 'label: "Tecnoempleo"' in source
    assert "www.tecnoempleo.com" in source
    assert "TODO" in source
    for field in (
        "hostMatch",
        "buildSearchUrl",
        "extractCards",
        "noResultsMarkers",
        "canonicalUrl",
    ):
        assert field in source
    assert "function extractTecnoempleoCards(" in source


def test_scaffold_registers_the_portal(tree: Path) -> None:
    new_portal.scaffold(tree, "tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com")
    registry = (tree / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import tecnoempleo from './tecnoempleo.js';" in registry
    assert "export const PORTALS = [linkedin, infojobs, tecnoempleo];" in registry
    assert registry.index("import tecnoempleo") > registry.index("import infojobs")


def test_scaffold_needs_no_fork_test_nor_provenance(tree: Path) -> None:
    """extension-rewrite S6: the vendor fork test is retired and PROVENANCE.md
    no longer lists files, so the scaffold neither needs nor creates them."""
    created = new_portal.scaffold(
        tree, "tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com"
    )
    assert len(created) == 4
    assert not (tree / "tests" / "test_extension_fork.py").exists()
    assert not (tree / "extension" / "PROVENANCE.md").exists()


def test_scaffolded_test_starts_red(tree: Path) -> None:
    """The generated test refuses the empty placeholder fixture: RED first."""
    new_portal.scaffold(tree, "tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com")
    test_source = (tree / "tests" / "test_portal_tecnoempleo.py").read_text(
        encoding="utf-8"
    )
    assert "extension_portal_fixture_harness.js" in test_source
    assert "tecnoempleo" in test_source
    assert "host_permissions" in test_source
    expected = json.loads(
        (
            tree / "tests" / "fixtures" / "portals" / "tecnoempleo.expected.json"
        ).read_text(encoding="utf-8")
    )
    assert expected["cards"] == []


def test_scaffold_refuses_to_overwrite_and_changes_nothing(tree: Path) -> None:
    adapter = tree / "extension" / "portals" / "tecnoempleo.js"
    adapter.write_text("// owner work\n", encoding="utf-8")
    before = {p: p.read_text(encoding="utf-8") for p in tree.rglob("*") if p.is_file()}
    with pytest.raises(new_portal.ScaffoldError, match="already exists"):
        new_portal.scaffold(tree, "tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com")
    after = {p: p.read_text(encoding="utf-8") for p in tree.rglob("*") if p.is_file()}
    assert after == before


def test_scaffold_twice_is_refused(tree: Path) -> None:
    new_portal.scaffold(tree, "manfred", "Manfred", "www.getmanfred.com")
    with pytest.raises(new_portal.ScaffoldError):
        new_portal.scaffold(tree, "manfred", "Manfred", "www.getmanfred.com")


@pytest.mark.parametrize("bad_id", ["", "Tecno", "1abc", "a-b", "a b", "../x", "x.js"])
def test_invalid_ids_are_rejected(tree: Path, bad_id: str) -> None:
    with pytest.raises(new_portal.ScaffoldError, match="id"):
        new_portal.scaffold(tree, bad_id, "X", "www.x.com")


@pytest.mark.parametrize(
    "bad_host", ["", "https://www.x.com", "www.x.com/jobs", "x", "a b.com"]
)
def test_invalid_hosts_are_rejected(tree: Path, bad_host: str) -> None:
    with pytest.raises(new_portal.ScaffoldError, match="host"):
        new_portal.scaffold(tree, "xportal", "X", bad_host)


def test_missing_registry_marker_changes_nothing(tree: Path) -> None:
    (tree / "extension" / "portals" / "registry.js").write_text(
        "export const OTHER = [];\n", encoding="utf-8"
    )
    before = sorted(p for p in tree.rglob("*") if p.is_file())
    with pytest.raises(new_portal.ScaffoldError, match="registry"):
        new_portal.scaffold(tree, "xportal", "X", "www.x.com")
    assert sorted(p for p in tree.rglob("*") if p.is_file()) == before


def test_label_with_quotes_is_escaped(tree: Path) -> None:
    new_portal.scaffold(tree, "xportal", 'X "Jobs"', "www.x.com")
    assert 'label: "X \\"Jobs\\""' in (
        tree / "extension" / "portals" / "xportal.js"
    ).read_text(encoding="utf-8")


def test_main_prints_next_steps(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = new_portal.main(
        ["tecnoempleo", "Tecnoempleo", "www.tecnoempleo.com", "--root", str(tree)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Next steps" in out
    assert "host_permissions" in out
    assert "SourceSpec" in out and "cva/ingest/sources.py" in out
    assert "reload" in out.lower()


def test_main_reports_errors_with_a_nonzero_exit(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = new_portal.main(["Bad Id", "X", "www.x.com", "--root", str(tree)])
    assert code == 1
    assert "error" in capsys.readouterr().err.lower()


@requires_node
def test_end_to_end_red_then_green_in_a_copy(tmp_path: Path) -> None:
    """Scaffold into a copy of the real extension, watch the generated test go
    RED on the placeholder fixture, fill the portal in, watch it go GREEN."""
    shutil.copytree(_ROOT / "extension", tmp_path / "extension")
    (tmp_path / "tests" / "fixtures" / "portals").mkdir(parents=True)
    for name in ("minidom.js", "extension_portal_fixture_harness.js"):
        shutil.copy(
            _ROOT / "tests" / "fixtures" / name, tmp_path / "tests" / "fixtures" / name
        )
    new_portal.scaffold(tmp_path, "toyjobs", "ToyJobs", "www.toyjobs.test")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                str(
                    tmp_path
                    / "tests"
                    / "fixtures"
                    / "extension_portal_fixture_harness.js"
                ),
                "toyjobs",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            encoding="utf-8",
        )

    red = run()
    assert red.returncode != 0  # placeholder fixture: no cards yet

    fixtures = tmp_path / "tests" / "fixtures" / "portals"
    (fixtures / "toyjobs.html").write_text(
        "<main>"
        + "".join(
            f'<article class="job"><a href="/job/{i}">Role {i}</a><span>Acme</span><em>Barcelona</em></article>'
            for i in (1, 2, 3)
        )
        + "</main>",
        encoding="utf-8",
    )
    cards = [
        {
            "id": str(i),
            "title": f"Role {i}",
            "company": "Acme",
            "location": "Barcelona",
            "link": f"https://www.toyjobs.test/job/{i}",
        }
        for i in (1, 2, 3)
    ]
    (fixtures / "toyjobs.expected.json").write_text(
        json.dumps(
            {
                "cards": cards,
                "no_results_text": "Sin resultados",
                "urls": ["https://www.toyjobs.test/x"],
            }
        ),
        encoding="utf-8",
    )
    adapter_path = tmp_path / "extension" / "portals" / "toyjobs.js"
    source = adapter_path.read_text(encoding="utf-8")
    start = source.index("function extractToyjobsCards(")
    end = source.index("\n}\n", start) + 3
    adapter_path.write_text(
        source[:start]
        + """function extractToyjobsCards() {
  return Promise.resolve(
    Array.from(document.querySelectorAll('article.job')).map((el) => {
      const a = el.querySelector('a');
      const href = a.getAttribute('href');
      return {
        id: href.split('/').pop(),
        title: a.innerText,
        company: el.querySelector('span').innerText,
        location: el.querySelector('em').innerText,
        link: 'https://www.toyjobs.test' + href,
      };
    }),
  );
}
"""
        + source[end:],
        encoding="utf-8",
    )
    adapter_path.write_text(
        adapter_path.read_text(encoding="utf-8").replace(
            "'TODO: no-results copy'", "'sin resultados'"
        ),
        encoding="utf-8",
    )
    green = run()
    assert green.returncode == 0, f"{green.stdout}\n{green.stderr}"


@pytest.mark.owner  # the Claude skill stays private; the public recipe is a doc
def test_add_portal_skill_has_valid_frontmatter_and_workflow() -> None:
    skill = (_ROOT / ".claude" / "skills" / "add-portal" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert skill.startswith("---\n")
    front, _, body = skill[4:].partition("\n---\n")
    assert "name: add-portal" in front
    for phrase in ("add portal", "añade portal", "nuevo portal"):
        assert phrase in front
    for needle in (
        "scripts/new_portal.py",
        "NEVER browse",
        "RED",
        "host_permissions",
        "cva/ingest/sources.py",
        "reload",
        "raw saved page",
    ):
        assert needle in body, needle


def test_adding_a_portal_recipe_covers_the_workflow() -> None:
    """The public, tool-agnostic recipe any coding agent can follow."""
    recipe = (_ROOT / "docs" / "adding-a-portal.md").read_text(encoding="utf-8")
    for needle in (
        "scripts/new_portal.py",
        "NEVER browse",
        "RED",
        "host_permissions",
        "cva/ingest/sources.py",
        "raw saved page",
    ):
        assert needle in recipe, needle


def test_portals_guide_covers_the_essentials() -> None:
    guide = (_ROOT / "docs" / "portals.md").read_text(encoding="utf-8")
    for needle in (
        "buildSearchUrl",
        "adding-a-portal.md",
        "reload",
        "Terms of use",
        "host_permissions",
    ):
        assert needle in guide, needle


def test_scaffold_keeps_owner_portals_last(tmp_path: Path) -> None:
    """Public portals go before ``...OWNER_PORTALS``, which stays last so the
    public export can empty it (public-release S4)."""
    (tmp_path / "extension" / "portals").mkdir(parents=True)
    (tmp_path / "extension" / "portals" / "registry.js").write_text(
        "import linkedin from './linkedin.js';\n"
        "import { OWNER_PORTALS } from './owner_portals.js';\n\n"
        "export const PORTALS = [linkedin, ...OWNER_PORTALS];\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "fixtures" / "portals").mkdir(parents=True)
    new_portal.scaffold(tmp_path, "toyjobs", "ToyJobs", "www.toyjobs.test")
    registry = (tmp_path / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "export const PORTALS = [linkedin, toyjobs, ...OWNER_PORTALS];" in registry
