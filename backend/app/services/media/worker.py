import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from . import config
from . import store as db
from . import encoder
from . import splitter
from . import trimmer


def _fmt_bytes(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.2f} GB"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.0f} KB"

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_paused = threading.Event()
_paused.set()  # not paused by default

_cancel_events: dict[int, threading.Event] = {}

# Jobs are only claimed from the queue when a slot is free, so queued jobs stay
# "queued" (FileSplitter claimed everything at once and marked it running while
# it waited in the thread pool). The limit is read live from config.MAX_WORKERS,
# so changing it in Settings takes effect without a restart.
_POOL_SIZE = 16
_slots = threading.Condition()
_active = 0
_stop = threading.Event()


def start(num_workers: int = None) -> None:
    global _executor
    if num_workers:
        config.MAX_WORKERS = num_workers
    _executor = ThreadPoolExecutor(max_workers=_POOL_SIZE)
    _stop.clear()
    if db.get_setting('queue_paused', '0') == '1':
        _paused.clear()
        logger.info("Queue starting in paused state (persisted)")
    t = threading.Thread(target=_queue_loop, daemon=True)
    t.start()
    logger.info("Worker started with %d thread(s)", num_workers or config.MAX_WORKERS)


def pause() -> None:
    _paused.clear()
    logger.info("Queue paused")


def resume() -> None:
    _paused.set()
    logger.info("Queue resumed")


def is_paused() -> bool:
    return not _paused.is_set()


def cancel_job(job_id: int) -> bool:
    """Signal a running job to cancel. Returns True if the signal was sent."""
    event = _cancel_events.get(job_id)
    if event:
        event.set()
        logger.info("Cancel signal sent for job %d", job_id)
        return True
    return False


def stop() -> None:
    """Stop claiming new jobs (running jobs carry on until they finish or are cancelled)."""
    _stop.set()
    _paused.set()  # release a loop parked on pause so it can exit


def _queue_loop() -> None:
    while not _stop.is_set():
        try:
            if not _paused.wait(timeout=1):
                continue
            if _stop.is_set():
                break
            if config.ENCODER_BACKEND == "nvenc" and not encoder.PROBE_DONE.is_set():
                # Don't let jobs fall back to CPU while the boot-time GPU probe is still running.
                encoder.PROBE_DONE.wait(timeout=5)
                continue
            if not _wait_for_slot():
                continue
            job = db.dequeue_next_job()
            if job:
                job_dict = dict(job)
                job_id = job_dict["id"]
                cancel_event = threading.Event()
                _cancel_events[job_id] = cancel_event  # register BEFORE submit
                _claim_slot()
                _executor.submit(_run_in_slot, job_dict, cancel_event)
            else:
                time.sleep(3)
        except Exception:
            logger.exception("Queue loop error — retrying in 5s")
            time.sleep(5)


def _wait_for_slot(timeout: float = 5.0) -> bool:
    with _slots:
        return _slots.wait_for(lambda: _active < max(1, config.MAX_WORKERS), timeout=timeout)


def _claim_slot() -> None:
    global _active
    with _slots:
        _active += 1


def _run_in_slot(job: dict, cancel_event: threading.Event) -> None:
    global _active
    try:
        _run_job(job, cancel_event)
    finally:
        with _slots:
            _active -= 1
            _slots.notify_all()


def active_count() -> int:
    with _slots:
        return _active


def _run_job(job: dict, cancel_event: threading.Event) -> None:
    job_id = job["id"]
    file_id = job["file_id"]
    job_type = job["job_type"]
    target_res = job.get("target_resolution", "original")
    episode_count = job.get("episode_count") or 0

    def _fail(msg: str, pct: float = 0) -> None:
        # Always write the failure reason to both the file (Files tab) and the job
        # (Jobs/History tab's "Show log"), regardless of whether progress_cb already
        # reported it — so the log is never silently empty for a failed job.
        db.set_file_status(file_id, "error", msg)
        db.update_job_progress(job_id, pct, msg)
        db.set_job_status(job_id, "error")

    file_row = db.get_file(file_id)
    if file_row is None:
        msg = f"File record {file_id} no longer exists"
        logger.error("Job %d: %s", job_id, msg)
        db.update_job_progress(job_id, 0, msg)
        db.set_job_status(job_id, "error")
        _cancel_events.pop(job_id, None)
        return

    # If cancel was signalled before this thread was free, bail out immediately.
    if cancel_event.is_set():
        db.set_file_status(file_id, "pending")
        db.set_job_status(job_id, "cancelled")
        db.update_job_progress(job_id, 0, "Cancelled before start")
        _cancel_events.pop(job_id, None)
        return

    input_path = file_row["path"]
    if job_type in ("encode", "trim"):
        # Decided now: a GPU that isn't available falls back to the CPU in encoder.py.
        used = "nvenc" if config.ENCODER_BACKEND == "nvenc" and encoder.NVENC_AVAILABLE else "cpu"
        db.set_job_stats(job_id, used, file_row["duration_sec"])
    db.set_file_status(file_id, "processing")
    db.set_job_status(job_id, "running")

    # Track the last progress log so we can use it as the error message on failure.
    last_log = [""]

    def progress_cb(pct: float, log_line: str) -> None:
        last_log[0] = log_line
        db.update_job_progress(job_id, pct, log_line)

    try:
        if job_type == "encode":
            out_probe = encoder.encode_to_x265(
                input_path,
                target_resolution=target_res,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                db.set_file_status(file_id, "pending")
                db.set_job_status(job_id, "cancelled")
                db.update_job_progress(job_id, 0, "Cancelled by user")
            elif out_probe is not None:
                orig_bytes = file_row["size_bytes"] or 0
                new_bytes = os.path.getsize(input_path)
                db.update_file_size(
                    file_id, new_bytes,
                    codec=out_probe.codec,
                    width=out_probe.width,
                    height=out_probe.height,
                )
                saved = max(0, orig_bytes - new_bytes)
                savings_pct = (saved / orig_bytes) * 100 if orig_bytes else 0
                done_msg = (
                    f"Done — saved {savings_pct:.1f}%"
                    f" ({_fmt_bytes(orig_bytes)} → {_fmt_bytes(new_bytes)})"
                )
                db.set_file_status(file_id, "done")
                db.set_job_status(job_id, "done")
                db.update_job_progress(job_id, 100.0, done_msg, saved_bytes=saved)
                if saved > 0:
                    db.increment_saved_bytes(saved)
            else:
                err = last_log[0][:300] if last_log[0] else "Encoding failed (no further details available)"
                _fail(err)

        elif job_type == "trim":
            out_probe = trimmer.trim_start(
                input_path,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                db.set_file_status(file_id, "pending")
                db.set_job_status(job_id, "cancelled")
                db.update_job_progress(job_id, 0, "Cancelled by user")
            elif out_probe is not None:
                new_bytes = os.path.getsize(input_path)
                db.update_file_size(
                    file_id, new_bytes,
                    codec=out_probe.codec,
                    width=out_probe.width,
                    height=out_probe.height,
                    duration_sec=out_probe.duration_sec,
                )
                db.set_file_status(file_id, "done")
                db.set_job_status(job_id, "done")
                db.update_job_progress(job_id, 100.0, f"Trimmed {trimmer.TRIM_SECONDS}s off start")
            else:
                err = last_log[0][:300] if last_log[0] else "Trimming failed (no further details available)"
                _fail(err)

        elif job_type == "split":
            scenes = splitter.split_by_scenes(
                input_path,
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                episode_count=episode_count,
            )
            if cancel_event.is_set():
                db.set_file_status(file_id, "pending")
                db.set_job_status(job_id, "cancelled")
                db.update_job_progress(job_id, 0, "Cancelled by user")
            elif scenes is not None:
                db.set_file_status(file_id, "done")
                db.set_job_status(job_id, "done")
                db.update_job_progress(job_id, 100.0, f"{len(scenes)} scenes created")
            else:
                err = last_log[0][:300] if last_log[0] else "Splitting failed or no scenes found"
                _fail(err)

        else:
            msg = f"Unknown job type: {job_type}"
            logger.error(msg)
            _fail(msg)

    except Exception as exc:
        logger.exception("Job %d failed: %s", job_id, exc)
        _fail(f"Unexpected error: {exc}"[:300])
    finally:
        _cancel_events.pop(job_id, None)
