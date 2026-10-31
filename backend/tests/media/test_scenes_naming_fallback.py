"""FileSplitter features that had no direct test: each scene-detection method on a
real clip, TPDB scene naming (network mocked), and encoding on the CPU when NVENC
isn't available."""
import shutil

import pytest

from app.services.media import config, encoder, scene_detector, scene_namer

from .test_engine_e2e import _clip_with_black_gaps

ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                            reason="ffmpeg not installed")


# ── Scene detection: every method finds the two gaps ──────────────────────────

@pytest.fixture(scope="module")
def gapped(tmp_path_factory):
    """6 s of testsrc, 2 s black, 6 s testsrc, 2 s black, 6 s testsrc → cuts near 6–8 s and 14–16 s."""
    path = tmp_path_factory.mktemp("scenes") / "gapped.mp4"
    _clip_with_black_gaps(path, segments=3, seg_len=6, gap=2)
    return str(path)


def _near_gaps(cuts):
    return [c for c in cuts if 5.5 <= c <= 8.5 or 13.5 <= c <= 16.5]


@ffmpeg
@pytest.mark.parametrize("method", ["black", "title", "select", "combined", "auto"])
def test_each_method_finds_the_scene_changes(gapped, method):
    cuts = scene_detector.detect_scenes(gapped, duration_sec=22, method=method, min_gap=3,
                                        black_min_duration=0.5, freeze_min_duration=1.0,
                                        threshold=0.3)
    assert cuts, f"{method} found nothing"
    assert len(_near_gaps(cuts)) >= 1, (method, cuts)
    assert all(0 < c < 22 for c in cuts)


@ffmpeg
def test_target_count_trims_to_exactly_n_cuts(gapped):
    cuts = scene_detector.detect_scenes(gapped, duration_sec=22, method="combined", min_gap=3,
                                        black_min_duration=0.5, freeze_min_duration=1.0,
                                        target_count=1)
    assert len(cuts) == 1


def test_select_timestamps_are_read_from_ffmpeg_metadata_output(tmp_path):
    # FileSplitter's parser looked for "pts_time=", but ffmpeg's metadata=print
    # writes "pts_time:6", so the select method never found a cut.
    f = tmp_path / "m.txt"
    f.write_text("frame:0    pts:76800   pts_time:6\nlavfi.scene_score=1.000000\n"
                 "frame:1    pts:102400  pts_time:8.04\nlavfi.scene_score=0.9\n")
    assert scene_detector._parse_timestamps(str(f)) == [6.0, 8.04]


def test_cuts_near_the_ends_and_each_other_are_dropped():
    assert scene_detector._filter_boundary_cuts([1.0, 50.0, 99.5], 100, min_gap=5) == [50.0]
    assert scene_detector._merge_nearby([10.0, 11.0, 30.0], min_gap=5) == [10.0, 30.0]


def test_cancelled_detection_returns_nothing(tmp_path):
    import threading
    done = threading.Event()
    done.set()
    assert scene_detector.detect_scenes(str(tmp_path / "missing.mp4"), cancel_event=done,
                                        duration_sec=10, method="black") == []


# ── TPDB scene naming ─────────────────────────────────────────────────────────

def test_titles_are_cleaned_and_made_safe_for_filenames():
    assert scene_namer.clean_title("2024.01.05.Some_Studio.Scene.Title") == "Some Studio Scene Title"
    assert scene_namer.sanitize('a/b:c*"d?') == "abcd"
    assert scene_namer.sanitize("   ") == "Scene"


def test_names_prefer_title_then_performers_then_number():
    assert scene_namer._format_name({"title": " Pilot "}, 0) == "Pilot"
    assert scene_namer._format_name({"performers": [{"name": "A"}, {"name": "B"}]}, 0) == "A & B"
    assert scene_namer._format_name({}, 2) == "Scene 3"


def test_phash_matches_name_each_scene(monkeypatch, tmp_path):
    frames = iter(range(10))
    monkeypatch.setattr(scene_namer, "_extract_keyframe", lambda p, t: str(tmp_path / f"f{next(frames)}.jpg"))
    monkeypatch.setattr(scene_namer, "_phash_int", lambda p: 1234)
    answers = iter([{"title": "First"}, None, {"title": "Third"}])
    monkeypatch.setattr(scene_namer, "_query_phash", lambda h, k: next(answers))
    names = scene_namer.get_scene_names("/x/clip.mkv", [(0, 10), (10, 20), (20, 30)], "key")
    assert names == ["First", "Scene 2", "Third"]   # 2 of 3 matched: gaps get numbers


def test_too_few_phash_matches_fall_back_to_the_filename(monkeypatch):
    monkeypatch.setattr(scene_namer, "_extract_keyframe", lambda p, t: None)
    monkeypatch.setattr(scene_namer, "_query_parse",
                        lambda stem, key: [{"title": "One"}, {"title": "Two"}] if stem == "clip" else [])
    assert scene_namer.get_scene_names("/x/clip.mkv", [(0, 5), (5, 10)], "key") == ["One", "Two"]


def test_no_matches_at_all_means_numbered_names(monkeypatch):
    monkeypatch.setattr(scene_namer, "_extract_keyframe", lambda p, t: None)
    monkeypatch.setattr(scene_namer, "_query_parse", lambda stem, key: [])
    assert scene_namer.get_scene_names("/x/clip.mkv", [(0, 5), (5, 10)], "key") is None


# ── NVENC → CPU fallback ──────────────────────────────────────────────────────

def test_nvenc_backend_without_a_gpu_encodes_on_the_cpu(monkeypatch):
    monkeypatch.setattr(encoder, "NVENC_AVAILABLE", False)
    args = encoder._build_codec_args("nvenc", 24, "slow")
    assert args[:2] == ["-c:v", "libx265"] and "-crf" in args


def test_nvenc_backend_with_a_gpu_uses_hevc_nvenc(monkeypatch):
    monkeypatch.setattr(encoder, "NVENC_AVAILABLE", True)
    args = encoder._build_codec_args("nvenc", 24, "slow")
    assert args[:2] == ["-c:v", "hevc_nvenc"] and args[args.index("-cq") + 1] == "24"


def test_unknown_presets_fall_back_to_medium():
    assert encoder._build_codec_args("cpu", 22, "warp-speed")[-1] == "medium"
    assert config.X265_CRF  # the default quality is set
