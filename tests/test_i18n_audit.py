"""Every production call passes the UI language (bot-i18n S3/S5).

During the migration, functions that render user-facing text gained a UI
language parameter with a transitional Spanish default (``LEGACY_LANG``), so
callers that were not migrated yet kept today's output. A call that omits it
silently shows Spanish to an English user, and no output test notices while
the suite runs with ``CVA_UI_LANGUAGE=es``. This module makes "every
production call passes the UI language" a permanent invariant instead.

The rule, checked statically over ``bot.py``, ``cli.py`` and ``cva/**``:

1. A **UI-language parameter** is a ``def`` parameter named ``lang``,
   ``ui_lang`` or ``ui_language`` whose annotation is ``Lang`` (from
   :mod:`cva.i18n`, possibly ``Lang | None``) and which has a default
   (``LEGACY_LANG``, ``FEEDBACK_LANG``, ``"es"``, ``None``...). Document
   languages are NOT UI languages and are excluded by construction: they are
   typed ``str`` or ``Language`` (``cv_keyboard(lang=...)`` is the CV
   language, its UI parameter is ``ui_lang``; ``infer_language``,
   ``salutation_for``, cover-letter/follow-up ``language``). A parameter
   without a default is enforced by mypy already.
2. A **call** to such a function (top-level function, ``Class(...)`` through
   ``__init__``, ``self.method``, ``module.func``, imported names and
   re-exports, or ``obj.method`` when the method name belongs to exactly one
   indexed function and to nothing else in production) must pass that
   argument by keyword or by position (``*args``/``**kwargs`` count as
   passing it).
3. Calls that are Spanish on purpose live in :data:`ALLOWLIST`, each with its
   reason. A stale entry (the call no longer exists) also fails, so the list
   cannot rot.

``cva/setup/`` is excluded: the setup wizard is English-only text and
declares no UI-language parameter. Unresolvable calls (an object of unknown
type with an ambiguous method name) are not checked; the rule errs on the
side of no false positives.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_PARAM_NAMES = frozenset({"lang", "ui_lang", "ui_language"})

# (caller module, enclosing function qualname, callee "module:qualname") ->
# reason. One entry covers every such call inside that enclosing function.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    # Validators default to FEEDBACK_LANG ("es"): their problems go back to
    # the model in the retry prompt, which stays Spanish so the owner golden
    # prompts are byte-identical (L7). The owner-facing warnings are rendered
    # in ui_lang separately.
    (
        "cva.match.cover_letter",
        "generate_cover_letter._check",
        "cva.match.cover_letter:validate_parts",
    ): "problems feed the Spanish retry prompt (FEEDBACK_LANG)",
    (
        "cva.match.followup",
        "generate_followup",
        "cva.match.followup:validate_followup",
    ): "problems feed the Spanish retry prompt (FEEDBACK_LANG)",
    # Test doubles of JobService have no ui_language: Spanish keeps the
    # existing bot tests' output; the real service always has one.
    (
        "bot",
        "_ui",
        "getattr",
    ): "fallback for service test doubles without ui_language",
    # Module-level Spanish constant kept for compatibility (callback-data
    # docs and tests index it); production code calls star_fields(lang).
    (
        "cva.star",
        "<module>",
        "cva.star:star_fields",
    ): "STAR_FIELDS compatibility constant, not shown to the user",
}


@dataclass(frozen=True)
class UiParam:
    name: str
    position: int | None  # positional index after self/cls; None: keyword-only


@dataclass
class Module:
    name: str
    tree: ast.Module
    defs: dict[str, UiParam | None] = field(default_factory=dict)  # qualname
    classes: dict[str, list[str]] = field(default_factory=dict)  # bases (names)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    module: str
    scope: str
    callee: str
    line: int

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.module, self.scope, self.callee)


def production_files(root: Path = ROOT) -> list[Path]:
    files = [root / "bot.py", root / "cli.py"]
    files += [
        p
        for p in sorted((root / "cva").rglob("*.py"))
        if "setup" not in p.relative_to(root / "cva").parts[:1]
    ]
    return files


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _mentions_lang(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id == "Lang":
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "Lang" in node.value.replace("Language", "").split():
                return True
            try:
                if _mentions_lang(ast.parse(node.value, mode="eval").body):
                    return True
            except SyntaxError:
                pass
    return False


def _imports_lang(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "cva.i18n"
            and any(alias.name == "Lang" for alias in node.names)
        ):
            return True
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "Lang" for t in node.targets
        ):
            return True  # cva.i18n itself
    return False


def ui_param(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, method: bool
) -> UiParam | None:
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults: dict[str, bool] = {}
    for arg, default in zip(
        positional[len(positional) - len(args.defaults) :], args.defaults, strict=True
    ):
        defaults[arg.arg] = default is not None
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        defaults[arg.arg] = kw_default is not None
    skip = 1 if method and positional and positional[0].arg in {"self", "cls"} else 0
    for index, arg in enumerate(positional):
        if (
            arg.arg in UI_PARAM_NAMES
            and defaults.get(arg.arg)
            and _mentions_lang(arg.annotation)
        ):
            return UiParam(arg.arg, index - skip)
    for arg in args.kwonlyargs:
        if (
            arg.arg in UI_PARAM_NAMES
            and defaults.get(arg.arg)
            and _mentions_lang(arg.annotation)
        ):
            return UiParam(arg.arg, None)
    return None


def _resolve_relative(
    module: str, is_package: bool, level: int, target: str | None
) -> str:
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def index_module(name: str, source: str, is_package: bool = False) -> Module:
    tree = ast.parse(source)
    mod = Module(name, tree)
    has_lang = _imports_lang(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.defs[node.name] = ui_param(node, method=False) if has_lang else None
        elif isinstance(node, ast.ClassDef):
            mod.classes[node.name] = [ast.unparse(b) for b in node.bases]
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    static = any(
                        isinstance(d, ast.Name) and d.id == "staticmethod"
                        for d in item.decorator_list
                    )
                    mod.defs[f"{node.name}.{item.name}"] = (
                        ui_param(item, method=not static) if has_lang else None
                    )
    for imp in ast.walk(tree):
        if isinstance(imp, ast.Import):
            for alias in imp.names:
                if alias.asname:
                    mod.imports[alias.asname] = (alias.name, None)
                else:
                    top = alias.name.split(".")[0]
                    mod.imports.setdefault(top, (top, None))
        elif isinstance(imp, ast.ImportFrom):
            source_mod = (
                _resolve_relative(name, is_package, imp.level, imp.module)
                if imp.level
                else imp.module or ""
            )
            for alias in imp.names:
                mod.imports[alias.asname or alias.name] = (source_mod, alias.name)
    return mod


class Audit:
    def __init__(self, modules: dict[str, Module]) -> None:
        self.modules = modules
        ui_by_attr: Counter[str] = Counter()
        any_by_attr: Counter[str] = Counter()
        self.unique_method: dict[str, str] = {}
        for mod in modules.values():
            for qualname, param in mod.defs.items():
                attr = qualname.rsplit(".", 1)[-1]
                any_by_attr[attr] += 1
                if param is not None:
                    ui_by_attr[attr] += 1
                    self.unique_method[attr] = f"{mod.name}:{qualname}"
        self.unique_method = {
            attr: target
            for attr, target in self.unique_method.items()
            if ui_by_attr[attr] == 1 and any_by_attr[attr] == 1
        }

    # -- resolution -------------------------------------------------------
    def _symbol(self, module: str, name: str, depth: int = 0) -> str | None:
        """``module:qualname`` of a def/class, or ``module`` for a module."""
        if depth > 5:
            return None
        mod = self.modules.get(module)
        if mod is None:
            return None
        if name in mod.defs or name in mod.classes:
            return f"{module}:{name}"
        if name in mod.imports:
            src, attr = mod.imports[name]
            if attr is None:
                return src if src in self.modules else None
            return self._from(src, attr, depth + 1)
        return None

    def _from(self, src: str, attr: str, depth: int) -> str | None:
        if f"{src}.{attr}" in self.modules:
            return f"{src}.{attr}"
        return self._symbol(src, attr, depth)

    def _callable(self, target: str, depth: int = 0) -> tuple[str, UiParam] | None:
        """The function a ``module:qualname`` target runs when called."""
        if ":" not in target or depth > 5:
            return None
        module, qualname = target.split(":", 1)
        mod = self.modules[module]
        if qualname in mod.classes:
            return self._method(module, qualname, "__init__", depth)
        param = mod.defs.get(qualname)
        return (target, param) if param is not None else None

    def _method(
        self, module: str, cls: str, attr: str, depth: int = 0
    ) -> tuple[str, UiParam] | None:
        if depth > 5:
            return None
        mod = self.modules[module]
        qualname = f"{cls}.{attr}"
        if qualname in mod.defs:
            param = mod.defs[qualname]
            return (f"{module}:{qualname}", param) if param is not None else None
        for base in mod.classes.get(cls, []):
            base_target = self._symbol(module, base)
            if base_target and ":" in base_target:
                base_mod, base_cls = base_target.split(":", 1)
                if base_cls in self.modules[base_mod].classes:
                    found = self._method(base_mod, base_cls, attr, depth + 1)
                    if found:
                        return found
        return None

    def resolve(
        self, mod: Module, cls: str | None, func: ast.expr
    ) -> tuple[str, UiParam] | None:
        """The ``module:qualname`` a call runs and its UI-language parameter."""
        if isinstance(func, ast.Name):
            target = self._symbol(mod.name, func.id)
            found = self._callable(target) if target else None
            return found
        if not isinstance(func, ast.Attribute):
            return None
        value = func.value
        if isinstance(value, ast.Name) and value.id in {"self", "cls"} and cls:
            found = self._method(mod.name, cls, func.attr)
            if found:
                return found
        else:
            dotted = _dotted(value)
            if dotted:
                head, *rest = dotted.split(".")
                target = self._symbol(mod.name, head)
                for part in rest:
                    if target is None or ":" in target:
                        target = None
                        break
                    target = self._from(target, part, 0)
                if target is not None:
                    if ":" not in target:  # module.func / module.Class
                        sym = self._from(target, func.attr, 0)
                        found = self._callable(sym) if sym else None
                        return found
                    module, qualname = target.split(":", 1)
                    if qualname in self.modules[module].classes:  # Class.method
                        found = self._method(module, qualname, func.attr)
                        return found
        unique = self.unique_method.get(func.attr)
        if unique and "." in unique.split(":", 1)[1]:
            module, qualname = unique.split(":", 1)
            param = self.modules[module].defs[qualname]
            assert param is not None
            return unique, param
        return None

    # -- checking ---------------------------------------------------------
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for mod in self.modules.values():
            _Visitor(self, mod, out).visit(mod.tree)
        return out


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _is_legacy(node: ast.expr) -> bool:
    """``LEGACY_LANG`` passed explicitly is the same as omitting the language."""
    if isinstance(node, ast.Name):
        return node.id == "LEGACY_LANG"
    return isinstance(node, ast.Attribute) and node.attr == "LEGACY_LANG"


def _passes(call: ast.Call, param: UiParam) -> bool:
    if any(kw.arg is None or kw.arg == param.name for kw in call.keywords):
        return True
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        return True
    if param.position is None:
        return False
    return len(call.args) > param.position


class _Visitor(ast.NodeVisitor):
    def __init__(self, audit: Audit, mod: Module, out: list[Finding]) -> None:
        self.audit, self.mod, self.out = audit, mod, out
        self.scope: list[str] = []
        self.cls: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        outer = self.cls
        self.cls = node.name if not self.scope else outer
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.cls = outer

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self.audit.resolve(self.mod, self.cls, node.func)
        legacy = any(_is_legacy(a) for a in node.args) or any(
            _is_legacy(kw.value) for kw in node.keywords
        )
        callee: str | None = None
        if resolved and (legacy or not _passes(node, resolved[1])):
            callee = resolved[0]
        elif legacy:
            callee = ast.unparse(node.func)
        if callee is not None:
            scope = ".".join(self.scope) or "<module>"
            self.out.append(Finding(self.mod.name, scope, callee, node.lineno))
        self.generic_visit(node)


def audit_sources(sources: dict[str, str]) -> list[Finding]:
    """Audit ``{module name: source}``; names ending in ``.__init__`` are packages."""
    modules: dict[str, Module] = {}
    for name, source in sources.items():
        is_package = name.endswith(".__init__")
        clean = name.removesuffix(".__init__")
        modules[clean] = index_module(clean, source, is_package)
    return Audit(modules).findings()


def audit_production(root: Path = ROOT) -> list[Finding]:
    sources: dict[str, str] = {}
    for path in production_files(root):
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        sources[name] = path.read_text(encoding="utf-8")
    return audit_sources(sources)


# -- tests ------------------------------------------------------------------


def _report(findings: list[Finding]) -> str:
    return "\n".join(
        f"  {f.module}:{f.line} in {f.scope} -> {f.callee}" for f in findings
    )


def test_every_production_call_passes_the_ui_language() -> None:
    offending = [f for f in audit_production() if f.key not in ALLOWLIST]
    assert not offending, (
        "calls omit the UI language (pass service.ui_language, _lang(context), "
        "settings.ui_language... or add an ALLOWLIST entry with its reason):\n"
        + _report(offending)
    )


def test_allowlist_has_no_stale_entries() -> None:
    present = {f.key for f in audit_production()}
    stale = sorted(set(ALLOWLIST) - present)
    assert not stale, f"ALLOWLIST entries match no call any more: {stale}"


_I18N = 'from typing import Literal\nLang = Literal["en", "es"]\nLEGACY_LANG = "es"\n'

_LIB = """
from cva.i18n import LEGACY_LANG, Lang

def card(offer: str, lang: Lang = LEGACY_LANG) -> str: ...
def keyboard(offer: str, *, lang: str = "es", ui_lang: Lang = LEGACY_LANG) -> str: ...
def letter(text: str, language: str = "es") -> str: ...

class Panel:
    def __init__(self, data: str, ui_language: Lang = LEGACY_LANG) -> None: ...
    def render_for(self, lang: Lang | None = None) -> str: ...
"""

_CALLER = """
from cva import lib
from cva.i18n import LEGACY_LANG
from cva.lib import Panel, card, keyboard, letter

def handler(lang, panel):
    card("x")                       # offends: positional default omitted
    card("x", lang)                 # ok: positional
    lib.card("x", lang=lang)        # ok: keyword through the module
    keyboard("x", lang="en")        # offends: CV language is not ui_lang
    keyboard("x", ui_lang=lang)     # ok
    letter("x")                     # ok: document language, not UI
    Panel("d")                      # offends: __init__
    panel.render_for()              # offends: unique method name
    card(*("x", lang))              # ok: unpacked
    card("x", lang=LEGACY_LANG)     # offends: explicit legacy default
    getattr(panel, "x", LEGACY_LANG)  # offends: legacy fallback anywhere
"""


def test_audit_catches_synthetic_offending_calls() -> None:
    findings = audit_sources(
        {"cva.i18n.__init__": _I18N, "cva.lib": _LIB, "cva.caller": _CALLER}
    )
    got = sorted((f.line, f.callee) for f in findings)
    assert got == [
        (7, "cva.lib:card"),
        (10, "cva.lib:keyboard"),
        (13, "cva.lib:Panel.__init__"),
        (14, "cva.lib:Panel.render_for"),
        (16, "cva.lib:card"),
        (17, "getattr"),
    ]
    assert {f.scope for f in findings} == {"handler"}
