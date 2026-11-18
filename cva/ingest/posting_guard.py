"""Posting guard: strip and flag hidden / invisible text in offer content.

An offer is untrusted text that reaches the extraction and judge prompts.
Attackers (or sloppy pages) can hide content from the human reader but not
from the model: zero-width characters inside words, bidi overrides/isolates,
Unicode "tag" characters that spell ASCII, variation-selector byte runs, and
HTML blocks styled invisible. This pure module removes all of that before
analysis and reports what it found so the card can warn the owner.

Plain text only reaches the bot from the bridge (inline text) and from paste,
so :func:`clean_text` is the main entry point. The server-side fetch also
sees HTML (the JSON-LD description), so :func:`strip_hidden_html` (the single
place the excerpt logic lives) drops hidden blocks there; the LinkedIn fragment scraper keeps text only, so hidden markup
is not visible on that path (the character guard still runs on its text).

Legitimate uses are left alone: emoji ZWJ sequences, a single variation
selector after a symbol, flag-subdivision tag sequences (England/Scotland/
Wales flags), ZWJ/ZWNJ inside non-Latin scripts, LRM/RLM/ALM marks and
ideographic variation selectors.

Idea adapted from ai-job-search tools/posting_guard.py (MIT).
"""

import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Final

_BLACK_FLAG: Final = "\U0001f3f4"
_CANCEL_TAG: Final = "\U000e007f"
_VS16: Final = "\ufe0f"
# Flag subdivision sequences (gbeng/gbsct/gbwls...): 2-6 tag letters/digits.
_SUBDIVISION_LEN: Final = range(2, 7)
EXCERPT_LEN: Final = 80

# Removed always; tampering only when sitting inside a word.
_ZERO_WIDTH: Final = frozenset(
    {0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x180E, 0xFEFF}
)
_JOINERS: Final = frozenset({0x200C, 0x200D})
# Embeddings, overrides (202A-202E) and isolates (2066-2069): visual reordering.
_BIDI_TAMPER: Final = frozenset(range(0x202A, 0x202F)) | frozenset(
    range(0x2066, 0x206A)
)
_SOFT_HYPHEN: Final = 0x00AD


def _is_tag_char(cp: int) -> bool:
    return 0xE0000 <= cp <= 0xE007F


def _is_variation_selector(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


@dataclass(frozen=True)
class GuardReport:
    """Cleaned text plus what was removed.

    ``removed`` counts invisible characters taken out; ``hidden_blocks``
    counts hidden HTML elements dropped (a different unit, reported apart);
    ``hidden_text`` carries decoded tag/variation-selector payloads and short
    hidden-block excerpts; ``tampering`` marks structures that have no
    innocent use in an offer (in-word zero-width, bidi override, tag run).
    """

    text: str
    removed: int = 0
    hidden_text: tuple[str, ...] = ()
    tampering: bool = False
    hidden_blocks: int = 0

    @property
    def flagged(self) -> bool:
        return self.tampering or bool(self.hidden_text)


def warning_line(report: GuardReport | None) -> str:
    """The single Spanish card line for a flagged report, else ``""``.

    Counts are reported per kind (characters, hidden blocks), never summed;
    the summed count below only drives singular/plural agreement of the verb.
    """
    if report is None or not report.flagged:
        return ""
    kinds: list[str] = []
    if report.removed:
        noun = "carácter invisible" if report.removed == 1 else "caracteres invisibles"
        kinds.append(f"{report.removed} {noun}")
    if report.hidden_blocks:
        noun = "bloque oculto" if report.hidden_blocks == 1 else "bloques ocultos"
        kinds.append(f"{report.hidden_blocks} {noun}")
    detail = ", ".join(kinds) or "contenido oculto"
    suffix = "eliminado" if report.removed + report.hidden_blocks <= 1 else "eliminados"
    return f"\u26a0\ufe0f Texto oculto en la oferta: {detail} ({suffix})"


def _is_word_char(ch: str) -> bool:
    return ch.isalnum()


def _non_latin_letter(ch: str) -> bool:
    return ch.isalpha() and ord(ch) > 0x24F


def _emoji_like(ch: str) -> bool:
    return unicodedata.category(ch) == "So" or 0x1F3FB <= ord(ch) <= 0x1F3FF


def _is_flag_subdivision(run: str) -> bool:
    """A valid emoji subdivision tag sequence: 2-6 [a-z0-9] tag chars + cancel."""
    if not run.endswith(_CANCEL_TAG):
        return False
    body = run[:-1]
    return len(body) in _SUBDIVISION_LEN and all(
        0xE0061 <= ord(c) <= 0xE007A or 0xE0030 <= ord(c) <= 0xE0039 for c in body
    )


def _decode_tags(run: str) -> str:
    return "".join(chr(ord(c) - 0xE0000) for c in run if 0xE0020 <= ord(c) <= 0xE007E)


def _decode_variation_bytes(run: str) -> str:
    payload = bytes(
        (ord(c) - 0xFE00) if ord(c) <= 0xFE0F else (ord(c) - 0xE0100 + 16) for c in run
    )
    return "".join(
        c for c in payload.decode("utf-8", errors="ignore") if c.isprintable()
    ).strip()


def _neighbour_after(text: str, index: int) -> str:
    """First character after ``index`` that is not itself an invisible filler."""
    j = index + 1
    while j < len(text) and (ord(text[j]) in _ZERO_WIDTH or ord(text[j]) in _JOINERS):
        j += 1
    return text[j] if j < len(text) else ""


def clean_text(text: str) -> GuardReport:
    """Remove invisible characters from ``text`` and report what was found."""
    out: list[str] = []
    removed = 0
    tampering = False
    hidden: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        cp = ord(ch)
        if _is_tag_char(cp):
            j = i
            while j < n and _is_tag_char(ord(text[j])):
                j += 1
            run = text[i:j]
            if i > 0 and text[i - 1] == _BLACK_FLAG and _is_flag_subdivision(run):
                out.append(run)  # legitimate flag-subdivision emoji sequence
            else:
                removed += len(run)
                tampering = True
                decoded = _decode_tags(run).strip()
                if decoded:
                    hidden.append(decoded)
            i = j
            continue
        if _is_variation_selector(cp):
            j = i
            while j < n and _is_variation_selector(ord(text[j])):
                j += 1
            run = text[i:j]
            if len(run) >= 2:
                removed += len(run)
                tampering = True
                decoded = _decode_variation_bytes(run)
                if decoded:
                    hidden.append(decoded)
            else:
                out.append(run)  # emoji/text presentation or ideographic variant
            i = j
            continue
        if cp in _BIDI_TAMPER:
            removed += 1
            tampering = True
        elif cp == _SOFT_HYPHEN:
            removed += 1
        elif cp in _ZERO_WIDTH or cp in _JOINERS:
            prev = out[-1] if out else ""
            nxt = _neighbour_after(text, i)
            if cp in _JOINERS and (
                (
                    prev
                    and nxt
                    and (prev == _VS16 or _emoji_like(prev))
                    and _emoji_like(nxt)
                )
                or (prev and _non_latin_letter(prev))
                or (nxt and _non_latin_letter(nxt))
            ):
                out.append(ch)  # emoji ZWJ sequence / joiner in a non-Latin script
            else:
                removed += 1
                if prev and nxt and _is_word_char(prev) and _is_word_char(nxt):
                    tampering = True
        else:
            out.append(ch)
        i += 1
    return GuardReport(
        text="".join(out),
        removed=removed,
        hidden_text=tuple(hidden),
        tampering=tampering,
    )


# --- hidden HTML blocks ------------------------------------------------------------

_VOID_TAGS: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_ZERO_VALUE_RE: Final = re.compile(r"^0(?:\.0+)?(?:px|pt|em|rem|%|vh|vw)?$")
_HIDDEN_HINT_RE: Final = re.compile(
    r"\bhidden\b|display\s*:\s*none|font-size\s*:\s*0|opacity\s*:\s*0", re.IGNORECASE
)


def _style_hides(style: str) -> bool:
    for declaration in style.split(";"):
        prop, _, value = declaration.partition(":")
        prop, value = prop.strip().lower(), value.strip().lower()
        value = value.replace("!important", "").strip()
        if prop == "display" and value == "none":
            return True
        if prop == "visibility" and value == "hidden":
            return True
        if prop in {"font-size", "opacity"} and _ZERO_VALUE_RE.match(value):
            return True
    return False


def _element_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        lowered = name.lower()
        if lowered == "hidden":
            return True
        if lowered == "aria-hidden" and (value or "").strip().lower() == "true":
            return True
        if lowered == "style" and value and _style_hides(value):
            return True
    return False


# Elements whose end tag is optional, and the open elements a new start tag
# of that name implicitly closes (HTML "optional tags", simplified): browsers
# close them without an explicit end tag, so the tag stack must too.
_P_CLOSERS: Final = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dl",
        "fieldset",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    ]
)
# Inline elements that may be left open inside an implicitly closed element.
_FORMATTING_TAGS: Final = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "big",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "ins",
        "kbd",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "time",
        "tt",
        "u",
        "var",
    }
)
_IMPLICIT_CLOSE: Final[dict[str, frozenset[str]]] = {
    **{name: frozenset({"p"}) for name in _P_CLOSERS},
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "tr": frozenset({"tr", "td", "th"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "option": frozenset({"option"}),
    "optgroup": frozenset({"option", "optgroup"}),
}


class _HiddenBlockStripper(HTMLParser):
    """Rebuild the markup minus hidden elements, collecting their text.

    Inside a hidden element the open tags live on a name stack. An end tag
    only closes when it matches an open element (popping everything above
    it, which covers optional end tags such as ``<p>``/``<li>``/``<td>``);
    a stray end tag is ignored, as browsers do. The hidden region ends only
    when the hidden element itself is popped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.blocks: list[str] = []
        self.dropped = False
        self._stack: list[str] = []
        self._chunks: list[str] = []

    def _finish(self) -> None:
        text = " ".join(unescape("".join(self._chunks)).split())
        if text:
            self.blocks.append(text)
        self._chunks = []

    def _close_implied(self, tag: str) -> None:
        """Pop the open element a start tag of ``tag`` implicitly closes."""
        closes = _IMPLICIT_CLOSE.get(tag)
        if not closes:
            return
        # Open formatting children (<b>, <span>, <a>...) sit above the implied
        # element: pop through them, as a browser's adoption would close them.
        depth = len(self._stack)
        while depth and self._stack[depth - 1] in _FORMATTING_TAGS:
            depth -= 1
        if depth and self._stack[depth - 1] in closes:
            del self._stack[depth - 1 :]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._stack:
            self._close_implied(tag)
            if self._stack:
                if tag not in _VOID_TAGS:
                    self._stack.append(tag)
                return
            self._finish()  # the hidden element was implicitly closed
        if _element_hidden(attrs):
            self.dropped = True
            if tag not in _VOID_TAGS:
                self._stack = [tag]
            return
        self.parts.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._stack:
            return
        if _element_hidden(attrs):
            self.dropped = True
            return
        self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            self.parts.append(f"</{tag}>")
            return
        if tag in _VOID_TAGS or tag not in self._stack:
            return  # stray end tag: browsers ignore it
        while self._stack.pop() != tag:
            pass
        if not self._stack:
            self._finish()

    def handle_data(self, data: str) -> None:
        (self._chunks if self._stack else self.parts).append(data)

    def handle_entityref(self, name: str) -> None:
        (self._chunks if self._stack else self.parts).append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        (self._chunks if self._stack else self.parts).append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if not self._stack:
            self.parts.append(f"<!--{data}-->")

    def close(self) -> None:
        super().close()
        if self._stack:
            self._stack = []
            self._finish()


def scan_hidden_html(markup: str) -> tuple[str, list[str]]:
    """Markup without hidden elements plus the FULL text of each hidden block."""
    if not _HIDDEN_HINT_RE.search(markup) and "aria-hidden" not in markup.lower():
        return markup, []
    stripper = _HiddenBlockStripper()
    stripper.feed(markup)
    stripper.close()
    if not stripper.dropped:
        return markup, []
    return "".join(stripper.parts), stripper.blocks


def strip_hidden_html(markup: str) -> tuple[str, list[str]]:
    """Drop hidden elements; return the markup and short excerpts of what hid."""
    cleaned, blocks = scan_hidden_html(markup)
    return cleaned, [block[:EXCERPT_LEN] for block in blocks]
