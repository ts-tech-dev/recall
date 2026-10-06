"""OCR of images and scanned PDFs, AI captions, and how image text reaches search and answers."""

import pytest

from recall import images as images_mod
from recall import llm
from recall.answer import build_prompt, image_catalogue, retrieve
from recall.config import Settings
from recall.extractors import Context, extract
from recall.images import ocr_available, ocr_bytes
from recall.index import Index
from tests.conftest import make_png, make_text_png

pytestmark = pytest.mark.skipif(not ocr_available(), reason="rapidocr not installed")


@pytest.fixture()
def ocr_index(image_notes, data):
    idx = Index(image_notes, data, ocr=True)
    idx.build()
    return idx


def test_ocr_reads_text():
    assert "8443" in ocr_bytes(make_text_png("Allow TCP 8443 from DMZ"))
    assert ocr_bytes(make_png()) == ""  # no text
    assert ocr_bytes(make_text_png("tiny", w=60, h=40)) == ""  # icons are skipped
    assert ocr_bytes(b"not an image") == ""


def test_scanned_pdf_page_is_ocred(image_notes, tmp_path):
    d = extract(image_notes / "scanned-invoice.pdf", Context(image_notes, tmp_path / "c", ocr=ocr_bytes))
    assert "Text recognized by OCR" in d.markdown and "4,250" in d.markdown
    assert "/api/cache-image/" not in d.markdown  # the page scan isn't repeated as a figure
    no_ocr = extract(image_notes / "scanned-invoice.pdf", Context(image_notes, tmp_path / "c"))
    assert "4,250" not in no_ocr.markdown


def test_text_in_note_images_is_searchable(ocr_index):
    r = ocr_index.search("8443 DMZ")[0]
    assert r["path"] == "firewall/edge.md"
    assert "text in image" in r["text"]


def test_standalone_images_with_text_are_searchable(ocr_index):
    r = ocr_index.search("OKR brainstorm")[0]
    assert r["path"] == "whiteboard.png" and r["ext"] == ".png"
    assert r["images"][0]["url"] == "/api/file?path=whiteboard.png"
    st = ocr_index.stats()
    assert st["images"] == 4 and ".png" not in st["by_type"]  # images counted separately from notes


def test_scanned_pdf_searchable(ocr_index):
    assert ocr_index.search("invoice total due")[0]["path"] == "scanned-invoice.pdf"


def test_ocr_off_means_no_image_text(image_notes, data):
    idx = Index(image_notes, data, ocr=False)
    idx.build()
    assert idx.search("8443 DMZ") == []


def test_ocr_cached_by_content(ocr_index, image_notes, monkeypatch):
    calls = []
    real = images_mod.ocr_bytes
    monkeypatch.setattr(images_mod, "ocr_bytes", lambda b: calls.append(1) or real(b))
    (image_notes / "firewall" / "edge.md").write_text("# Edge firewall\n\nChanged.\n\n![rules](rules.png)\n")
    assert ocr_index.build()["updated"] == 1
    assert calls == []  # the image itself didn't change


# ------------------------------------------------------------------ captions


def test_captions_fold_into_search_and_prompt(image_notes, data):
    idx = Index(image_notes, data, ocr=False)
    seen = []

    def captioner(data_bytes, media_type):
        seen.append(media_type)
        return "A network diagram of the zebra datacenter"

    idx.captioner = captioner
    s = idx.build()
    assert s["captioned"] == len(seen) > 0
    assert all(m == "image/png" for m in seen)
    r = idx.search("zebra datacenter", limit=30)
    note_hit = next(x for x in r if x["path"] == "firewall/edge.md")
    assert "[image 'rules' shows: A network diagram" in note_hit["text"]
    assert "firewall/rules.png" not in [x["path"] for x in r]  # embedded image: found via its note
    assert "whiteboard.png" in [x["path"] for x in r]  # loose image: found on its own
    # a second build doesn't caption the same images again
    assert idx.build().get("captioned") == 0
    hits = retrieve(idx, "zebra datacenter", None, 5)
    cat = image_catalogue(hits)
    assert cat[0]["caption"].startswith("A network diagram")
    assert 'description="A network diagram' in build_prompt("q", "summary", hits, cat)


def test_caption_limit_and_failure(image_notes, data):
    idx = Index(image_notes, data, ocr=False)
    idx.captioner = lambda b, m: "x"
    idx.caption_limit = 1
    assert idx.build()["captioned"] == 1

    idx2 = Index(image_notes, data, ocr=False)

    def boom(b, m):
        raise RuntimeError("rate limited")

    idx2.captioner = boom
    assert idx2.build()["captioned"] == 0  # stops cleanly, retries next run


def test_caption_image_strips_refusal_note(monkeypatch):
    monkeypatch.setattr(llm, "stream_answer", lambda *a, **k: iter(["\n\n> The model declined to answer this request."]))
    assert llm.caption_image(Settings(api_key="k"), "AAA", "image/png") == ""
    monkeypatch.setattr(llm, "stream_answer", lambda s, *a, **k: iter([f"A chart ({s.model}, {s.effort})"]))
    out = llm.caption_image(Settings(api_key="k", caption_model="claude-haiku-4-5"), "AAA", "image/png")
    assert out == "A chart (claude-haiku-4-5, low)"


def test_ocr_runs_after_text_is_searchable(image_notes, data, monkeypatch):
    """A document's text is indexed before its images are OCR'd, then the image text is folded in."""
    idx = Index(image_notes, data, ocr=True)
    seen_during_ocr = []
    real = images_mod.ocr_bytes

    def ocr(b):
        if not seen_during_ocr:
            seen_during_ocr.append([r["path"] for r in idx.search("Edge firewall rule set", mode="keyword")])
        return real(b)

    monkeypatch.setattr(images_mod, "ocr_bytes", ocr)
    s = idx.build()
    assert "firewall/edge.md" in seen_during_ocr[0]  # searchable while OCR was still running
    assert s["ocr"] >= 1
    assert "firewall/edge.md" in [r["path"] for r in idx.search("8443 DMZ")]  # image text arrived afterwards
