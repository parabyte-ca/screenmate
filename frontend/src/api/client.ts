import { authStore } from "../auth";

const BASE = "/api";

function _authHeader(): Record<string, string> {
  const token = authStore.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ..._authHeader(),
      ...(init?.headers as Record<string, string> ?? {}),
    },
  });
  if (res.status === 401) {
    authStore.clearToken();
    window.location.href = "/login";
    throw new Error("Session expired. Please log in.");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(err.detail ?? err.msg ?? "Request failed");
  }
  return res.json() as Promise<T>;
}

// For endpoints that return a raw Response (file downloads, multipart uploads)
function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: { ..._authHeader(), ...(init?.headers as Record<string, string> ?? {}) },
  });
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TorrentResult {
  title: string;
  size: string;
  size_bytes: number;
  seeds: number;
  leeches: number;
  magnet: string;
  info_hash?: string;
  quality?: string;
  source: string;
  url?: string;
}

export interface WatchlistItem {
  id: number;
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  last_checked?: string;
  last_found?: string;
  created_at: string;
  show_status: "Running" | "Ended" | "To Be Determined" | "In Development" | "Unknown" | null;
  show_status_checked_at: string | null;
  tvmaze_id: number | null;
  show_status_override: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
  poster_url?: string | null;
  summary?: string | null;
  network?: string | null;
  premiered?: string | null;
  metadata_checked_at?: string | null;
}

export interface QualityProfile {
  id: number;
  name: string;
  quality: string;
  codec: string;
  max_size_mb: number | null;
  preferred_groups: string | null;
  prefer_season_packs: boolean;
  fallback_quality: string | null;
  fallback_after_hours: number;
  watchlist_count: number;
}

export type ProfileInput = Omit<QualityProfile, "id" | "watchlist_count">;

export interface JackettTestResult {
  ok: boolean;
  kind?: string;
  version?: string | null;
  indexers?: number;
  error?: string;
}

export interface WatchlistCreate {
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
}

export interface Download {
  id: number;
  title: string;
  torrent_hash?: string;
  size_bytes?: number;
  status: string;
  download_path?: string;
  watchlist_id?: number;
  created_at: string;
  progress?: number;
  eta?: number;
  dlspeed?: number;
  is_stale?: boolean;
}

export interface DownloadPath {
  id: number;
  name: string;
  path: string;
  is_default: boolean;
  free_bytes?: number | null;
  total_bytes?: number | null;
  low_space?: boolean;
}

export type Settings = Record<string, string>;

export interface PlexLibrary {
  key: string;
  title: string;
  type: string;
}

export interface PlexTestResult {
  ok: boolean;
  error?: string;
  libraries?: PlexLibrary[];
}

export interface JellyfinTestResult {
  ok: boolean;
  error?: string;
  server_name?: string;
  version?: string;
}

export interface BackupMeta {
  created_at: string;
  app_version: string;
  db_path: string;
}

export interface RestoreResponse {
  ok: boolean;
  restored_from?: string;
  message?: string;
  error?: string;
}

export interface DownloadHistoryItem {
  id: number;
  name: string;
  source: "manual" | "watchlist";
  indexer: string | null;
  folder: string | null;
  torrent_hash: string | null;
  size_bytes: number | null;
  size_human: string;
  added_at: string;        // ISO 8601 with Z suffix
  completed_at: string | null;
  status: "downloading" | "completed" | "failed";
  watchlist_id: number | null;
  error_msg: string | null;
}

export interface HistoryResponse {
  items: DownloadHistoryItem[];
  total: number;
}

export interface WatchlistEpisode {
  id: number;
  watchlist_id: number;
  season: number;
  episode: number;
  downloaded_at: string;       // ISO 8601 with Z suffix
  torrent_hash: string | null;
  torrent_name: string | null;
}

export type EpisodeStatus = "downloaded" | "missing" | "upcoming";

export interface GuideEpisode {
  number: number;
  name: string;
  airs_at: string | null;
  status: EpisodeStatus;
  torrent_name: string | null;
}

export interface GuideSeason {
  season: number;
  episodes: GuideEpisode[];
  total: number;
  aired: number;
  downloaded: number;
}

export interface EpisodeGuide {
  available: boolean;
  pointer: { season: number; episode: number };
  seasons: GuideSeason[];
}

export interface EpisodeSearchResult {
  status: "started" | "not_found" | "already_downloaded" | "duplicate";
  message: string;
  title?: string;
}

export interface CalendarEntry {
  watchlist_id: number;
  title: string;
  poster_url: string | null;
  enabled: boolean;
  season: number;
  episode: number;
  name: string;
  airs_at: string;
  status: EpisodeStatus;
}

export interface CalendarResponse {
  entries: CalendarEntry[];
  unmatched: number;
}

export interface BrowseShow {
  tvmaze_id: number;
  name: string;
  poster_url: string | null;
  network: string | null;
  premiered: string | null;
  status: string | null;
  genres: string[];
  rating: number | null;
  summary: string | null;
  next_airs: string | null;
  next_episode: string | null;
  season_premiere: boolean;
  on_watchlist: boolean;
  watchlist_id: number | null;
}

export interface BrowseResponse {
  mode: "airing" | "search";
  country: string;
  shows: BrowseShow[];
}

export interface TraktResult {
  ok: boolean;
  error: string | null;
  at: string;
  checked: number;
  added: string[];
  existing: number;
  unmatched: string[];
  deferred: number;
}

export interface TraktStatus {
  configured: boolean;
  enabled: boolean;
  last_result: TraktResult | null;
  seen_count: number;
  max_new_per_run: number;
}

export interface MarkEpisodeRequest {
  season: number;
  episode: number;
  torrent_name?: string;
}

export interface DuplicateCheckResult {
  is_duplicate: boolean;
  match_type: "hash" | "name" | "active" | null;
  matched_name: string | null;
  matched_at: string | null;
}

export interface LatestRelease {
  version: string;
  name: string;
  notes: string;
  url: string;
  published_at: string;
}

export interface HelperInfo {
  configured: boolean;
  connected: boolean;
  last_seen: string | null;
  every_minutes: number;
}

export interface UpdateStatus {
  current_version: string;
  latest_release: LatestRelease | null;
  update_available: boolean;
  checked_at: string | null;
  check_error: string | null;
  helper: HelperInfo;
  upgrade_state: "requested" | "running" | "succeeded" | "failed" | null;
  upgrade_message: string | null;
  can_upgrade: boolean;
  previous_version: string | null;
  can_rollback: boolean;
  helper_install_cmd: string;
  upgrade_log?: string;
}

// ── API client ────────────────────────────────────────────────────────────────

export type StepStatus = "pending" | "running" | "done" | "failed" | "cancelled" | "skipped";

export interface JourneyStep {
  kind: "found" | "download" | "encode" | "split" | "trim" | string;
  label: string;
  status: StepStatus;
  progress?: number;
  speed?: number | null;
  eta?: number | null;
  detail?: string | null;
  log?: string | null;
  stale?: boolean;
  size_bytes?: number | null;
  saved_bytes?: number;
  path?: string | null;
  retry_count?: number;
  reason?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  ref: { type: "download" | "media_job" | "plan"; id: number };
}

export interface Journey {
  id: number;
  title: string;
  source: "download" | "library";
  status: "active" | "done" | "failed" | "cancelled";
  download_id: number | null;
  media_file_id: number | null;
  poster_url: string | null;
  created_at: string | null;
  updated_at: string | null;
  current_step: number;
  saved_bytes: number;
  steps: JourneyStep[];
}

export type ActivityFilter = "all" | "active" | "failed" | "done_today";

export interface ActivityStats {
  active: number;
  failed_24h: number;
  saved_today_bytes: number;
  queue_paused: boolean;
}

export interface ProgressEvent {
  key: string;
  journey_id: number | null;
  download_id?: number;
  job_id?: number;
  progress: number;
  speed?: number | null;
  eta?: number | null;
  log?: string | null;
}

export type PipelineActionType = "trim" | "encode" | "split";

export interface PipelineAction {
  type: PipelineActionType;
  resolution?: string;
  episode_count?: number;
}

export interface PipelineMatch {
  source?: "any" | "download" | "scan";
  codecs?: string[];
  min_height?: number | null;
  max_height?: number | null;
  min_size_mb?: number | null;
  min_duration_min?: number | null;
  max_duration_min?: number | null;
  keywords?: string[];
  anthology?: boolean | null;
  path_prefix?: string | null;
  watchlist_ids?: number[];
}

export interface PipelineRule {
  id: number;
  name: string;
  enabled: boolean;
  priority: number;
  match: PipelineMatch;
  actions: PipelineAction[];
}

export type PipelineRuleInput = Omit<PipelineRule, "id" | "priority"> & { priority?: number };

export interface PipelineTemplate {
  name: string;
  description: string;
  match: PipelineMatch;
  actions: PipelineAction[];
}

export interface PipelineSettings {
  pipeline_enabled: boolean;
  pipeline_on_scan: boolean;
  pipeline_remove_torrent: boolean;
}

export interface PathMappingRow {
  id?: number;
  remote_prefix: string;
  local_prefix: string;
}

export interface PipelinePreviewItem {
  file_id: number;
  filename: string;
  path: string;
  codec: string | null;
  height: number | null;
  size_bytes: number;
  rule_id: number | null;
  rule_name: string;
  actions: PipelineActionType[];
  est_saved_bytes: number;
}

export interface PipelinePreview {
  count: number;
  total_size_bytes: number;
  est_saved_bytes: number;
  encode_ratio: number;
  items: PipelinePreviewItem[];
}

export interface PathTestResult {
  path: string;
  mapped: string;
  exists: boolean;
  video_files: number;
  files: { path: string; filename?: string; codec?: string; height?: number; size_bytes?: number;
           rule_name?: string | null; actions?: string[]; error?: string }[];
}

export type MediaStatus = "pending" | "queued" | "processing" | "done" | "error" | "skipped" | "cancelled";
export type MediaJobType = "encode" | "split" | "trim";

export interface MediaFile {
  id: number;
  path: string;
  filename: string;
  size_bytes: number | null;
  duration_sec: number | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  is_anthology: number;
  anthology_override: number | null;
  status: MediaStatus;
  error_msg: string | null;
  category: string;
  updated_at: string | null;
}

export interface MediaStats {
  total: number; done: number; processing: number; queued: number; errors: number;
  skipped: number; pending: number; total_saved_bytes: number; paused: boolean; scan_running: boolean;
}

export interface MediaSettings {
  media_paths: string[];
  scene_threshold: number;
  min_scene_duration: number;
  scene_method: string;
  x265_crf: number;
  x265_preset: string;
  target_resolution: string;
  encoder_backend: "cpu" | "nvenc";
  max_workers: number;
  ffprobe_timeout: number;
  black_min_duration: number;
  black_pix_th: number;
  split_min_duration: number;
  split_min_size: number;
  split_keywords: string;
  split_scene_method: string;
  split_min_episode_gap: number;
  split_black_min_duration: number;
  split_freeze_min_duration: number;
  split_episode_count: number;
  tpdb_api_key_set: boolean;
  resolutions: string[];
  nvenc_available: boolean;
  nvenc_probe_done: boolean;
  nvenc_last_error: string | null;
  cpu_presets: string[];
  nvenc_presets: string[];
}

type MediaResult = { ok: boolean; msg?: string };

export const api = {
  search(q: string, indexer = "all", quality?: string, codec = "x265", filterAdult = true) {
    const p = new URLSearchParams({ q, indexer, codec, filter_adult: String(filterAdult) });
    if (quality && quality !== "Any") p.set("quality", quality);
    return req<TorrentResult[]>(`/search?${p}`);
  },

  watchlist: {
    list: () => req<WatchlistItem[]>("/watchlist"),
    create: (data: WatchlistCreate) =>
      req<WatchlistItem>("/watchlist", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<WatchlistCreate>) =>
      req<WatchlistItem>(`/watchlist/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/watchlist/${id}`, { method: "DELETE" }),
    scan: (id: number) => req(`/watchlist/${id}/scan`, { method: "POST" }),
    scanAll: () => req("/scan", { method: "POST" }),
    episodes: {
      list: (id: number) =>
        req<WatchlistEpisode[]>(`/watchlist/${id}/episodes`),
      mark: (id: number, data: MarkEpisodeRequest) =>
        req<WatchlistEpisode>(`/watchlist/${id}/episodes`, {
          method: "POST",
          body: JSON.stringify(data),
        }),
      deleteOne: (watchlistId: number, episodeId: number) =>
        req(`/watchlist/${watchlistId}/episodes/${episodeId}`, { method: "DELETE" }),
      reset: (id: number) =>
        req<{ deleted: number }>(`/watchlist/${id}/episodes?confirm=true`, { method: "DELETE" }),
    },
    guide: (id: number) => req<EpisodeGuide>(`/watchlist/${id}/guide`),
    searchEpisode: (id: number, season: number, episode: number) =>
      req<EpisodeSearchResult>(`/watchlist/${id}/episodes/${season}/${episode}/search`, { method: "POST" }),
    refreshMetadata: () =>
      req<{ checked: number; matched: number; remaining: number; unavailable: boolean }>("/watchlist/refresh-metadata", { method: "POST" }),
    addFromTvmaze: (data: { tvmaze_id: number; start?: "next" | "beginning" }) =>
      req<WatchlistItem>("/watchlist/from-tvmaze", { method: "POST", body: JSON.stringify(data) }),
    checkShowStatuses: () =>
      req<{ ok: boolean; message: string }>("/watchlist/check-show-statuses", { method: "POST" }),
    setOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "POST" }),
    removeOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "DELETE" }),
  },

  downloads: {
    list: () => req<Download[]>("/downloads"),
    add: (data: { magnet: string; title: string; download_path_id?: number; indexer?: string; force?: boolean }) =>
      req("/downloads", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/downloads/${id}`, { method: "DELETE" }),
    clearFinished: () => req<{ ok: boolean; deleted: number }>("/downloads", { method: "DELETE" }),
    checkDuplicate: (data: { torrent_hash: string | null; torrent_name: string }) =>
      req<DuplicateCheckResult>("/downloads/check-duplicate", { method: "POST", body: JSON.stringify(data) }),
  },

  activity: {
    list: (filter: ActivityFilter = "all", limit = 50, offset = 0) =>
      req<{ total: number; items: Journey[] }>(`/activity?filter=${filter}&limit=${limit}&offset=${offset}`),
    get: (id: number) => req<Journey>(`/activity/${id}`),
    stats: () => req<ActivityStats>("/activity/stats"),
    cancel: (id: number) => req<{ ok: boolean; msg: string }>(`/activity/${id}/cancel`, { method: "POST" }),
    retry: (id: number) => req<{ ok: boolean; msg: string }>(`/activity/${id}/retry`, { method: "POST" }),
    remove: (id: number) => req<{ ok: boolean }>(`/activity/${id}`, { method: "DELETE" }),
    clearFinished: () => req<{ ok: boolean; cleared: number }>("/activity/clear-finished", { method: "POST" }),
  },

  media: {
    files: () => req<MediaFile[]>("/media/files"),
    stats: () => req<MediaStats>("/media/stats"),
    scan: () => req<MediaResult>("/media/scan", { method: "POST" }),
    queue: (fileId: number, jobType: MediaJobType, targetResolution = "original", episodeCount = 0) =>
      req<MediaResult & { job_id: number }>("/media/queue", {
        method: "POST",
        body: JSON.stringify({ file_id: fileId, job_type: jobType, target_resolution: targetResolution, episode_count: episodeCount }),
      }),
    queueBulk: (fileIds: number[], jobType: MediaJobType, targetResolution = "original", episodeCount = 0) =>
      req<{ ok: boolean; queued: number; skipped: number }>("/media/queue/bulk", {
        method: "POST",
        body: JSON.stringify({ file_ids: fileIds, job_type: jobType, target_resolution: targetResolution, episode_count: episodeCount }),
      }),
    bulk: (action: "skip" | "delete", fileIds: number[]) =>
      req<{ ok: boolean; done: number; skipped: number; errors: string[] }>("/media/files/bulk", {
        method: "POST", body: JSON.stringify({ action, file_ids: fileIds }),
      }),
    rename: (id: number, newName: string) =>
      req<MediaResult & { filename?: string }>(`/media/files/${id}/rename`, { method: "POST", body: JSON.stringify({ new_name: newName }) }),
    remove: (id: number) => req<MediaResult>(`/media/files/${id}/delete`, { method: "POST" }),
    skip: (id: number) => req<MediaResult>(`/media/files/${id}/skip`, { method: "POST" }),
    cancel: (id: number) => req<MediaResult>(`/media/files/${id}/cancel`, { method: "POST" }),
    setAnthology: (id: number, value: boolean | null) =>
      req<MediaResult>(`/media/files/${id}/anthology`, { method: "POST", body: JSON.stringify({ value }) }),
    purge: () => req<{ ok: boolean; removed: number }>("/media/files/purge", { method: "POST" }),
    pause: () => req<{ ok: boolean; paused: boolean }>("/media/queue/pause", { method: "POST" }),
    resume: () => req<{ ok: boolean; paused: boolean }>("/media/queue/resume", { method: "POST" }),
    /** URL for <video src> / download links; carries the token because media elements can't send headers. */
    streamUrl: (id: number, download = false) => {
      const p = new URLSearchParams();
      const token = authStore.getToken();
      if (token) p.set("token", token);
      if (download) p.set("download", "1");
      const q = p.toString();
      return `${BASE}/media/files/${id}/stream${q ? `?${q}` : ""}`;
    },
    settings: () => req<MediaSettings>("/media/settings"),
    saveSettings: (data: Record<string, unknown>) =>
      req<MediaResult & { rejected?: string[] }>("/media/settings", { method: "POST", body: JSON.stringify(data) }),
    recheckGpu: () => req<{ nvenc_available: boolean; nvenc_last_error: string | null }>("/media/encoder/recheck-gpu", { method: "POST" }),
    setSavedBytes: (value: number) =>
      req<{ ok: boolean; total_saved_bytes: number }>("/media/settings/saved-bytes", { method: "POST", body: JSON.stringify({ value }) }),
    trash: () => req<{ size_bytes: number; file_count: number }>("/media/trash"),
    emptyTrash: () => req<{ ok: boolean; freed_bytes: number; removed_files: number }>("/media/trash/empty", { method: "POST" }),
  },

  pipeline: {
    settings: () => req<PipelineSettings>("/pipeline/settings"),
    saveSettings: (data: Partial<PipelineSettings>) =>
      req<PipelineSettings>("/pipeline/settings", { method: "PUT", body: JSON.stringify(data) }),
    templates: () => req<PipelineTemplate[]>("/pipeline/templates"),
    rules: () => req<PipelineRule[]>("/pipeline/rules"),
    createRule: (data: PipelineRuleInput) =>
      req<PipelineRule>("/pipeline/rules", { method: "POST", body: JSON.stringify(data) }),
    updateRule: (id: number, data: PipelineRuleInput) =>
      req<PipelineRule>(`/pipeline/rules/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    deleteRule: (id: number) => req(`/pipeline/rules/${id}`, { method: "DELETE" }),
    reorder: (ids: number[]) =>
      req<PipelineRule[]>("/pipeline/rules/reorder", { method: "POST", body: JSON.stringify({ ids }) }),
    mappings: () => req<PathMappingRow[]>("/pipeline/mappings"),
    saveMappings: (rows: PathMappingRow[]) =>
      req<PathMappingRow[]>("/pipeline/mappings", { method: "PUT", body: JSON.stringify(rows) }),
    testPath: (path: string) =>
      req<PathTestResult>("/pipeline/test-path", { method: "POST", body: JSON.stringify({ path }) }),
    preview: (rule?: Partial<PipelineRuleInput>) =>
      req<PipelinePreview>("/pipeline/preview", { method: "POST", body: JSON.stringify(rule ? { rule } : {}) }),
    apply: (fileIds: number[], ruleId?: number | null) =>
      req<{ ok: boolean; queued: number }>("/pipeline/apply", {
        method: "POST", body: JSON.stringify({ file_ids: fileIds, rule_id: ruleId ?? undefined }),
      }),
  },

  history: {
    list: (params: string) => req<HistoryResponse>(`/history?${params}`),
    /** Fetch the full history as a CSV file; returns a raw Response. */
    export: (): Promise<Response> => authedFetch(`/history/export`),
    deleteOne: (id: number) => req(`/history/${id}`, { method: "DELETE" }),
    clearAll: () => req<{ deleted: number }>("/history?confirm=true", { method: "DELETE" }),
  },

  paths: {
    list: () => req<DownloadPath[]>("/paths"),
    create: (data: { name: string; path: string; is_default: boolean }) =>
      req<DownloadPath>("/paths", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<{ name: string; path: string; is_default: boolean }>) =>
      req<DownloadPath>(`/paths/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/paths/${id}`, { method: "DELETE" }),
  },

  trakt: {
    status: () => req<TraktStatus>("/trakt/status"),
    test: (data: { client_id: string; username: string }) =>
      req<{ ok: boolean; shows?: number; error?: string }>("/trakt/test", { method: "POST", body: JSON.stringify(data) }),
    sync: () => req<TraktResult>("/trakt/sync", { method: "POST" }),
    forgetHistory: () => req<{ ok: boolean; cleared: number }>("/trakt/seen", { method: "DELETE" }),
  },

  onboarding: {
    status: () => req<{ needed: boolean }>("/onboarding/status"),
    complete: () => req<{ ok: boolean }>("/onboarding/complete", { method: "POST" }),
  },

  calendar: {
    get: (days = 30, back = 7) => req<CalendarResponse>(`/calendar?days=${days}&back=${back}`),
  },

  browse: {
    get: (q = "") => req<BrowseResponse>(`/browse${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  },

  profiles: {
    list: () => req<QualityProfile[]>("/profiles"),
    create: (data: ProfileInput) =>
      req<QualityProfile>("/profiles", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<ProfileInput>) =>
      req<QualityProfile>(`/profiles/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) =>
      req<{ ok: boolean; detached: number }>(`/profiles/${id}`, { method: "DELETE" }),
  },

  backup: {
    /** Fetch the export ZIP; returns the raw Response so the caller can stream
     *  the blob — do not pass through req<T>() which calls .json(). */
    export: (): Promise<Response> => authedFetch(`/backup/export`),
    /** Upload a backup ZIP; returns the raw Response so the caller can inspect
     *  both success JSON and error JSON without an intermediate throw. */
    restore: (file: File): Promise<Response> => {
      const form = new FormData();
      form.append("file", file);
      return authedFetch(`/backup/restore`, { method: "POST", body: form });
    },
  },

  auth: {
    status: () => req<{ auth_required: boolean }>("/auth/status"),
    login: (password: string) =>
      req<{ token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    revoke: () => req<{ ok: boolean }>("/auth/revoke", { method: "POST" }),
  },

  updates: {
    status: () => req<UpdateStatus>("/admin/updates"),
    check: () => req<UpdateStatus>("/admin/updates/check", { method: "POST" }),
    upgrade: (version: string, rollback = false) =>
      req<UpdateStatus>("/admin/updates/upgrade", {
        method: "POST",
        body: JSON.stringify({ version, rollback }),
      }),
  },

  settings: {
    get: () => req<Settings>("/settings"),
    update: (data: Partial<Settings>) =>
      req<Settings>("/settings", { method: "PUT", body: JSON.stringify(data) }),
    testQbit: () =>
      req<{ success: boolean; message: string }>("/settings/test-qbit", { method: "POST" }),
    testPlex: (data: { url: string; token: string; library_key?: string }) =>
      req<PlexTestResult>("/settings/test-plex", { method: "POST", body: JSON.stringify(data) }),
    testTmdb: (api_key: string) =>
      req<{ ok: boolean; message: string }>("/settings/test-tmdb", { method: "POST", body: JSON.stringify({ api_key }) }),
    testJackett: (data: { url: string; api_key: string }) =>
      req<JackettTestResult>("/settings/test-jackett", { method: "POST", body: JSON.stringify(data) }),
    testJellyfin: (data: { url: string; api_key: string }) =>
      req<JellyfinTestResult>("/settings/test-jellyfin", { method: "POST", body: JSON.stringify(data) }),
    testDigest: (data: {
      digest_smtp_host: string; digest_smtp_port: string; digest_smtp_user: string;
      digest_smtp_password: string; digest_from_email: string; digest_recipients: string;
      plex_url: string; plex_token: string; digest_excluded_libs: string;
    }) =>
      req<{ ok: boolean; message?: string; error?: string }>("/settings/test-digest", { method: "POST", body: JSON.stringify(data) }),
  },
};
