import pytest

from app.services.media import config, store


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    # apply() rewrites module globals; snapshot every runtime attribute so tests stay isolated.
    for attr, _ in config.SETTINGS_MAP.values():
        monkeypatch.setattr(config, attr, getattr(config, attr))


def test_apply_casts_each_type():
    assert config.apply("x265_crf", "22") and config.X265_CRF == 22
    assert config.apply("x265_crf", 24.0) and config.X265_CRF == 24
    assert config.apply("scene_threshold", "0.3") and config.SCENE_THRESHOLD == 0.3
    assert config.apply("x265_preset", "slow") and config.X265_PRESET == "slow"


def test_apply_lists_from_string_or_list():
    assert config.apply("split_keywords", " Vol, Compilation ,,") 
    assert config.SPLIT_KEYWORDS == ["Vol", "Compilation"]
    assert config.apply("media_paths", ["/a", " /b "]) and config.MEDIA_PATHS == ["/a", "/b"]
    assert config.serialize("media_paths", ["/a", "/b"]) == "/a,/b"


def test_apply_rejects_bad_values_without_changing_state():
    before = config.X265_CRF
    assert config.apply("x265_crf", "high") is False
    assert config.X265_CRF == before


def test_load_from_db_overrides_env_defaults(media_db):
    store.set_setting("x265_crf", "19")
    store.set_setting("split_keywords", "Box,Set")
    store.set_setting("scene_method", "")  # empty = keep default
    default_method = config.SCENE_METHOD
    config.load_from_db()
    assert config.X265_CRF == 19
    assert config.SPLIT_KEYWORDS == ["Box", "Set"]
    assert config.SCENE_METHOD == default_method


def test_categorize_uses_longest_matching_root(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_PATHS", ["/media", "/media/TV"])
    assert config.categorize("/media/TV/show.mkv") == "tv"
    assert config.categorize("/elsewhere/x.mkv") == "other"
