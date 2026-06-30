"""Ingest user-supplied sources — web links and uploaded PDFs.

The *Improve* flow lets a user attach their own reference material: one or more
URLs, or PDF documents. We fetch/extract the text, trim it to a sane size, and
turn each into a :class:`~blog_writer.workflows.state.Citation` the rest of the
pipeline already knows how to weave in (fact-check, critic, and the Writer's
numbered ``[n](url)`` citations).

Kept as a standalone tool (not inline in the pipeline) so the network/PDF
parsing — and its size caps and failure handling — live in one place and are easy
to unit-test without spinning up the whole pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO

from blog_writer.workflows.state import Citation

logger = logging.getLogger(__name__)

# Per-source extracted-text cap so one huge document can't dominate the context
# window. The Writer only needs enough to ground and cite, not the whole file.
MAX_SOURCE_CHARS = 6_000

# Total budget across all user sources.
MAX_TOTAL_CHARS = 24_000

# Network guard for link fetches.
FETCH_TIMEOUT_SECONDS = 20.0
MAX_DOWNLOAD_BYTES = 10_000_000  # 10 MB


@dataclass
class UserPDF:
    """An uploaded PDF: original filename + raw bytes."""

    filename: str
    data: bytes


def _clean_text(text: str) -> str:
    """Collapse whitespace and trim to the per-source cap."""
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS].rstrip() + "\n\n…(source truncated)…"
    return text


def _title_from_html(html: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if title:
            return title[:200]
    return fallback


def _strip_html(html: str) -> str:
    """Very small HTML → text reduction (no extra dependency)."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|h[1-6]|li|tr)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    # Unescape the few entities that matter for readable prose.
    for entity, char in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&nbsp;", " "),
    ):
        text = text.replace(entity, char)
    return text


def extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes. Returns '' on any parse failure."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        logger.warning("pypdf is not installed — cannot extract PDF text.")
        return ""
    try:
        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > MAX_SOURCE_CHARS * 2:
                break
        return "\n\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 - pypdf raises a variety of errors
        logger.warning("Failed to parse PDF: %s", exc)
        return ""


def citation_from_pdf(pdf: UserPDF, *, index: int) -> Citation | None:
    """Build a Citation from an uploaded PDF, or None when it has no text."""
    text = _clean_text(extract_pdf_text(pdf.data))
    if not text:
        return None
    name = pdf.filename or f"document-{index}.pdf"
    return Citation(
        key=f"U{index}",
        kind="external",
        title=name,
        url=f"attachment://{name}",
        summary=text,
    )


def _fetch_url(url: str) -> Citation | None:
    """Fetch a URL and turn it into a Citation. Returns None on failure."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        logger.warning("httpx is not installed — cannot fetch links.")
        return None
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "blog-writer/1.0 (+source-ingest)"},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            raw = resp.content[:MAX_DOWNLOAD_BYTES]
    except Exception as exc:  # noqa: BLE001 - network errors are varied
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None

    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        text = _clean_text(extract_pdf_text(raw))
        title = url.rsplit("/", 1)[-1] or url
    else:
        html = raw.decode("utf-8", errors="replace")
        title = _title_from_html(html, fallback=url)
        text = _clean_text(_strip_html(html))

    if not text:
        return None
    return Citation(key="U", kind="external", title=title, url=url, summary=text)


def ingest_user_sources(
    *,
    links: list[str] | None = None,
    pdfs: list[UserPDF] | None = None,
) -> list[Citation]:
    """Turn user-supplied links + PDFs into Citations, honoring size budgets.

    Failures (unreachable link, unparseable PDF) are skipped with a log line so a
    single bad input never aborts the whole Improve run.
    """
    citations: list[Citation] = []
    total = 0
    index = 1

    for url in links or []:
        url = (url or "").strip()
        if not url:
            continue
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url
        citation = _fetch_url(url)
        if citation is None:
            continue
        if total + len(citation.summary) > MAX_TOTAL_CHARS:
            break
        citation.key = f"U{index}"
        citations.append(citation)
        total += len(citation.summary)
        index += 1

    for pdf in pdfs or []:
        citation = citation_from_pdf(pdf, index=index)
        if citation is None:
            continue
        if total + len(citation.summary) > MAX_TOTAL_CHARS:
            break
        citations.append(citation)
        total += len(citation.summary)
        index += 1

    return citations


__all__ = [
    "MAX_SOURCE_CHARS",
    "MAX_TOTAL_CHARS",
    "UserPDF",
    "citation_from_pdf",
    "extract_pdf_text",
    "ingest_user_sources",
]
