# Security audit: secrets and the client

**Date:** 2026-10-31
**Scope:**
- the backend code and frontend source
- the production bundle (`frontend/dist`)
- source maps
- the Dockerfile and build context
- the CI workflows
- `.env` handling
- the full git history

**Question:** can a credential, API key, token, private key or connection string reach the browser, the bundle, the image or the logs?

## Result
The frontend bundle, the image layout and the git history were already clean. The API was not: one endpoint returned every stored secret in clear text. That is fixed, and CI now checks for regressions on every push.

## Findings

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | High | `GET /api/settings` and the `PUT` response returned the stored secrets in clear text: qBittorrent password, Plex token, Jellyfin, Jackett/Prowlarr and TMDB keys, Trakt client ID, Apprise URL (holds bot tokens), SMTP password, the UI password and the **VAPID private key**. Requests from the local network need no login (by design), so anyone on the LAN could read them. | Fixed |
| 2 | Medium | Error text from the Plex, Jellyfin and Jackett test buttons and from Plex digest calls can contain the request URL, and those URLs carry `X-Plex-Token` / `apikey`. That text was returned to the page and written to the logs. Access logs also recorded the `?token=` login token that video and live-event URLs use. | Fixed |
| 3 | Low | There was no `.dockerignore`, so `data/` (the database), `.env` and `.git` were sent to the Docker builder. They were never copied into the image. | Fixed |
| 4 | Low | `.gitignore` did not cover stray `*.db` files or `.env.*` variants. | Fixed |
| 5 | Info | The frontend reads no environment variables, and production builds have no source maps. The bundle, the service workers and the manifest contain no secrets or internal hostnames. | Verified; now enforced in config and CI |
| 6 | Info | Every commit on every branch is free of credentials. CI uses only `GITHUB_TOKEN` and prints no secrets. The final image contains only `app/` and the built frontend. | Verified |
| 7 | Info | Media engine settings already hid the TPDB key (`tpdb_api_key_set` only). Push settings expose only the VAPID *public* key, plus a label for each device. | Verified |

## What changed

**Secrets stay on the server.** The new module is `backend/app/services/secret_settings.py`.
- A secret that has been saved comes back as `••••••••`. An empty one comes back empty, so the page can still tell whether it's set.
- `session_secret` and `vapid_private_key` are never sent at all, and cannot be overwritten through the settings API.
- On save, a field that still holds the mask keeps the stored value. Typing a new value replaces it.
- When a Test button sends the mask, the server swaps in the stored value before calling Plex, Jellyfin, Jackett/Prowlarr, TMDB, Trakt or SMTP. All external calls already ran on the server.
- Error messages have secret values and URL credentials removed before they are returned or logged.
- Every log line from every logger, uvicorn's access log included, has `token=`, `apikey=`, `api_key=`, `X-Plex-Token=` and `password=` values masked.

**Settings page.** Focusing a field that holds a saved secret selects it, so typing replaces it. A tooltip explains this.

**Build.**
- `vite.config.ts` sets `sourcemap: false` and `envPrefix: "VITE_"` explicitly.
- `.dockerignore` keeps data, env files, git metadata and tests out of the build context.

**CI.** `scripts/scan-secrets.sh` runs twice on every push and fails the build when it finds something:
- on the tracked source (tests excluded, because they hold deliberate fake secrets)
- on `frontend/dist`, where it also fails if any source map is present

It checks for:
- AWS, GitHub, Slack and Stripe-style keys
- private keys and JWTs
- database URLs that include credentials
- hard-coded `password`, `secret` and `api_key` values

**Tests.** `backend/tests/test_secret_settings.py` covers:
- no seeded secret appears in any settings response
- the mask round-trips on save
- server-only keys can't be overwritten
- Test buttons use the stored key
- URL and log redaction

## Not changed (by design or needs a decision)
- **Local network access needs no login.** Auth applies only to requests that arrive through a Cloudflare tunnel, which is behaviour inherited from AuTorrent. Anyone on the LAN can still *use* the app; they just can't read its secrets any more. Requiring login everywhere would be a separate, opt-in setting.
- **The UI password is stored as plain text** in the database, and login tokens are derived from it. Hashing it (for example with scrypt) would protect it if the database file were copied. It needs a migration and a re-login, so it is proposed as a follow-up.
- **Backup export** (`/api/backup/export`) contains the whole database, secrets included, because that is what a full restore needs. Treat backup files as sensitive.

## How to re-check
```bash
scripts/scan-secrets.sh                                  # source
(cd frontend && npm run build) && scripts/scan-secrets.sh frontend/dist
cd backend && pytest -q tests/test_secret_settings.py
curl -s localhost:4260/api/settings | grep -c '••••••••'   # secrets show as the mask
```
