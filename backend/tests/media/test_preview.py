"""Library previews: thumbnails (cached, with a fail marker) and on-the-fly conversion
to browser-friendly MP4, including seeking and the limit on simultaneous players."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.media import preview, store

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _hevc_mkv(path: Path, seconds: int = 6) -> None:
    """An HEVC + AC-3 MKV: the kind of file a browser can't play directly."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
         "-c:a", "ac3", "-shortest", str(path)],
        check=True, timeout=120)


@pytest.fixture
def thumbs(tmp_path, monkeypatch):
    folder = tmp_path / "thumbs"
    monkeypatch.setattr(preview, "cache_dir", lambda: folder)
    return folder


@pytest.fixture
def clip(tmp_media_roots, media_db):
    path = Path(tmp_media_roots[0]) / "Show S01E01.mkv"
    _hevc_mkv(path)
    fid = store.upsert_file(str(path), path.name, path.stat().st_size, 6.0, "hevc", False)
    return fid, path


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_thumbnail_is_taken_ten_percent_in_and_at_most_five_minutes():
    assert preview.thumb_time(60) == 6.0
    assert preview.thumb_time(7200) == 300.0
    assert preview.thumb_time(None) == 1.0


def test_play_uses_the_gpu_when_there_is_one():
    assert "h264_nvenc" in preview.play_cmd("/m/a.mkv", 0, nvenc=True)
    cpu = preview.play_cmd("/m/a.mkv", 12.5, nvenc=False)
    assert "libx264" in cpu and cpu[cpu.index("-ss") + 1] == "12.5"
    assert "-ss" not in preview.play_cmd("/m/a.mkv", 0, nvenc=False)


# ── Thumbnails ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_thumbnail_is_a_jpeg_and_is_cached(api, clip, thumbs, monkeypatch):
    fid, _ = clip
    r = api.get(f"/api/media/files/{fid}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == b"\xff\xd8\xff"
    assert len(list(thumbs.glob("*.jpg"))) == 1

    def no_ffmpeg(*a, **k):
        raise AssertionError("ffmpeg ran again")
    monkeypatch.setattr(preview.subprocess, "run", no_ffmpeg)
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 200


@needs_ffmpeg
def test_an_unreadable_file_gets_no_thumbnail_and_is_not_retried(api, tmp_media_roots, media_db, thumbs):
    bad = Path(tmp_media_roots[0]) / "broken.mkv"
    bad.write_bytes(b"not a video")
    fid = store.upsert_file(str(bad), bad.name, 11, 60.0, "h264", False)
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 404
    assert len(list(thumbs.glob("*.fail"))) == 1
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 404


def test_files_outside_the_media_folders_are_not_served(api, tmp_path, media_db, thumbs):
    outside = tmp_path / "secret.mkv"
    outside.write_bytes(b"x")
    fid = store.upsert_file(str(outside), outside.name, 1, 1.0, "h264", False)
    assert api.get(f"/api/media/files/{fid}/thumb").status_code == 404
    assert api.get(f"/api/media/files/{fid}/play").status_code == 404
    assert api.get("/api/media/files/9999/play").status_code == 404


# ── Conversion ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_play_streams_browser_friendly_mp4(api, clip, tmp_path):
    fid, _ = clip
    r = api.get(f"/api/media/files/{fid}/play")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert r.content[4:8] == b"ftyp"
    out = tmp_path / "out.mp4"
    out.write_bytes(r.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout.split()
    assert probe == ["h264", "aac"]


@needs_ffmpeg
def test_play_starts_where_asked(api, clip, tmp_path):
    fid, _ = clip
    whole = api.get(f"/api/media/files/{fid}/play").content
    later = api.get(f"/api/media/files/{fid}/play?start=4").content
    assert 0 < len(later) < len(whole)


def test_too_many_players_is_a_clear_429(api, clip_stub, monkeypatch):
    fid = clip_stub

    def busy(*a, **k):
        raise preview.Busy("2 previews are already converting. Close one and try again.")
    monkeypatch.setattr(preview, "open_player", busy)
    r = api.get(f"/api/media/files/{fid}/play")
    assert r.status_code == 429 and "already converting" in r.json()["msg"]


def test_the_player_slot_is_freed_when_the_stream_ends(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            import io
            self.stdout = io.BytesIO(b"abc")
            self.killed = False
        def poll(self):
            return 0
        def kill(self):
            self.killed = True
        def wait(self):
            return 0
    monkeypatch.setattr(preview.subprocess, "Popen", FakeProc)
    for _ in range(preview.MAX_PLAYERS + 2):     # would raise Busy if slots leaked
        p = preview.open_player("/m/a.mkv", 0)
        assert b"".join(p.chunks()) == b"abc"
        p.stop()                                  # a second stop is harmless


@pytest.fixture
def clip_stub(tmp_media_roots, media_db):
    path = Path(tmp_media_roots[0]) / "a.mkv"
    path.write_bytes(b"x")
    return store.upsert_file(str(path), path.name, 1, 1.0, "h264", False)
