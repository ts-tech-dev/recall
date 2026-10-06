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
    assert "## Page 1" in d.markdown and "## Page 2" in d.markdown
    assert "Drain each node" in d.markdown and "etcd snapshot" in d.markdown
    imgs = cached_images(ctx, d.markdown)
    assert len(imgs) == 1 and imgs[0].is_file() and imgs[0].stat().st_size > 0


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
