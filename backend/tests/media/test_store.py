import threading

from app.services.media import config, store


def _file(path="/m/a.mkv", **kw):
    args = dict(filename=path.rsplit("/", 1)[-1], size_bytes=1000, duration_sec=60.0,
                codec="h264", is_anthology=False)
    args.update(kw)
    return store.upsert_file(path, **args)


def test_upsert_inserts_then_updates_same_row(media_db):
    fid = _file()
    assert store.get_file(fid)["status"] == "pending"
    assert _file(size_bytes=2000) == fid
    assert store.get_file(fid)["size_bytes"] == 2000
    assert len(store.get_all_files()) == 1


def test_upsert_sentinel_preserves_manual_anthology_override(media_db):
    fid = _file()
    store.set_anthology_override(fid, True)
    _file(is_anthology=False)  # rescan with default sentinel
    assert store.get_file(fid)["anthology_override"] == 1


def test_anthology_override_reset_recalculates(media_db, monkeypatch):
    monkeypatch.setattr(config, "SPLIT_MIN_DURATION", 10)
    monkeypatch.setattr(config, "SPLIT_KEYWORDS", ["Vol"])
    fid = _file("/m/Best Of Vol 2.mkv")
    store.set_anthology_override(fid, False)
    assert store.get_file(fid)["is_anthology"] == 0
    store.set_anthology_override(fid, None)
    row = store.get_file(fid)
    assert row["anthology_override"] is None and row["is_anthology"] == 1


def test_dequeue_is_fifo_and_marks_running(media_db):
    a, b = _file("/m/a.mkv"), _file("/m/b.mkv")
    j1 = store.create_job(a, "encode")
    j2 = store.create_job(b, "split")
    first = store.dequeue_next_job()
    assert first["id"] == j1 and first["status"] == "running" and first["started_at"]
    assert store.dequeue_next_job()["id"] == j2
    assert store.dequeue_next_job() is None


def test_concurrent_dequeue_never_double_claims(media_db):
    ids = [store.create_job(_file(f"/m/{n}.mkv"), "encode") for n in range(20)]
    claimed, lock = [], threading.Lock()

    def grab():
        while (job := store.dequeue_next_job()) is not None:
            with lock:
                claimed.append(job["id"])

    threads = [threading.Thread(target=grab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == ids


def test_recover_interrupted(media_db):
    running_f, queued_orphan = _file("/m/a.mkv"), _file("/m/b.mkv")
    queued_ok = _file("/m/c.mkv")
    job = store.create_job(running_f, "encode")
    store.dequeue_next_job()
    store.set_file_status(running_f, "processing")
    store.set_file_status(queued_orphan, "queued")
    store.create_job(queued_ok, "encode")
    store.set_file_status(queued_ok, "queued")

    store.recover_interrupted()

    assert store.get_job(job)["status"] == "error"
    assert store.get_job(job)["log_tail"] == "Interrupted by restart"
    assert store.get_file(running_f)["status"] == "pending"
    assert store.get_file(queued_orphan)["status"] == "pending"
    assert store.get_file(queued_ok)["status"] == "queued"  # still has a live job


def test_saved_bytes_accounting(media_db):
    assert store.get_stats()["total_saved_bytes"] == 0
    store.increment_saved_bytes(100)
    store.increment_saved_bytes(50)
    assert store.get_stats()["total_saved_bytes"] == 150
    store.set_saved_bytes(7)
    assert store.get_stats()["total_saved_bytes"] == 7


def test_stats_counts_by_status(media_db):
    a, b = _file("/m/a.mkv"), _file("/m/b.mkv")
    store.set_file_status(b, "done")
    s = store.get_stats()
    assert (s["total"], s["pending"], s["done"]) == (2, 1, 1)


def test_purge_missing_and_delete(media_db, tmp_path):
    real = tmp_path / "real.mkv"
    real.write_bytes(b"x")
    keep = _file(str(real))
    gone = _file("/nope/gone.mkv")
    store.create_job(gone, "encode")
    assert store.purge_missing_files() == 1
    assert store.get_file(gone) is None and store.get_file(keep) is not None
    store.delete_file(keep)
    assert store.get_all_files() == []


def test_clear_finished_jobs_keeps_active(media_db):
    f = _file()
    done = store.create_job(f, "encode")
    store.set_job_status(done, "done")
    active = store.create_job(f, "split")
    assert store.clear_finished_jobs() == 1
    assert store.get_job(active) is not None


def test_settings_roundtrip(media_db):
    assert store.get_setting("x265_crf", "28") == "28"
    store.set_setting("x265_crf", "22")
    store.set_setting("x265_crf", "23")
    assert store.get_setting("x265_crf") == "23"
    assert store.get_settings(["x265_crf", "missing"]) == {"x265_crf": "23"}


def test_worker_claims_jobs_only_when_a_slot_is_free(media_db, monkeypatch):
    """Queued jobs must stay queued until a worker can actually run them."""
    import time

    from app.services.media import worker

    monkeypatch.setattr(config, "MAX_WORKERS", 1)
    monkeypatch.setattr(config, "ENCODER_BACKEND", "cpu")
    gate = threading.Event()
    started = []

    def fake_run(job, cancel_event):
        started.append(job["id"])
        gate.wait(5)
        store.set_job_status(job["id"], "done")

    monkeypatch.setattr(worker, "_run_job", fake_run)
    monkeypatch.setattr(worker, "_active", 0)
    ids = [store.create_job(_file(f"/m/{n}.mkv"), "encode") for n in range(3)]
    worker.start()
    deadline = time.monotonic() + 5
    while not started and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    assert started == [ids[0]]
    assert [store.get_job(i)["status"] for i in ids] == ["running", "queued", "queued"]
    monkeypatch.setattr(config, "MAX_WORKERS", 2)  # raised live from Settings
    with worker._slots:
        worker._slots.notify_all()
    deadline = time.monotonic() + 5
    while len(started) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started == ids[:2]
    worker.stop()  # no loop may outlive this test
    gate.set()
    worker.resume()
