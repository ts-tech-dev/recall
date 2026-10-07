import os

import pytest

from recall import config, net
from recall.embeddings import FastEmbedEmbedder
from recall.rerank import FastEmbedReranker


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("RECALL_ALLOW_DOWNLOADS", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)


def test_offline_by_default(offline):
    assert not config.downloads_allowed()
    net.setup_network()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_downloads_can_be_allowed(monkeypatch):
    monkeypatch.setenv("RECALL_ALLOW_DOWNLOADS", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(net, "use_system_certificates", lambda: None)
    net.setup_network()
    assert config.downloads_allowed() and "HF_HUB_OFFLINE" not in os.environ


def test_bundled_models_are_used(monkeypatch, tmp_path):
    monkeypatch.delenv("RECALL_MODEL_DIR", raising=False)
    monkeypatch.setattr(config, "BUNDLED_MODELS", tmp_path)
    assert config.model_dir() == tmp_path
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("RECALL_MODEL_DIR", str(other))
    assert config.model_dir() == other


@pytest.mark.parametrize("cls, target", [(FastEmbedEmbedder, "fastembed.TextEmbedding"),
                                         (FastEmbedReranker, "fastembed.rerank.cross_encoder.TextCrossEncoder")])
def test_models_load_local_files_only(offline, monkeypatch, cls, target):
    seen = {}

    def fake(name, **kw):
        seen.update(kw)
        raise ValueError(f"Could not load model {name} from any source.")

    monkeypatch.setattr(target, fake)
    m = cls("some/model")
    with pytest.raises(ValueError, match="works offline") as e:
        m._get()
    assert seen["local_files_only"] is True
    assert "some/model" in str(e.value)


def test_other_errors_pass_through(offline):
    e = RuntimeError("broken onnx file")
    assert net.model_load_error("m", e) is e
