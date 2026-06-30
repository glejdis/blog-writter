"""Tests for user-supplied source ingestion (links + PDFs) in Improve."""

from __future__ import annotations

import pytest

from blog_writer.config import AppConfig
from blog_writer.tools import user_sources
from blog_writer.tools.user_sources import (
    UserPDF,
    citation_from_pdf,
    ingest_user_sources,
)
from blog_writer.workflows import improve_blog_post
from blog_writer.workflows.state import Citation

# A minimal, valid single-page PDF whose content stream draws the text
# "Hello source world". Enough for pypdf.extract_text() to return it.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"5 0 obj\n<< /Length 58 >>\nstream\n"
    b"BT /F1 24 Tf 72 700 Td (Hello source world) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000241 00000 n \n"
    b"0000000322 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
    b"startxref\n430\n%%EOF\n"
)


def test_extract_pdf_text_reads_content() -> None:
    text = user_sources.extract_pdf_text(_MINIMAL_PDF)
    assert "Hello source world" in text


def test_extract_pdf_text_handles_garbage() -> None:
    assert user_sources.extract_pdf_text(b"not a pdf at all") == ""


def test_citation_from_pdf_builds_external_citation() -> None:
    pdf = UserPDF(filename="spec.pdf", data=_MINIMAL_PDF)
    citation = citation_from_pdf(pdf, index=1)
    assert citation is not None
    assert citation.kind == "external"
    assert citation.title == "spec.pdf"
    assert citation.url == "attachment://spec.pdf"
    assert "Hello source world" in citation.summary


def test_citation_from_pdf_returns_none_for_empty() -> None:
    assert citation_from_pdf(UserPDF(filename="x.pdf", data=b"junk"), index=1) is None


def test_ingest_user_sources_from_pdfs_only() -> None:
    pdfs = [UserPDF(filename="a.pdf", data=_MINIMAL_PDF)]
    hits = ingest_user_sources(links=None, pdfs=pdfs)
    assert len(hits) == 1
    assert hits[0].key == "U1"
    assert hits[0].url == "attachment://a.pdf"


def test_ingest_user_sources_fetches_links(monkeypatch) -> None:
    def fake_fetch(url: str) -> Citation:
        return Citation(
            key="U",
            kind="external",
            title="Example",
            url=url,
            summary="Fetched body text about Azure.",
        )

    monkeypatch.setattr(user_sources, "_fetch_url", fake_fetch)
    hits = ingest_user_sources(links=["example.com/post"], pdfs=None)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/post"  # scheme auto-added
    assert hits[0].key == "U1"


def test_ingest_user_sources_skips_failed_links(monkeypatch) -> None:
    monkeypatch.setattr(user_sources, "_fetch_url", lambda url: None)
    hits = ingest_user_sources(links=["http://broken"], pdfs=None)
    assert hits == []


@pytest.mark.asyncio
async def test_improve_merges_user_sources_first(tmp_path) -> None:
    config = AppConfig(
        stub=True,
        provider="stub",
        sandbox="stub",
        critic_threshold=0,
        drafts_dir=tmp_path / "drafts",
        samples_dir=tmp_path / "samples",
    )
    draft = "# My Post\n\nA paragraph about Azure landing zones.\n"
    state = await improve_blog_post(
        draft,
        config=config,
        rewrite=False,
        user_pdfs=[UserPDF(filename="my-source.pdf", data=_MINIMAL_PDF)],
    )
    # The user PDF should appear in external hits and lead the list (E1).
    assert any(c.url == "attachment://my-source.pdf" for c in state.external_hits)
    assert state.external_hits[0].key == "E1"
