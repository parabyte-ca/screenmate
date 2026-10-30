# ScreenMate: merging AuTorrent and FileSplitter

## Context
Two self-hosted apps cover different halves of one workflow:
- **AuTorrent** (`parabyte-ca/autorrent`): FastAPI, SQLAlchemy, APScheduler and React 18 with Vite and Tailwind. It finds media and downloads it through qBittorrent.
- **FileSplitter** (`parabyte-ca/filesplitter`): Flask, raw SQLite and a single 1,777-line `index.html`. It indexes media and re-encodes it to x265, splits anthologies, trims intros and names scenes.

Today a file leaves AuTorrent and only reaches FileSplitter on a later scan. That means two UIs, two logins, two updaters, two databases and two Docker stacks.

**ScreenMate** (`parabyte-ca/screenmate`, currently empty) combines them into one app. Goals:
- full feature parity with both apps
- one streamlined UI
- an automated pipeline from finding media to a library-ready file

Decisions so far:
- Stack is FastAPI + React.
- v1 includes four extra features: Auto pipeline, Unified Live Activity, Command palette + PWA, and Savings & insights.
- Existing installs move over with a one-click importer.
- The old repos are archived once parity is reached.

## Architecture
The app is a single container with a single SQLite database (WAL mode) and a single scheduler. **AuTorrent's codebase is the base.** FileSplitter's ffmpeg engine is ported into it as services.

```
backend/app/
  routers/        autorrent routers (kept) + library, jobs, pipeline, activity, insights, import, events
  services/
    acquire/      autorrent services moved here: indexers/, qbittorrent, grab, watchlist_add, trakt*, tvmaze, tmdb, calendar_view, duplicate…
    media/        filesplitter port: codec_detector, encoder (CPU/NVENC + GPU probe), scene_detector, splitter, trimmer, scene_namer, scanner, trash
    pipeline/     rules engine, job queue/worker pool, journey tracker, path mapper
    events.py     in-process pub/sub → SSE (/api/events)
    insights.py   savings, codec-mix, throughput aggregates
    importer.py   reads autorrent.db + filesplitter.db
  models.py       autorrent models + MediaFile, Job, Journey, JourneyStep, Rule, PathMapping, SavingsLedger
frontend/src/pages/  Activity, Library, Search, Watchlist, Calendar, Browse, History, Insights, Settings, Updates
```

Code to reuse rather than rewrite:
- **FileSplitter media modules** (`codec_detector.py`, `encoder.py`, `scene_detector.py`, `splitter.py`, `trimmer.py`, `scene_namer.py`, `trash.py`, `scanner.py`): copied almost unchanged. They are pure functions built around `subprocess` with `progress_cb` and `cancel_event` hooks, which is exactly what the new worker needs. The only change is swapping `config.py` lookups for ScreenMate's settings service.
- **FileSplitter `worker.py`**: its thread pool, pause/resume and cancel logic become `pipeline/worker.py`. It reads its queue from the `Job` table through SQLAlchemy instead of `db.py`.
- **AuTorrent `services/download_events.py` and `qbit_states.py`**: the completion detector already exists. It gains one hook: on completion, call `pipeline.on_download_complete(download)`.
- **AuTorrent `migrations.py`, `routers/backup.py`, `routers/auth.py`, `routers/onboarding.py`, `services/updater.py`**: reused as they are. Onboarding gets an import step.
- **Shared update path**: `update.sh` and `scripts/upgrade-agent.sh` already exist in both repos. Use FileSplitter's version, which adds backup, test against a copy of the database, and automatic rollback.
- **FileSplitter `static/sw.js` and `manifest.json`**: the starting point for the PWA.

## Extra features ("wow" features)
1. **Auto pipeline.** `Rule` = match conditions plus ordered actions.
   - Match conditions: codec, resolution, duration, size, keyword, source (download or scan), quality profile, watchlist item.
   - Actions: trim, split (with scene-namer naming), encode (CRF/CQ, preset, backend), rename from a template, move to a folder, refresh the media library, notify.
   - Rules are evaluated when a download completes and when a library scan finds a new file.
   - A *dry-run* preview shows which rules would fire, and the resulting size estimate, before anything is enabled.
   - **Path mapping** (qBittorrent path → container path) is a required setting, with a "Test mapping" button.
2. **Unified Live Activity.** Each item gets a `Journey` made of `JourneyStep` rows.
   - Steps: found, download, encode, split, trim, rename, library, notify. Each stores status, progress %, ETA, an ffmpeg log tail, and why it ran (for example "rule X").
   - Everything streams over one SSE channel; the 10-second polling goes away.
   - The Activity page has filter chips. Each item opens a journey drawer with Retry, Skip step and Cancel buttons.
   - A global status pill shows active and failed counts plus GB saved today.
   - Journeys are saved, so they pick up again after a restart.
   - This replaces AuTorrent's Downloads tab and FileSplitter's Active Jobs tab.
3. **Command palette + PWA.** Ctrl+K opens a palette with fuzzy actions: search torrents, add to watchlist, encode or split a file, jump to any page, pause the queue, run a scan. Also:
   - keyboard shortcuts
   - an installable PWA
   - web push (VAPID) as a notification channel alongside Apprise
4. **Savings & insights.** `SavingsLedger` records the bytes before and after every encode. The Insights page shows:
   - total GB reclaimed
   - library codec mix
   - GPU vs CPU throughput
   - a forecast of what encoding the remaining x264 files would save
   - FileSplitter's existing `saved-bytes` total, carried over

## Quality-of-life merges
- One login, one Settings page with grouped sections and a "Test" button for every integration, one updater, one backup ZIP covering everything.
- The Library page replaces FileSplitter's file table. It uses the React table pattern from autorrent's `History.tsx` and adds bulk select, preview stream, rename, delete to trash, anthology toggle, skip and queue actions.
- Encoding defaults per quality profile, so a 4K profile can automatically mean NVENC CQ22.
- Disk-space guard: AuTorrent's `disk_space.py` blocks encodes when the destination is short on space.

## Delivery phases
Each phase is a pull request on `claude/great-wright-2btctp` or a follow-up branch, with CI green before merging.

0. **Scaffold.**
   - Import the AuTorrent tree without its git history and rename the app to ScreenMate.
   - Dockerfile gains ffmpeg, with an NVENC compose profile.
   - Merge the CI workflows (autorrent `build.yml` builds to GHCR; filesplitter `ci.yml` runs bash tests).
   - Port 4260 by default, so ScreenMate can run alongside the old apps during migration.
1. **Media engine port.**
   - Copy the FileSplitter modules to `services/media/` and add the `MediaFile` and `Job` models plus a worker.
   - Port `tests/test_encoder.py`, `test_codec_detector.py`, `test_trash*.py` and the related suites to pytest under `backend/tests/media/`.
2. **Events + Journeys + Activity page** (SSE bus, journey tracker, Activity UI, header status pill).
3. **Pipeline:** rules engine, path mapping, completion hook, dry-run preview, rules UI in Settings.
4. **Library page + merged Settings.** FileSplitter's settings move into the AuTorrent settings model, including runtime-editable encode and scene parameters, Recheck GPU, and Trash.
5. **Importer:** an onboarding step that uploads or points at `autorrent.db` and `filesplitter.db`, maps them to the new tables, and reports what came across.
6. **Command palette + PWA + web push.**
7. **Insights page.**
8. **Parity sign-off.**
   - Work through the checklist below, then update the README with a revision history in FileSplitter's style.
   - Add pointer READMEs to the two old repos and archive them, with your confirmation before archiving.

## Parity checklist (both apps, every item must be ticked)
- **AuTorrent:**
  - Search: NYAA, TPB, EZTV, YTS, TorrentGalaxy, 1337x, Jackett/Prowlarr, with the adult filter, quality and codec filters
  - Watchlist: episodes, guide, scan, TVMaze show-ended detection, override
  - Quality profiles
  - Trakt sync
  - Calendar and Browse
  - Downloads (qBittorrent state, auto-cleanup, clear finished)
  - Duplicate detection
  - History: search, CSV export, delete
  - Plex/Jellyfin refresh and Plex digest
  - Apprise notifications
  - TMDB posters
  - Auth and token revoke
  - Onboarding wizard
  - Backup/restore
  - `/health`
  - Themes and collapsible sidebar
  - Updates page and upgrade agent
- **FileSplitter:**
  - Scan and classification (codec, duration, size, keyword, anthology)
  - x265 CPU and NVENC encoding, GPU probe with retry, Recheck GPU, fallback to CPU
  - Scene detection (select, black, freeze)
  - Split by stream copy
  - TPDB pHash scene naming
  - Intro trim
  - Rename, stream preview, delete to `.trash` with 30-day purge and Empty Trash
  - Skip and cancel; pause and resume the queue; clear jobs
  - Saved-bytes counter
  - Runtime settings
  - ffprobe timeout
  - PWA
  - Daily release check and in-app upgrade

## Verification
- `cd backend && pytest`: all of autorrent's tests plus the ported filesplitter suites pass.
- Media engine end-to-end test:
  - Generate short h264 clips with `ffmpeg -f lavfi` (testsrc plus black frames between "scenes").
  - Assert the encode produces HEVC, the split produces N files, and the trim removes about 3.5 seconds.
  - Assert the savings are recorded.
- Pipeline integration test with a mocked qBittorrent: a completed download creates a journey and the rules fire in order. SSE events are asserted with an httpx stream client.
- Importer test against fixture copies of both old database schemas.
- `cd frontend && npm run build` plus type checking.
- Playwright smoke test (Chromium is pre-installed):
  - Log in, then open each page.
  - Ctrl+K opens the palette.
  - The Activity drawer updates live.
- `docker build` succeeds, and `/health` returns OK inside the container.
- `tests/bash/*` (update and upgrade agent) pass.

---

# Phase 1: Media engine port (current step)

## Context
Phase 0 (scaffold) is merged to `main` through the PR. Phase 1 brings FileSplitter's ffmpeg engine into ScreenMate as a backend that works through its API. Settings and data go into ScreenMate's SQLAlchemy database. The UI comes in Phase 4 (Library page); until then the engine is usable through FastAPI's `/docs`. The work goes on a new branch from `main` (`claude/great-wright-2btctp` was merged, so I'll restart it from `main`), with a new draft PR.

## Approach: port with as few changes as possible
FileSplitter's modules read module-level `config.X` values, which are changed at runtime when settings are saved. They talk to storage only through `db.<function>()` calls. Two choices keep those modules close to unchanged:

1. **`backend/app/services/media/config.py`**: a port of FileSplitter's `config.py` with the same attribute names and the same environment-variable defaults.
   - Dropped, because ScreenMate already has these: `FLASK_PORT`/`_find_free_port`, `VERSION`, `DATABASE_PATH`, `FLASK_SECRET_KEY`, `DASHBOARD_PASSWORD`, `APP_SOURCE_URL`, `APP_UPDATE_CHECK`, `APP_UPGRADE_DIR`.
   - Added: `SETTINGS_MAP` (moved from FileSplitter's `app._SETTINGS_MAP`), `apply(key, value)` (the cast logic from `api_post_settings`, including the special cases for `SPLIT_KEYWORDS` and booleans), and `load_from_db(session)` (from `_reload_settings_from_db`).
   - Settings keep **the same key names** (`x265_crf`, `scene_threshold`, and so on) in ScreenMate's existing `settings` table. That makes the Phase 5 importer a straight copy.
   - `media_paths` becomes a setting you can edit, falling back to the `MEDIA_PATHS` environment variable.
2. **`backend/app/services/media/store.py`**: a replacement for `db.py` with **the same function names and return shapes** (dicts that behave like `sqlite3.Row`). It uses SQLAlchemy.
   - Functions: `upsert_file`, `set_file_status`, `set_anthology_override`, `get_file(_by_path)`, `get_all_files`, `get_stats`, `create_job`, `get_active_jobs`, `get_active_job_for_file`, `get_job`, `dequeue_next_job`, `update_job_progress`, `set_job_status`, `update_file_size`, `rename_file`, `delete_file`, `purge_missing_files`, `clear_finished_jobs`, `get_recent_jobs`, `get/set_setting`, `increment/set_saved_bytes`.
   - `recover_interrupted()` ports the crash-recovery block from `init_db`: running jobs become error, processing files go back to pending, orphaned queued files go back to pending.
   - `dequeue_next_job` uses a short transaction plus a status guard so two workers can never take the same job.

New models in `backend/app/models.py`:
- `MediaFile` (table `media_files`) and `MediaJob` (table `media_jobs`), with the columns from FileSplitter's `files` and `jobs` tables. That includes the columns added later: `width`, `height`, `anthology_override`, `saved_bytes`, `episode_count`.
- Both are created by `Base.metadata.create_all` through `run_migrations()` (in `backend/app/migrations.py`). No ALTER statements are needed for new tables.

## Files
`backend/app/services/media/`: copied from `parabyte-ca/filesplitter`. The only edits are imports (`import config` → `from . import config`, `import db` → `from . import store as db`), plus the items below.
- `codec_detector.py`, `scene_detector.py`, `splitter.py`, `trimmer.py`, `scene_namer.py`, `trash.py`, `scanner.py`: imports only.
- `encoder.py`: one real change. `NVENC_AVAILABLE = _nvenc_available()` currently runs at import time and can block for up to 120 seconds. It becomes `NVENC_AVAILABLE = False` plus `start_probe()`, which runs `_nvenc_available()` in a daemon thread and sets `PROBE_DONE` (a `threading.Event`). This keeps FastAPI startup and `/health` instant, and keeps tests from running ffmpeg on import.
- `worker.py`: copied, with `import db` changed to use the store. Before dequeuing, a worker waits on `encoder.PROBE_DONE` (up to `NVENC_PROBE_MAX_WAIT` seconds) when the backend is `nvenc`. Without this, jobs queued during startup would silently run on the CPU.
- `__init__.py`: adds `startup()` and `shutdown()` functions. `startup()` loads settings, runs `recover_interrupted()`, starts the GPU probe, and starts the worker.

`backend/app/routers/media.py`: FileSplitter's Flask API, rewritten as FastAPI under the `/api/media` prefix. Function bodies are kept.
- **Files:** `GET /files`, `GET /files/{id}/stream` (Starlette `FileResponse` supports Range requests, so seeking works), `POST /files/{id}/rename`, `/delete`, `/anthology`, `/cancel`, `/skip`, `POST /files/purge`.
- **Jobs and queue:** `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/clear`, `POST /queue`, `/queue/pause`, `/queue/resume`.
- **Scan and stats:** `POST /scan` (runs in a background thread, with the `_scan_status` flag), `GET /stats`.
- **Trash:** `GET /trash`, `POST /trash/empty`.
- **Settings:** `GET` and `POST /settings`, `POST /settings/saved-bytes`, `POST /encoder/recheck-gpu`.
- **Not ported:** FileSplitter's `/stream` SSE endpoint. Phase 2's unified event stream replaces it.

Other edits:
- `backend/app/main.py`: register the router; call `media.startup()` and `media.shutdown()` in `lifespan`.
- `backend/app/services/scheduler.py`: a daily `trash.cleanup_trash(max_age_days=30)` job, replacing FileSplitter's sleep loop.
- `backend/app/middleware/auth.py`: also accept `?token=` on `GET` requests. `<video>` tags and Phase 2's `EventSource` cannot send an `Authorization` header. It uses the same `make_token` and `hmac.compare_digest` check.
- `backend/app/routers/backup.py`: no change needed, because the new tables live in the same database file.
- `backend/requirements.txt`: add `requests`, `Pillow` and `imagehash` (needed by `scene_namer`), with versions pinned as in FileSplitter.
- `docker-compose.yml` / `.env.example`: document the media environment variables (`MEDIA_PATHS`, `X265_CRF`, `SCENE_*`, `SPLIT_*`, `MAX_WORKERS`, `TPDB_API_KEY`, and so on) as comments.

## Tests (`backend/tests/media/`)
- **Ported, imports changed only:** `test_codec_detector.py`, `test_encoder.py`, `test_trash.py`.
- **Rewritten for FastAPI's TestClient:** `test_trash_routes.py`.
- **Not ported:** `test_updates_routes.py`, `test_release_cache.py`, `test_versioning.py`, `test_upgrade_state.py`, `test_agent_status.py`. These cover FileSplitter's updater, and ScreenMate's own updater tests already cover the same ground.
- **New:**
  - `test_store.py`: dequeue order and no double-claiming, crash recovery, saved-bytes accounting, the anthology override.
  - `test_media_config.py`: SETTINGS_MAP casts, including the bool/int trap that FileSplitter documents in its code comments.
  - `test_media_routes.py`: every endpoint, using a temporary database and temporary media roots.
  - `test_engine_e2e.py`: skipped when ffmpeg is missing. Generates 20–40 second h264 clips with `ffmpeg -f lavfi` (testsrc with black gaps), then:
    - scans them and checks classification
    - encodes and checks the codec is hevc with savings recorded
    - splits and checks N output files
    - trims and checks the duration is about 3.5 seconds shorter
    - cancels mid-encode and checks the original file is untouched
  - `test_auth_query_token.py`.
- A conftest fixture points `media.config` at temporary media roots and uses an in-memory database.

## Verification
- `cd backend && pytest -q`: all 646 existing tests plus the new media suite pass, locally and in CI (CI already installs ffmpeg).
- Run uvicorn locally with `MEDIA_PATHS` pointing at a scratch folder of generated clips. Use `/docs` to run scan → queue encode → watch `GET /api/media/jobs` progress → stream the file with curl Range requests.
- Confirm `/health` responds immediately at startup, so the GPU probe isn't blocking anything.
- `docker build`, then `ffmpeg -version` inside the container.
- `shellcheck` and the frontend build pass as before (the frontend is untouched).

## Follow-ups to flag
- The `anthropic-skills:autorrent` skill will be out of date. Once Phase 3 lands, replace it with a `screenmate` skill (use `skill-creator`).
- The `homelab` and `plex` skills may refer to the old ports and containers. Review them after migration.
