"""Keeps one service's entry in a parent Confluence page's services section.

A parent page (for example "Portal") is written by people. Each child service
that opts in owns exactly one entry in a marked section of it, and only that
entry is ever changed:

    <p>[anchor autodocs-services-start]</p>
    <h2>Services in this area</h2>
    <p>[anchor autodocs-service-<name>]<strong>[link to child page]</strong>: summary</p>
    ...
    <p>[anchor autodocs-services-end]</p>

The markers are Confluence anchor macros, which survive editing in the
Confluence editor. Everything outside the section, and every other service's
entry inside it, is left byte-for-byte as it was. A parent with no section gets
one appended at the end; a section without this service gets its entry inserted
in alphabetical order.
"""

import html
import re

SECTION_START = "autodocs-services-start"
SECTION_END = "autodocs-services-end"
ENTRY_PREFIX = "autodocs-service-"
SECTION_HEADING = "Services in this area"
MAX_SUMMARY_CHARS = 600
MIN_SUMMARY_CHARS = 20


def anchor_macro(name: str) -> str:
    return (
        '<ac:structured-macro ac:name="anchor">'
        f'<ac:parameter ac:name="">{html.escape(name)}</ac:parameter>'
        "</ac:structured-macro>"
    )


def _anchor_pattern(name_pattern: str) -> str:
    # Confluence adds attributes such as ac:schema-version and ac:macro-id when
    # a page is saved in its editor, so attributes are matched loosely.
    return (
        r'<ac:structured-macro\b[^>]*\bac:name="anchor"[^>]*>\s*'
        r'<ac:parameter\b[^>]*\bac:name=""[^>]*>\s*'
        f"({name_pattern})"
        r"\s*</ac:parameter>\s*</ac:structured-macro>"
    )


def _find_anchor(body: str, name: str, start: int = 0) -> re.Match[str] | None:
    return re.compile(_anchor_pattern(re.escape(name))).search(body, start)


def _entry_regex() -> re.Pattern[str]:
    # One entry: a paragraph whose content starts with an entry anchor.
    return re.compile(
        r"<p\b[^>]*>\s*"
        + _anchor_pattern(re.escape(ENTRY_PREFIX) + r"[^<\s]+")
        + r".*?</p>",
        flags=re.DOTALL,
    )


def normalise_summary(text: str) -> str:
    """Collapse the model's summary to one paragraph of plain text, or fail."""
    summary = re.sub(r"\s+", " ", text).strip()
    if len(summary) < MIN_SUMMARY_CHARS:
        raise ValueError(f"parent summary is too short ({len(summary)} characters)")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ValueError(
            f"parent summary is {len(summary)} characters; the limit is {MAX_SUMMARY_CHARS}"
        )
    if re.search(r"<[a-zA-Z/!]", summary) or "**" in summary or summary.startswith("#"):
        raise ValueError("parent summary must be plain text, with no markup")
    return summary


def build_entry(service: str, child_title: str, summary: str) -> str:
    link = (
        "<ac:link>"
        f'<ri:page ri:content-title="{html.escape(child_title, quote=True)}" />'
        "</ac:link>"
    )
    return (
        f"<p>{anchor_macro(ENTRY_PREFIX + service)}"
        f"<strong>{link}</strong>: {html.escape(summary, quote=False)}</p>"
    )


def build_section(entries: list[str]) -> str:
    return (
        f"<p>{anchor_macro(SECTION_START)}</p>"
        f"<h2>{SECTION_HEADING}</h2>"
        + "".join(entries)
        + f"<p>{anchor_macro(SECTION_END)}</p>"
    )


def _section_bounds(body: str) -> tuple[int, int] | None:
    """Character range between the start and end markers, or None if absent."""
    start = _find_anchor(body, SECTION_START)
    if not start:
        return None
    end = _find_anchor(body, SECTION_END, start.end())
    if not end:
        raise ValueError(
            "parent page has the services start marker but no end marker; "
            "fix the page by hand rather than guess where the section ends"
        )
    return start.end(), end.start()


def _entries(body: str, bounds: tuple[int, int]) -> list[tuple[str, int, int]]:
    """(service, start, end) for each entry inside the section."""
    lo, hi = bounds
    found = []
    for match in _entry_regex().finditer(body, lo, hi):
        name = match.group(1)[len(ENTRY_PREFIX) :]
        found.append((name, match.start(), match.end()))
    return found


def _end_insertion_point(body: str, bounds: tuple[int, int]) -> int:
    """Where a new last entry goes: before the paragraph holding the end marker."""
    lo, hi = bounds
    p_open = body.rfind("<p", lo, hi)
    if p_open != -1 and re.fullmatch(r"<p\b[^>]*>\s*", body[p_open:hi]):
        return p_open
    return hi


def existing_entry_text(body: str, service: str) -> str | None:
    """Plain text of this service's current summary, or None if it has no entry."""
    bounds = _section_bounds(body)
    if not bounds:
        return None
    for name, start, end in _entries(body, bounds):
        if name == service:
            entry = body[start:end]
            after_link = re.split(r"</strong>\s*:", entry, maxsplit=1)
            text = after_link[1] if len(after_link) == 2 else entry
            text = re.sub(r"<[^>]+>", "", text)
            return html.unescape(re.sub(r"\s+", " ", text)).strip()
    return None


def upsert_service_entry(
    body: str, service: str, child_title: str, summary: str
) -> str:
    """Return the parent body with this service's entry added or replaced."""
    entry = build_entry(service, child_title, normalise_summary(summary))
    bounds = _section_bounds(body)
    if not bounds:
        separator = "\n" if body.strip() else ""
        return body.rstrip() + separator + build_section([entry])
    entries = _entries(body, bounds)
    for name, start, end in entries:
        if name == service:
            return body[:start] + entry + body[end:]
    for name, start, _ in entries:
        if name > service:
            return body[:start] + entry + body[start:]
    point = _end_insertion_point(body, bounds)
    return body[:point] + entry + body[point:]
