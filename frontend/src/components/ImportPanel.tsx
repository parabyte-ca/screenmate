import { useState } from "react";
import { AlertCircle, CheckCircle2, Database, Loader2, Upload } from "lucide-react";
import { api, type ImportInspect, type ImportRunResult } from "../api/client";
import { fmtSize } from "../utils";

// Bring an AuTorrent and/or FileSplitter database into ScreenMate. Used by the
// setup wizard and Settings → Data management.

const LABEL: Record<string, string> = {
  watchlist: "shows", episodes: "episodes", downloads: "downloads", history: "history entries",
  folders: "download folders", profiles: "quality profiles", settings: "settings", trakt_seen: "Trakt entries",
  files: "files", jobs: "jobs",
};

function summary(counts: Record<string, number>, keys: string[]) {
  const word = (k: string, n: number) => {
    const w = LABEL[k] ?? k;
    if (n !== 1) return w;
    if (w.endsWith("ies")) return `${w.slice(0, -3)}y`;
    return w.endsWith("s") && w !== "settings" ? w.slice(0, -1) : w;
  };
  return keys.filter((k) => counts[k]).map((k) => `${counts[k]} ${word(k, counts[k])}`).join(" · ") || "nothing new";
}

function commonDir(paths: string[]) {
  if (!paths.length) return "";
  const parts = paths.map((p) => p.split("/").slice(0, -1));
  const first = parts[0];
  let n = first.length;
  for (const p of parts) while (n > 0 && p.slice(0, n).join("/") !== first.slice(0, n).join("/")) n--;
  return first.slice(0, n).join("/") || "/";
}

export default function ImportPanel({ onImported }: { onImported?: (r: ImportRunResult) => void }) {
  const [uploads, setUploads] = useState<ImportInspect[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [result, setResult] = useState<ImportRunResult | null>(null);

  const pick = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const found = await api.importer.inspect([...files]);
      setUploads(found);
      const fs = found.find((u) => u.kind === "filesplitter");
      if (fs?.counts && fs.counts.files_checked && fs.counts.files_found_here < fs.counts.files_checked) {
        setFrom(commonDir(fs.sample_paths ?? []));
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const valid = uploads.filter((u) => u.token && u.kind);
  const fs = valid.find((u) => u.kind === "filesplitter");
  const needsRewrite = !!fs?.counts && (fs.counts.files_checked ?? 0) > 0 && fs.counts.files_found_here < fs.counts.files_checked;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.importer.run(valid.map((u) => u.token!), {
        overwrite_settings: overwrite,
        ...(from.trim() && to.trim() ? { path_rewrite: { from: from.trim(), to: to.trim() } } : {}),
      });
      setResult(r);
      setUploads([]);
      onImported?.(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <div className="rounded-lg border border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/30 p-3 text-sm text-green-800 dark:text-green-200">
        <p className="flex items-center gap-1.5 font-medium"><CheckCircle2 className="h-4 w-4" />Import complete</p>
        {result.results.map((r) => (
          <p key={r.token} className="mt-1">
            <b>{r.kind === "autorrent" ? "AuTorrent" : "FileSplitter"}:</b>{" "}
            {r.kind === "autorrent"
              ? summary(r.imported, ["watchlist", "episodes", "downloads", "history", "folders", "profiles", "settings"])
              : `${summary(r.imported, ["files", "jobs", "settings"])}${r.imported.saved_bytes ? ` · ${fmtSize(r.imported.saved_bytes)} saved so far` : ""}`}
            {r.imported.files_missing_here ? ` (${r.imported.files_missing_here} files aren't at their path here yet)` : ""}
          </p>
        ))}
        {result.backup && <p className="mt-2 text-xs opacity-80">Your previous database was saved as <code>{result.backup}</code>.</p>}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <label className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 p-5 text-center text-sm text-gray-600 dark:text-gray-300 hover:border-blue-400 ${busy ? "opacity-60" : ""}`}>
        {busy ? <Loader2 className="h-5 w-5 animate-spin text-blue-600" /> : <Upload className="h-5 w-5 text-gray-400" />}
        <span>Choose <code>autorrent.db</code> and/or <code>filesplitter.db</code></span>
        <span className="text-xs text-gray-400">They're in each app's <code>data/</code> folder. Nothing changes until you press Import.</span>
        <input type="file" multiple accept=".db,.sqlite,.sqlite3,application/octet-stream" className="sr-only"
               aria-label="Database files" disabled={busy} onChange={(e) => pick(e.target.files)} />
      </label>

      {uploads.map((u, i) => (
        <div key={i} className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${u.error ? "border-red-200 dark:border-red-900 text-red-700 dark:text-red-300" : "border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200"}`}>
          {u.error ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> : <Database className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />}
          <div className="min-w-0">
            <p className="font-medium">{u.filename}{u.kind && ` — ${u.kind === "autorrent" ? "AuTorrent" : "FileSplitter"}`}</p>
            {u.error ? <p className="text-xs">{u.error}</p> : u.counts && (
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {u.kind === "autorrent"
                  ? summary(u.counts, ["watchlist", "episodes", "downloads", "history", "folders", "profiles", "settings"])
                  : `${summary(u.counts, ["files", "jobs"])}${u.counts.saved_bytes ? ` · ${fmtSize(u.counts.saved_bytes)} saved so far` : ""}`}
              </p>
            )}
          </div>
        </div>
      ))}

      {needsRewrite && (
        <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-xs text-amber-800 dark:text-amber-200">
          <p className="mb-2">
            FileSplitter's files aren't at the same paths here ({fs!.counts!.files_found_here} of {fs!.counts!.files_checked} found),
            e.g. <code>{fs!.sample_paths?.[0]}</code>. If the folder moved, change it on the way in:
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <input className="min-w-0 flex-1 rounded border border-amber-300 dark:border-amber-800 bg-white dark:bg-gray-900 px-2 py-1 text-sm" aria-label="Old folder" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="/media/bellesa" />
            <span>→</span>
            <input className="min-w-0 flex-1 rounded border border-amber-300 dark:border-amber-800 bg-white dark:bg-gray-900 px-2 py-1 text-sm" aria-label="New folder" value={to} onChange={(e) => setTo(e.target.value)} placeholder="/mnt/nas/bellesa" />
          </div>
        </div>
      )}

      {valid.length > 0 && (
        <>
          <label className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input type="checkbox" className="mt-0.5" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
            <span>Replace settings I've already changed here with the imported ones
              <span className="block text-xs text-gray-500">Off: only settings you haven't set yet are filled in.</span></span>
          </label>
          <button onClick={run} disabled={busy}
                  className="flex min-h-[40px] items-center gap-1.5 rounded-lg bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
            Import {valid.length === 2 ? "both" : ""}
          </button>
          <p className="text-xs text-gray-500 dark:text-gray-400">Your current database is backed up first. Items you already have are skipped, so importing twice is safe.</p>
        </>
      )}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}
