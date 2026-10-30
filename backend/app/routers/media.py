"""Media engine API — FileSplitter's /api routes, ported to FastAPI under /api/media.

Response bodies keep FileSplitter's shapes ({"ok": ..., "msg": ...} and so on) so the
Library page can be built against the same contract.
"""
import os
import threading

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, JSONResponse

from ..services.media import config, encoder, scanner, scene_namer, trash, worker
from ..services.media import store as db

router = APIRouter(prefix="/media", tags=["media"])

_scan_lock = threading.Lock()
_scan_status = {"running": False}


def _err(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "msg": msg}, status_code=status)


# --- Files ---

@router.get("/files")
def list_files():
    result = []
    for r in db.get_all_files():
        d = dict(r)
        d["category"] = config.categorize(d["path"])
        result.append(d)
    return result


@router.get("/files/{file_id}/stream")
def stream_file(file_id: int, download: str | None = None):
    """Serve the raw media file for in-browser preview (Range-enabled) or download."""
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    path = row["path"]
    # Defensive: only serve files that live under a configured media root.
    if trash._media_root_for(path) is None or not os.path.isfile(path):
        return _err("File not found", 404)
    disposition = "attachment" if download == "1" else "inline"
    return FileResponse(path, filename=row["filename"], content_disposition_type=disposition)


@router.post("/files/{file_id}/rename")
def rename_file(file_id: int, body: dict = Body(default={})):
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("Cannot rename while a job is active", 409)

    raw_name = (body.get("new_name") or "").strip()
    old_path = row["path"]
    directory = os.path.dirname(old_path)
    ext = os.path.splitext(old_path)[1]

    # Sanitize; strip any extension the user typed so we keep the original one.
    # Check for blank first: sanitize() falls back to "Scene" for empty input, which
    # in FileSplitter silently renamed the file to Scene.<ext>.
    stem = os.path.splitext(raw_name)[0]
    base = scene_namer.sanitize(stem) if stem.strip() else ""
    if not base:
        return _err("Invalid name", 400)
    new_filename = base + ext
    new_path = os.path.join(directory, new_filename)

    if new_path == old_path:
        return {"ok": True, "msg": "No change"}
    if os.path.exists(new_path):
        return _err("A file with that name already exists", 409)

    try:
        os.rename(old_path, new_path)
    except OSError as exc:
        return _err(f"Rename failed: {exc}", 500)
    db.rename_file(file_id, new_path, new_filename)
    return {"ok": True, "filename": new_filename}


@router.post("/files/{file_id}/delete")
def delete_file(file_id: int):
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("Cannot delete while a job is active", 409)

    path = row["path"]
    if os.path.exists(path):
        try:
            trash.move_to_trash(path)
        except OSError as exc:
            return _err(f"Delete failed: {exc}", 500)
    db.delete_file(file_id)
    return {"ok": True}


@router.post("/files/{file_id}/anthology")
def set_anthology(file_id: int, body: dict = Body(default={})):
    raw = body.get("value")  # True, False, or None (reset to auto)
    override = None if raw is None else bool(raw)
    if db.get_file(file_id) is None:
        return _err("File not found", 404)
    db.set_anthology_override(file_id, override)
    return {"ok": True}


@router.post("/files/{file_id}/skip")
def skip_file(file_id: int):
    file_row = db.get_file(file_id)
    if file_row is None:
        return _err("File not found", 404)
    if file_row["status"] in ("processing", "queued"):
        return _err("Cannot skip a file that is queued or processing", 409)
    db.set_file_status(file_id, "skipped")
    return {"ok": True}


@router.post("/files/{file_id}/cancel")
def cancel_file(file_id: int):
    job = db.get_active_job_for_file(file_id)
    if job is None:
        return _err("No active job for this file", 404)
    return _cancel_job_by_id(job["id"])


@router.post("/files/purge")
def purge_missing():
    return {"ok": True, "removed": db.purge_missing_files()}


# --- Jobs & queue ---

@router.get("/jobs")
def list_jobs():
    return [dict(r) for r in db.get_recent_jobs(50)]


def _cancel_job_by_id(job_id: int):
    """Shared cancel logic used by both cancel endpoints."""
    job = db.get_job(job_id)
    if job is None:
        return _err("Job not found", 404)
    status = job["status"]
    if status == "running":
        worker.cancel_job(job_id)
        return {"ok": True, "msg": "Cancel signal sent — job will stop shortly"}
    if status == "queued":
        db.set_job_status(job_id, "cancelled")
        db.set_file_status(job["file_id"], "pending")
        return {"ok": True, "msg": "Queued job cancelled"}
    return _err(f"Job is '{status}', cannot cancel", 409)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    return _cancel_job_by_id(job_id)


@router.post("/jobs/clear")
def clear_jobs():
    return {"ok": True, "cleared": db.clear_finished_jobs()}


@router.post("/queue")
def queue_job(body: dict = Body(default={})):
    file_id = body.get("file_id")
    job_type = body.get("job_type")  # encode | split | trim
    target_resolution = body.get("target_resolution", "original")
    try:
        episode_count = int(body.get("episode_count", 0) or 0)
    except (TypeError, ValueError):
        return _err("Invalid parameters", 400)

    if not file_id or job_type not in ("encode", "split", "trim"):
        return _err("Invalid parameters", 400)
    if target_resolution != "original" and target_resolution not in config.RESOLUTION_MAP:
        return _err("Invalid parameters", 400)

    if db.get_file(file_id) is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("File already queued or processing", 409)

    job_id = db.create_job(file_id, job_type, target_resolution, episode_count)
    db.set_file_status(file_id, "queued")
    return {"ok": True, "job_id": job_id}


@router.post("/queue/pause")
def pause_queue():
    worker.pause()
    db.set_setting("queue_paused", "1")
    return {"ok": True, "paused": True}


@router.post("/queue/resume")
def resume_queue():
    worker.resume()
    db.set_setting("queue_paused", "0")
    return {"ok": True, "paused": False}


# --- Scan & stats ---

@router.post("/scan")
def start_scan():
    with _scan_lock:
        if _scan_status["running"]:
            return _err("Scan already running", 409)
        _scan_status["running"] = True

    def _run():
        try:
            scanner.scan_all()
        finally:
            _scan_status["running"] = False

    threading.Thread(target=_run, name="media-scan", daemon=True).start()
    return {"ok": True, "msg": "Scan started"}


@router.get("/stats")
def stats():
    return {
        **db.get_stats(),
        "paused": worker.is_paused(),
        "scan_running": _scan_status["running"],
    }


# --- Trash ---

@router.get("/trash")
def trash_stats():
    return trash.trash_stats()


@router.post("/trash/empty")
def empty_trash():
    result = trash.empty_trash()
    return {"ok": True, "freed_bytes": result["size_bytes"], "removed_files": result["file_count"]}


# --- Settings ---

@router.get("/settings")
def get_settings():
    out = {key: getattr(config, attr) for key, (attr, _cast) in config.SETTINGS_MAP.items()}
    out["split_keywords"] = ",".join(config.SPLIT_KEYWORDS)
    out["tpdb_api_key_set"] = bool(config.TPDB_API_KEY)
    out.pop("tpdb_api_key")  # never echo the secret back
    out.update({
        "resolutions": ["original"] + list(config.RESOLUTION_MAP.keys()),
        "nvenc_available": encoder.NVENC_AVAILABLE,
        "nvenc_probe_done": encoder.PROBE_DONE.is_set(),
        "nvenc_last_error": encoder.LAST_NVENC_ERROR,
        "cpu_presets": encoder._CPU_PRESETS,
        "nvenc_presets": encoder._NVENC_PRESETS,
    })
    return out


@router.post("/settings")
def update_settings(body: dict = Body(default={})):
    """Persist runtime-adjustable settings; they apply to the next job without a restart."""
    rejected = []
    for key, val in body.items():
        if key not in config.SETTINGS_MAP:
            continue
        if config.apply(key, val):
            db.set_setting(key, config.serialize(key, val))
        else:
            rejected.append(key)
    if rejected:
        return JSONResponse({"ok": False, "msg": "Invalid value", "rejected": rejected}, status_code=400)
    return {"ok": True}


@router.post("/settings/saved-bytes")
def set_saved_bytes(body: dict = Body(default={})):
    try:
        value = int(body.get("value", 0))
    except (TypeError, ValueError):
        return _err("Invalid value", 400)
    db.set_saved_bytes(value)
    return {"ok": True, "total_saved_bytes": value}


@router.post("/encoder/recheck-gpu")
def recheck_gpu():
    available = encoder.recheck_nvenc()
    return {"nvenc_available": available, "nvenc_last_error": encoder.LAST_NVENC_ERROR}
