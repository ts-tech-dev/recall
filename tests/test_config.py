import json
import stat

import pytest

from recall.config import Settings, apply_update, config_path, load_settings, save_settings


def test_defaults(data):
    s = load_settings()
    assert s.provider == "anthropic" and s.model == "claude-opus-5-5" and s.notes_dir == ""
    assert not s.ai_enabled()


def test_env_key_fallback(data, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-123456")
    s = load_settings()
    assert s.ai_enabled()
    pub = s.public()
    assert pub["has_key"] and pub["key_from_env"] and pub["key_hint"] == "…3456"
    assert pub["api_key"] == ""


def test_save_is_private_and_roundtrips(data, notes):
    s = apply_update(Settings(), {"notes_dir": str(notes), "api_key": "sk-secret-abcdef"})
    save_settings(s)
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert json.loads(config_path().read_text())["api_key"] == "sk-secret-abcdef"
    assert load_settings().notes_dir == str(notes.resolve())


def test_public_never_leaks_key():
    pub = Settings(api_key="sk-secret-abcdef").public()
    assert "sk-secret" not in json.dumps(pub)


def test_empty_key_keeps_existing_and_clear_removes():
    s = Settings(api_key="sk-keep-me-1234")
    apply_update(s, {"api_key": "", "model": "claude-sonnet-5-5"})
    assert s.api_key == "sk-keep-me-1234" and s.model == "claude-sonnet-5-5"
    apply_update(s, {"api_key": "", "clear_api_key": True})
    assert s.api_key == ""


@pytest.mark.parametrize("bad", [{"provider": "bogus"}, {"effort": "extreme"}, {"notes_dir": "/definitely/not/here"}])
def test_validation(bad):
    with pytest.raises(ValueError):
        apply_update(Settings(), bad)


def test_local_openai_endpoint_needs_no_key():
    assert Settings(provider="openai", base_url="http://localhost:11434/v1").ai_enabled()
    assert not Settings(provider="openai").ai_enabled()
    assert not Settings(provider="none", api_key="k").ai_enabled()
