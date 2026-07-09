"""Split SRD markdown into heading-addressed sections for the FTS index.

The vendored fork mixes setext headings (`===` h1, `---` h2) with ATX
(`###`+). Table rows also contain runs of dashes, so a setext underline only
counts when the *previous* line is non-empty and is not itself a table row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_H1 = re.compile(r"^=+\s*$")
_SETEXT_H2 = re.compile(r"^-{3,}\s*$")


@dataclass
class Section:
    source: str
    heading_path: str
    heading: str
    body: str


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def parse_sections(text: str, source: str) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    # stack of (level, title); body lines accumulate for the deepest heading
    stack: list[tuple[int, str]] = []
    body: list[str] = []

    def flush() -> None:
        if not stack:
            return
        content = "\n".join(body).strip()
        if content:
            sections.append(
                Section(
                    source=source,
                    heading_path=" > ".join(title for _, title in stack),
                    heading=stack[-1][1],
                    body=content,
                )
            )
        body.clear()

    def open_heading(level: int, title: str) -> None:
        flush()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        prev_ok = line.strip() and not _is_table_row(line)
        if prev_ok and _SETEXT_H1.match(nxt):
            open_heading(1, line.strip())
            i += 2
            continue
        if prev_ok and _SETEXT_H2.match(nxt) and not _is_table_row(nxt):
            open_heading(2, line.strip())
            i += 2
            continue
        m = _ATX.match(line)
        if m:
            open_heading(len(m.group(1)), m.group(2))
            i += 1
            continue
        body.append(line)
        i += 1

    flush()
    return sections
