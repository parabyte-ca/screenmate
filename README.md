# ScreenMate

**Version 0.1.0 (in development)**

A self-hosted media pipeline. It finds shows and movies, downloads them through qBittorrent, then encodes them to x265, splits anthologies, trims intros, renames, and refreshes Plex/Jellyfin, all without you touching a file.

ScreenMate merges [AuTorrent](https://github.com/parabyte-ca/autorrent) (search, watchlist, downloads) and [FileSplitter](https://github.com/parabyte-ca/filesplitter) (scan, encode, split) into one app with one UI, one login, one database and one container.

> **Status:** Phase 6 (polish). **Ctrl/⌘+K** opens a command palette for any page, action, show or file. ScreenMate installs as an app, and sends **push notifications** to your phone (Settings → Notifications; needs HTTPS, e.g. a Cloudflare Tunnel). Next comes the Insights page. See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Planned v1 highlights

- **Auto pipeline:** rules like "if h264, encode to x265; if anthology, split" run automatically when a download completes or a scan finds a file. A dry-run preview shows what would happen first.
- **Unified Live Activity:** every item has one live timeline from found → downloaded → encoded → split → in the library, with retry and cancel at any step.
- **Command palette + PWA:** Ctrl+K for everything, an installable app, and push notifications.
- **Savings & insights:** GB reclaimed, library codec mix, GPU vs CPU throughput, and a forecast of what encoding the rest would save.
- **One-click import** of existing AuTorrent and FileSplitter databases.

---

## Quick Start

**Requirements:** Docker with the Compose plugin, and git.

```bash
git clone https://github.com/parabyte-ca/screenmate.git
cd screenmate
./setup.sh
```

`setup.sh` asks for the port (default 4260), your time zone and your media folders, and spots an NVIDIA GPU if you have one. It then:
- pulls the image and starts the container
- waits until the app is healthy
- offers to install the in-app upgrade helper
- prints the address to open

Your answers are saved to `.env` and `docker-compose.override.yml`. Both files stay out of git and are kept by `./update.sh`. Run `./setup.sh` again at any time to change them.

Unattended install:

```bash
./setup.sh -y --media /mnt/nas/tv --media /mnt/nas/movies --port 4260
```

Other flags: `--gpu` / `--no-gpu`, `--build` (build from this checkout instead of pulling), `--tz America/Toronto`, `--agent` / `--no-agent`. See `./setup.sh --help`.

Media folders are mounted at the same path inside the container. When qBittorrent runs on the same host, its download paths therefore match without any path mapping.

**Coming from AuTorrent or FileSplitter?** The setup wizard (or **Settings → Data management**) imports your old `autorrent.db` and `filesplitter.db` with one upload each.

---

## Updating ScreenMate

ScreenMate is automatically built and published to GitHub Container Registry on every push to `main`.

To update your running instance:

```bash
cd /path/to/screenmate
./update.sh
```

Or manually:

```bash
docker pull ghcr.io/parabyte-ca/screenmate:latest
docker compose up -d
```

### Versions and rolling back

`update.sh` (and the in-app upgrade) installs an exact release and records it as `SCREENMATE_VERSION` in `.env`; `docker-compose.yml` uses it as the image tag (`ghcr.io/parabyte-ca/screenmate:${SCREENMATE_VERSION:-latest}`). That is what makes going back possible:

- **Automatic** — if the new version does not become healthy, `update.sh` puts the previous release back.
- **From the app** — after an upgrade, the **Updates** page shows **Roll back to vX.Y.Z**.
- **By hand** — `./update.sh --version 2.6.1` installs any published release, older ones included.

Rolling back keeps your data; if you have used features added since, take a backup first (Settings → Data management). Delete the `SCREENMATE_VERSION` line from `.env` to follow `latest` again.

You can also update from inside the app: the **Updates** page shows when a new release is available and can install it for you (see `scripts/upgrade-agent.sh`).

Your data is stored in `./data/` and is not affected by updates.

> **First-time setup:** After the first successful GitHub Actions build, the package visibility must be set to **Public** so the image can be pulled without authentication. Go to `https://github.com/parabyte-ca?tab=packages`, find the `screenmate` package, and set its visibility to Public.

---

## Development

**Requirements:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
DATABASE_URL=sqlite:///./data/screenmate.db uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # runs on http://localhost:5173, proxies /api and /health to :8000
```

The Vite dev server proxies `/api` and `/health` to the FastAPI backend, so hot-reload works for both sides independently.

```bash
# Run backend tests
cd backend
python -m pytest tests/ -v
```

---

## Contributing

Bug reports and pull requests are welcome. Please open an issue first for any significant changes so we can discuss the approach before you invest time in it.

### Releases are automatic

Merging to `main` publishes a release when the merged change warrants one — you don't create tags by hand. The pull request **title** decides (use the [conventional commit](https://www.conventionalcommits.org) style):

| PR title | Release |
|---|---|
| `feat: …` | minor — `2.6.1` → `2.7.0` |
| `fix: …`, `perf: …` | patch — `2.6.0` → `2.6.1` |
| `feat!: …`, `fix!: …`, or a `BREAKING CHANGE:` line in the body | major — `2.7.0` → `3.0.0` |
| `docs:`, `ci:`, `test:`, `chore:`, `refactor:`, `style:`, `build:` | none (an image is still built) |

Add `[skip release]` to a PR title to merge without releasing. When several PRs merge before a build runs, the biggest change wins. A release builds the image (`vX.Y.Z`, `X.Y` and `latest`), then creates the git tag and GitHub release; the in-app **Updates** page offers it. **Release button:** to release right now whatever the titles say — say, a `docs:` merge you do want shipped, or a bigger bump — open the repository's **Actions** tab → *Build and push Docker image* → **Run workflow**, keep branch `main`, and pick `patch`, `minor` or `major`. (`none` just builds the image.) It needs something new since the last release, and ignores `[skip release]`. Pushing a `vX.Y.Z` tag yourself still works and builds exactly that version. The logic lives in `scripts/next_version.py`.

---

## License

MIT
