from pathlib import Path

import pytest

from recall.extractors import Context, extract, rewrite_markdown_links


@pytest.fixture()
def ctx(notes, tmp_path):
    return Context(root=notes, cache_dir=tmp_path / "cache")


def cached_images(ctx, md: str) -> list[Path]:
    import re

    return [ctx.cache_dir / n for n in re.findall(r"/api/cache-image/([0-9a-f]+\.\w+)", md)]


def test_markdown_frontmatter_title_and_tags(notes, ctx):
    d = extract(notes / "networking/vlans.md", ctx)
    assert d.title == "VLAN Setup Guide"
    assert d.tags == ["networking", "homelab"]
    assert not d.markdown.startswith("---")


def test_markdown_relative_image_rewritten(notes, ctx):
    md = extract(notes / "networking/vlans.md", ctx).markdown
    assert "![VLAN diagram](/api/file?path=images/vlan-diagram.png)" in md


def test_markdown_obsidian_embed_and_wikilinks(notes, ctx):
    md = extract(notes / "networking/vlans.md", ctx).markdown
    assert "![topology](/api/file?path=networking/topology.png)" in md
    assert "[backups](#doc=backups.md)" in md
    assert "[the router notes](#doc=networking/router.md)" in md


def test_markdown_embed_size_kept_for_previewer(notes, ctx):
    md = rewrite_markdown_links("![[topology.png|300]] ![[topology.png|300x200]] ![[topology.png|Net map]]", ctx,
                                notes / "backups.md")
    url = "/api/file?path=networking/topology.png"
    assert md == f"![topology|300]({url}) ![topology|300x200]({url}) ![Net map]({url})"


def test_markdown_code_blocks_untouched(notes, ctx):
    md = extract(notes / "networking/vlans.md", ctx).markdown
    assert 'echo "![fake](nothere.png)"' in md


def test_markdown_path_traversal_not_resolved(notes, ctx, tmp_path):
    (tmp_path / "outside.png").write_bytes(b"x")
    md = rewrite_markdown_links("![x](../../outside.png)", ctx, notes / "networking/vlans.md")
    assert md == "![x](../../outside.png)"  # left as-is, never mapped to /api/file


def test_markdown_external_links_untouched(notes, ctx):
    md = rewrite_markdown_links("![a](https://x.org/a.png) [b](https://x.org)", ctx, notes / "backups.md")
    assert md == "![a](https://x.org/a.png) [b](https://x.org)"


def test_pdf_pages_text_and_images(notes, ctx):
    d = extract(notes / "k8s-upgrade.pdf", ctx)
    assert d.title == "K8s Upgrade"
    # The larger-font first line becomes a section anchored to its page; page 2 continues inside it.
    assert "## Kubernetes cluster upgrade procedure {#page-1}" in d.markdown and "### Page 2" in d.markdown
    assert "Drain each node" in d.markdown and "etcd snapshot" in d.markdown
    imgs = cached_images(ctx, d.markdown)
    assert len(imgs) == 1 and imgs[0].is_file() and imgs[0].stat().st_size > 0


def _manual_pdf(path, outline=True):
    """Three pages with a running header/footer, section titles and a ruled table on page 2."""
    import pymupdf as fitz

    doc = fitz.open()
    sections = ["Installing the agent", "Network ports", "Uninstalling"]
    for i, title in enumerate(sections, 1):
        p = doc.new_page()
        p.insert_text((72, 30), "Acme Guide - Confidential", fontsize=8)
        p.insert_text((72, p.rect.height - 25), f"Page {i} of 3", fontsize=8)
        p.insert_text((72, 90), title, fontsize=18)
        p.insert_text((72, 130), f"Body text about {title.lower()} goes here.", fontsize=11)
        if i == 2:
            for r, row in enumerate([["Port", "Use"], ["443", "Console"], ["8443", "Gateway"]]):
                for c, cell in enumerate(row):
                    box = fitz.Rect(72 + c * 120, 160 + r * 22, 192 + c * 120, 182 + r * 22)
                    p.draw_rect(box, color=(0, 0, 0), width=0.6)
                    p.insert_text((box.x0 + 4, box.y1 - 6), cell, fontsize=10)
    if outline:
        doc.set_toc([[1, t, i] for i, t in enumerate(sections, 1)])
    doc.save(path)
    doc.close()


def test_pdf_sections_from_outline_tables_and_running_headers(tmp_path, ctx):
    _manual_pdf(tmp_path / "guide.pdf")
    md = extract(tmp_path / "guide.pdf", ctx).markdown
    assert "## Installing the agent {#page-1}" in md and "## Network ports {#page-2}" in md
    assert "Confidential" not in md and "of 3" not in md  # running header/footer dropped
    assert "| Port | Use |\n| --- | --- |\n| 443 | Console |\n| 8443 | Gateway |" in md
    assert md.count("| 443 |") == 1 and "443 Console" not in md  # table cells aren't repeated as text


def test_pdf_sections_from_font_sizes_without_outline(tmp_path, ctx):
    _manual_pdf(tmp_path / "guide.pdf", outline=False)
    md = extract(tmp_path / "guide.pdf", ctx).markdown
    assert "## Network ports {#page-2}" in md and "## Uninstalling {#page-3}" in md


def test_pdf_without_structure_keeps_page_sections(tmp_path, ctx):
    import pymupdf as fitz

    doc = fitz.open()
    for i in range(2):
        doc.new_page().insert_text((72, 90), f"Plain page {i + 1} text.", fontsize=11)
    doc.save(tmp_path / "plain.pdf")
    md = extract(tmp_path / "plain.pdf", ctx).markdown
    assert "## Page 1" in md and "## Page 2" in md


def test_docx_headings_lists_tables_images(notes, ctx):
    d = extract(notes / "onboarding.docx", ctx)
    assert d.title == "Onboarding Handbook"
    assert "# Laptop setup" in d.markdown
    assert "- Request Git access" in d.markdown
    assert "| IT helpdesk | it@example.com |" in d.markdown
    imgs = cached_images(ctx, d.markdown)
    assert len(imgs) == 1 and imgs[0].is_file()


def test_pptx_slides_notes_images(notes, ctx):
    d = extract(notes / "roadmap.pptx", ctx)
    assert "## Slide 1: Quarterly roadmap" in d.markdown
    assert "- Migrate monitoring to Prometheus" in d.markdown
    assert "Speaker notes:** Mention Grafana" in d.markdown
    assert len(cached_images(ctx, d.markdown)) == 1


def test_xlsx_sheets_as_tables(notes, ctx):
    md = extract(notes / "inventory.xlsx", ctx).markdown
    assert "## Sheet: Inventory" in md and "| nas01 | 10.0.10.5 | storage |" in md


def test_csv_as_table(notes, ctx):
    md = extract(notes / "subnets.csv", ctx).markdown
    assert md.splitlines()[0] == "| vlan | subnet | purpose |"
    assert "| 20 | 10.0.20.0/24 | iot |" in md


def test_html_strips_scripts_keeps_structure(notes, ctx):
    d = extract(notes / "wiki.html", ctx)
    assert d.title == "Wiki Export"
    assert "# DNS" in d.markdown and "- Upstream: Quad9" in d.markdown
    assert "alert(1)" not in d.markdown
    assert "/api/file?path=images/vlan-diagram.png" in d.markdown


def test_text_kind(notes, ctx):
    d = extract(notes / "todo.txt", ctx)
    assert d.kind == "text" and "UPS battery" in d.markdown


def test_unsupported_and_broken(notes, ctx):
    with pytest.raises(ValueError):
        extract(notes / "images/vlan-diagram.png", ctx)
    with pytest.raises(Exception):
        extract(notes / "broken.pdf", ctx)


def test_parallel_pdf_reading_matches_serial(tmp_path, ctx, monkeypatch):
    from recall.extractors import pdf

    _manual_pdf(tmp_path / "guide.pdf")
    serial = extract(tmp_path / "guide.pdf", ctx).markdown
    monkeypatch.setattr(pdf, "PARALLEL_MIN_PAGES", 2)
    monkeypatch.setenv("RECALL_PDF_WORKERS", "2")
    assert extract(tmp_path / "guide.pdf", ctx).markdown == serial


def test_extract_cache_reused_until_file_changes(tmp_path, ctx, monkeypatch):
    import os

    from recall.extractors import EXTRACTORS
    from recall.extractors.cache import ExtractCache

    _manual_pdf(tmp_path / "guide.pdf")
    calls = []
    real = EXTRACTORS["pdf"]
    monkeypatch.setitem(EXTRACTORS, "pdf", lambda p, c: calls.append(p) or real(p, c))
    cache = ExtractCache(tmp_path / "cache")
    first = cache.extract(tmp_path / "guide.pdf", "guide.pdf", ctx, extract)
    assert cache.extract(tmp_path / "guide.pdf", "guide.pdf", ctx, extract) == first and len(calls) == 1
    st = (tmp_path / "guide.pdf").stat()
    os.utime(tmp_path / "guide.pdf", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    cache.extract(tmp_path / "guide.pdf", "guide.pdf", ctx, extract)
    assert len(calls) == 2  # changed file: read again
    (tmp_path / "n.md").write_text("# N")
    cache.extract(tmp_path / "n.md", "n.md", ctx, extract)
    assert not cache._file("n.md").exists()  # Markdown is quick to read: not cached
    cache.delete("guide.pdf")
    assert cache.get(tmp_path / "guide.pdf", "guide.pdf") is None
