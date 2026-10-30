"""End-to-end media engine tests against real ffmpeg (skipped when it is missing).

Drives the real worker job runner — scan → encode / trim / split → store — on tiny
generated clips, so a broken port of any FileSplitter module shows up here.
"""
import os
import shutil
import subprocess
import threading
import time

import pytest

from app.services.media import codec_detector, config, scanner, store, worker

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed"
)


def _clip(path, seconds=6, size="320x240"):
    """A small h264 clip with audio."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def _clip_with_black_gaps(path, segments=3, seg_len=6, gap=2):
    """testsrc segments separated by fully black gaps — split points for blackdetect."""
    inputs, labels = [], []
    for i in range(segments):
        inputs += ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seg_len}"]
        labels.append(f"[{len(labels)}:v]")
        if i < segments - 1:
            inputs += ["-f", "lavfi", "-i", f"color=c=black:size=320x240:rate=25:duration={gap}"]
            labels.append(f"[{len(labels)}:v]")
    filt = "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[v]"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", filt,
         "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-g", "25", str(path)],
        check=True,
    )


@pytest.fixture(autouse=True)
def _engine_config(monkeypatch):
    monkeypatch.setattr(config, "ENCODER_BACKEND", "cpu")
    monkeypatch.setattr(config, "X265_PRESET", "ultrafast")
    monkeypatch.setattr(config, "TPDB_API_KEY", "")  # never reach the network


def _run(file_id, job_type, **kw):
    store.create_job(file_id, job_type, **kw)
    job = dict(store.dequeue_next_job())
    worker._run_job(job, threading.Event())
    return store.get_job(job["id"])


def test_scan_classifies_files(media_db, tmp_media_roots):
    root = tmp_media_roots[0]
    _clip(os.path.join(root, "Show.S01E01.mp4"))
    open(os.path.join(root, "notes.txt"), "w").close()
    scanner.scan_all()
    rows = store.get_all_files()
    assert [r["filename"] for r in rows] == ["Show.S01E01.mp4"]
    row = rows[0]
    assert row["codec"] == "h264" and row["width"] == 320 and row["is_anthology"] == 0
    assert row["duration_sec"] == pytest.approx(6, abs=0.5)


def test_encode_produces_hevc_and_records_job(media_db, tmp_media_roots):
    path = os.path.join(tmp_media_roots[0], "movie.mkv")
    _clip(path)
    scanner.scan_all()
    fid = store.get_file_by_path(path)["id"]

    job = _run(fid, "encode")

    assert job["status"] == "done", job["log_tail"]
    assert job["progress_pct"] == 100.0 and job["log_tail"].startswith("Done")
    assert codec_detector.probe(path).codec == "hevc"
    row = store.get_file(fid)
    assert row["status"] == "done" and row["codec"] == "hevc"
    assert row["size_bytes"] == os.path.getsize(path)
    assert store.get_stats()["total_saved_bytes"] == job["saved_bytes"]
    assert job["encoder"] == "cpu" and job["media_seconds"] == pytest.approx(6, abs=0.5)  # for Insights


def test_trim_removes_intro(media_db, tmp_media_roots):
    path = os.path.join(tmp_media_roots[0], "ep.mkv")
    _clip(path, seconds=10)
    scanner.scan_all()
    fid = store.get_file_by_path(path)["id"]

    job = _run(fid, "trim")

    assert job["status"] == "done", job["log_tail"]
    assert store.get_file(fid)["duration_sec"] == pytest.approx(10 - 3.5, abs=0.6)


def test_split_creates_one_file_per_segment(media_db, tmp_media_roots, monkeypatch):
    monkeypatch.setattr(config, "SPLIT_SCENE_METHOD", "black")
    monkeypatch.setattr(config, "SPLIT_BLACK_MIN_DURATION", 1.0)
    monkeypatch.setattr(config, "SPLIT_MIN_EPISODE_GAP", 3)
    root = tmp_media_roots[0]
    path = os.path.join(root, "Compilation Vol 1.mkv")
    _clip_with_black_gaps(path)
    scanner.scan_all()
    fid = store.get_file_by_path(path)["id"]

    job = _run(fid, "split")

    assert job["status"] == "done", job["log_tail"]
    assert job["log_tail"] == "3 scenes created"
    outputs = [f for f in os.listdir(root) if f.endswith(".mkv")]
    assert len(outputs) == 3 and not os.path.exists(path)


def test_cancel_mid_encode_leaves_original_untouched(media_db, tmp_media_roots, monkeypatch):
    monkeypatch.setattr(config, "X265_PRESET", "veryslow")
    path = os.path.join(tmp_media_roots[0], "long.mkv")
    _clip(path, seconds=30, size="640x480")
    before = os.path.getsize(path)
    scanner.scan_all()
    fid = store.get_file_by_path(path)["id"]
    store.create_job(fid, "encode")
    job = dict(store.dequeue_next_job())
    cancel = threading.Event()
    t = threading.Thread(target=worker._run_job, args=(job, cancel))
    t.start()
    deadline = time.monotonic() + 30
    while store.get_file(fid)["status"] != "processing" and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(1)
    cancel.set()
    t.join(timeout=60)

    assert not t.is_alive()
    assert store.get_job(job["id"])["status"] == "cancelled"
    assert store.get_file(fid)["status"] == "pending"
    assert os.path.getsize(path) == before
    assert codec_detector.probe(path).codec == "h264"
    leftovers = [f for f in os.listdir(tmp_media_roots[0]) if f != "long.mkv"]
    assert leftovers == []
