from recall import linkcheck
from tests.conftest import make_png


def test_broken_links_and_unlinked_notes(index, notes):
    (notes / "images" / "ok.png").write_bytes(make_png())
    (notes / "links.md").write_text(
        "# Links\n\n"
        "Fine: [[backups]], [[router|the router]], ![[ok.png]], [vlans](networking/vlans.md), "
        "[web](https://example.com), [top](#links), ![pic](images/ok.png).\n"
        "Broken: [[Nowhere]] and ![[missing.png]]\n"
        "and [gone](old/gone.md) and ![x](images/none.png) and <img src=\"none.svg\">.\n"
        "Code is skipped: `[[Nowhere]]`\n\n"
        "```\n[[Nowhere]]\n```\n")
    (notes / "templates").mkdir()
    (notes / "templates" / "Daily.md").write_text("# {{date}}\n\n[[{{title}}]]\n")
    index.build()
    r = linkcheck.check(index)
    assert [b["path"] for b in r["broken"]] == ["links.md"]
    assert [(l["line"], l["link"]) for l in r["broken"][0]["links"]] == [
        (4, "[[Nowhere]]"), (4, "![[missing.png]]"), (5, "[gone](old/gone.md)"), (5, "![x](images/none.png)"),
        (5, '<img src="none.svg"'),
    ]
    orphans = {o["path"] for o in r["orphans"]}
    assert "links.md" in orphans and "templates/Daily.md" not in orphans
    assert "backups.md" not in orphans and "networking/vlans.md" not in orphans  # links.md links to them
    assert all(o.endswith(".md") for o in orphans)  # PDFs and Office files are not "unlinked notes"
