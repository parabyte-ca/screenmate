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

## Follow-ups to flag
- The `anthropic-skills:autorrent` skill will be out of date. Once Phase 3 lands, replace it with a `screenmate` skill (use `skill-creator`).
- The `homelab` and `plex` skills may refer to the old ports and containers. Review them after migration.
