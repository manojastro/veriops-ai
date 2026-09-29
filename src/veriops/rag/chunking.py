"""Markdown-aware chunking: split on headings, then pack paragraphs up to a size limit with overlap."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    content: str


def chunk_markdown(text: str, max_chars: int = 900, overlap_chars: int = 120) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(2).strip(), []))
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        buffer = ""
        for paragraph in paragraphs:
            if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
                chunks.append(Chunk(len(chunks), heading, buffer))
                buffer = buffer[-overlap_chars:].split(" ", 1)[-1] + "\n\n" + paragraph if overlap_chars else paragraph
            else:
                buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
            while len(buffer) > max_chars * 1.5:  # hard-split very long paragraphs
                chunks.append(Chunk(len(chunks), heading, buffer[:max_chars]))
                buffer = buffer[max_chars - overlap_chars :]
        if buffer:
            chunks.append(Chunk(len(chunks), heading, buffer))
    return chunks
