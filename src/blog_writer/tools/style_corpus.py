"""Load the style corpus — example posts the Stylist agent learns from.

The corpus lives in ``knowledge_base/style_corpus/`` as Markdown files. Each file
is one example post (or a reference note) written in the target "house style"
(e.g. The Cloud Wire). The Stylist agent reads the concatenated corpus and
distills a reusable Style Card.

Keeping this in a dedicated loader (rather than inline in the pipeline) means the
corpus can grow without touching orchestration code, and the size cap lives in
one place so a large drop of examples can't blow the model context window.
"""

from __future__ import annotations

from pathlib import Path

# Total characters of corpus text to feed the Stylist. Generous enough for a
# handful of posts; trims the tail if the corpus grows large.
MAX_CORPUS_CHARS = 24_000

# Per-file cap so one giant file can't crowd out the others.
MAX_FILE_CHARS = 8_000


def style_corpus_files(corpus_dir: Path) -> list[Path]:
    """Return the Markdown corpus files in deterministic order.

    Files whose names start with ``_`` (e.g. ``_structural-patterns.md``) sort
    first naturally, so shared references lead and example posts follow.
    """
    if not corpus_dir.exists() or not corpus_dir.is_dir():
        return []
    return sorted(
        p
        for p in corpus_dir.glob("*.md")
        if p.is_file() and p.name.lower() != "readme.md"
    )


def load_style_corpus(corpus_dir: Path) -> str:
    """Concatenate the corpus into a single delimited string for the Stylist.

    Returns an empty string when the corpus is missing or empty, which the
    pipeline treats as "skip the style stage".
    """
    files = style_corpus_files(corpus_dir)
    if not files:
        return ""

    chunks: list[str] = []
    total = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n\n…(example truncated)…"
        block = f"--- BEGIN EXAMPLE: {path.name} ---\n{text}\n--- END EXAMPLE: {path.name} ---"
        if total + len(block) > MAX_CORPUS_CHARS:
            break
        chunks.append(block)
        total += len(block)

    return "\n\n".join(chunks)


__all__ = ["MAX_CORPUS_CHARS", "MAX_FILE_CHARS", "load_style_corpus", "style_corpus_files"]
