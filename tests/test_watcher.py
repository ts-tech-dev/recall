import time

import pytest

from recall.index import Index
from recall.watcher import Watcher, relevant


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_relevant_filter(notes):
    assert relevant(notes, str(notes / "a.md"))
    assert relevant(notes, str(notes / "x" / "y.pdf"))
    assert relevant(notes, str(notes / "pic.png"))
    assert not relevant(notes, str(notes / ".git" / "index"))
    assert not relevant(notes, str(notes / ".recall-tmp123"))
    assert not relevant(notes, str(notes / "~$lock.docx"))
    assert not relevant(notes, str(notes / "notes.swp"))
    assert not relevant(notes, "/etc/passwd")


@pytest.fixture()
def watched(index):
    w = Watcher(index, debounce=0.3)
    if not w.start():
        pytest.skip("watchdog unavailable")
    yield index
    w.stop()


def test_new_changed_and_deleted_files_are_indexed(watched, notes):
    v = watched.version
    (notes / "fresh.md").write_text("# Fresh\n\nAardvark migration notes")
    assert wait_for(lambda: watched.search("aardvark"))
    assert watched.version > v and "fresh.md" in watched.last_changed

    (notes / "fresh.md").write_text("# Fresh\n\nNow about narwhals")
    assert wait_for(lambda: watched.search("narwhals") and not watched.search("aardvark"))

    (notes / "fresh.md").unlink()
    assert wait_for(lambda: not watched.search("narwhals"))


def test_burst_of_changes_debounced(watched, notes, monkeypatch):
    builds = []
    real = Index.build
    monkeypatch.setattr(watched, "build", lambda *a, **k: builds.append(1) or real(watched, *a, **k))
    for i in range(15):
        (notes / f"bulk{i}.md").write_text(f"# Bulk {i}\n\nbulkword{i}")
    assert wait_for(lambda: watched.search("bulkword14"))
    time.sleep(0.8)
    assert len(builds) <= 2


def test_hidden_changes_ignored(watched, notes):
    v = watched.version
    (notes / ".hidden" / "x.md").write_text("ignored")
    time.sleep(1.0)
    assert watched.version == v
