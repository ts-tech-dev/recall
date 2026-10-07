import json
import stat

import pytest

from recall.config import Settings, apply_update, config_path, load_settings, save_settings


def test_defaults(data):
    s = load_settings()
    assert s.notes_dir == "" and s.top_k == 12 and s.semantic_search and s.ocr


def test_save_is_private_and_roundtrips(data, notes):
    s = apply_update(Settings(), {"notes_dir": str(notes), "top_k": 7})
    save_settings(s)
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert load_settings().notes_dir == str(notes.resolve()) and load_settings().top_k == 7


def test_old_ai_settings_are_dropped(data):
    """A config.json from a version with AI providers loads, and saving it forgets the old key."""
    config_path().write_text(json.dumps({"provider": "anthropic", "api_key": "sk-old-123456", "top_k": 9}))
    s = load_settings()
    assert s.top_k == 9 and "api_key" not in s.public()
    save_settings(s)
    assert "sk-old" not in config_path().read_text()


@pytest.mark.parametrize("bad", [{"notes_dir": "/definitely/not/here"}, {"top_k": "many"}])
def test_validation(bad):
    with pytest.raises(ValueError):
        apply_update(Settings(), bad)
