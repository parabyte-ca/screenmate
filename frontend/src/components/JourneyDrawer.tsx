import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, RotateCcw, Search, Square, Trash2, X } from "lucide-react";
import { api, type Journey, type ProgressEvent } from "../api/client";
import { useEvent } from "../events";
import { fmtDate, fmtSize } from "../utils";
import {
  applyProgress, fmtDuration, fmtEta, fmtSpeed, ProgressBar, STATUS_STYLE, StatusIcon, StepIcon,
} from "./journey";

interface Props {
  journeyId: number;
  onClose: () => void;
  onChanged: () => void;
}

export default function JourneyDrawer({ journeyId, onClose, onChanged }: Props) {
  const [j, setJ] = useState<Journey | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [openLog, setOpenLog] = useState<number | null>(null);

  const load = () => api.activity.get(journeyId).then(setJ).catch(() => setJ(null));
  useEffect(() => {
    load();
    setMsg(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journeyId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEvent<Journey>("journey.updated", (snap) => {
    if (snap.id === journeyId) load(); // refetch for the full logs
  });
  useEvent<ProgressEvent>("progress", (ev) => {
    if (ev.journey_id === journeyId) setJ((cur) => (cur ? applyProgress(cur, ev) : cur));
  });
  useEvent<{ id: number }>("journey.removed", ({ id }) => id === journeyId && onClose());

  const act = async (fn: () => Promise<{ msg?: string }>) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg(r.msg ?? null);
      onChanged();
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const current = j && j.current_step >= 0 ? j.steps[j.current_step] : null;
  const canCancel = current && (current.status === "running" || current.status === "pending");
  const canRetry = current && (current.status === "failed" || current.status === "cancelled");
  const searchHref = j ? `/search?q=${encodeURIComponent(j.title.replace(/S\d+E\d+.*/i, "").trim())}` : "/search";

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Journey details">
      <button className="absolute inset-0 bg-black/30" aria-label="Close" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col bg-white dark:bg-gray-900 shadow-xl">
        <header className="flex items-start gap-3 border-b border-gray-200 dark:border-gray-700 p-4">
          {j?.poster_url && <img src={j.poster_url} alt="" className="h-16 w-11 shrink-0 rounded object-cover" />}
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 break-words">{j?.title ?? "…"}</h2>
            {j && (
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Started {fmtDate(j.created_at)}
                {j.saved_bytes > 0 && <> · <span className="text-green-600 dark:text-green-400">saved {fmtSize(j.saved_bytes)}</span></>}
              </p>
            )}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-4">
          {!j ? (
            <Loader2 className="mx-auto mt-8 h-5 w-5 animate-spin text-blue-600" />
          ) : (
            <ol className="relative space-y-4 border-l border-gray-200 dark:border-gray-700 pl-6">
              {j.steps.map((s, i) => {
                const style = STATUS_STYLE[s.status];
                const dur = fmtDuration(s.started_at, s.finished_at);
                const live = s.status === "running";
                return (
                  <li key={`${s.kind}-${s.ref.id}-${i}`} className="relative">
                    <span className={`absolute -left-[37px] flex h-6 w-6 items-center justify-center rounded-full border-2 bg-white dark:bg-gray-900 ${style.ring}`}>
                      <StepIcon step={s} className="h-3 w-3" />
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{s.label}</span>
                      <span className={`inline-flex items-center gap-1 text-xs ${style.text}`}>
                        <StatusIcon status={s.status} className="h-3 w-3" />{style.label}
                      </span>
                      {dur && <span className="ml-auto text-xs text-gray-400">{dur}</span>}
                    </div>
                    {s.kind === "found" && s.detail && (
                      <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{s.detail}</p>
                    )}
                    {s.kind === "download" && (
                      <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                        {fmtSize(s.size_bytes)}
                        {s.retry_count ? ` · retry #${s.retry_count}` : ""}
                        {s.path ? ` · ${s.path}` : ""}
                      </p>
                    )}
                    {live && (
                      <div className="mt-2 space-y-1">
                        <ProgressBar pct={s.progress ?? 0} />
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {Math.round(s.progress ?? 0)}%
                          {fmtSpeed(s.speed) && ` · ${fmtSpeed(s.speed)}`}
                          {fmtEta(s.eta) && ` · ETA ${fmtEta(s.eta)}`}
                        </p>
                      </div>
                    )}
                    {s.stale && (
                      <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        <span>Stalled — no progress for a while.</span>
                        <Link to={searchHref} className="ml-auto flex items-center gap-1 font-medium hover:underline">
                          <Search className="h-3 w-3" />Re-search
                        </Link>
                      </div>
                    )}
                    {s.reason && (
                      <p className="mt-0.5 text-xs text-violet-600 dark:text-violet-400">{s.reason}</p>
                    )}
                    {s.ref.type === "plan" && s.detail && (
                      <p className={`mt-0.5 text-xs ${s.status === "failed" ? "text-red-600 dark:text-red-400" : "text-gray-500 dark:text-gray-400"}`}>
                        {s.detail}
                      </p>
                    )}
                    {s.ref.type === "media_job" && s.status !== "running" && s.detail && (
                      <p className={`mt-0.5 text-xs ${s.status === "failed" ? "text-red-600 dark:text-red-400" : "text-gray-500 dark:text-gray-400"}`}>
                        {s.detail}
                      </p>
                    )}
                    {s.log && s.log !== s.detail && (
                      <div className="mt-1">
                        <button onClick={() => setOpenLog(openLog === i ? null : i)}
                                className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                          {openLog === i ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                          Log
                        </button>
                        {openLog === i && (
                          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-gray-100 dark:bg-gray-800 p-2 text-[11px] text-gray-700 dark:text-gray-300">
                            {s.log}
                          </pre>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <footer className="border-t border-gray-200 dark:border-gray-700 p-4">
          {msg && <p className="mb-2 text-xs text-gray-600 dark:text-gray-300">{msg}</p>}
          <div className="flex flex-wrap gap-2">
            {canCancel && (
              <button disabled={busy} onClick={() => act(() => api.activity.cancel(journeyId))}
                      className="flex min-h-[40px] items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                <Square className="h-3.5 w-3.5" />Cancel {current?.label.toLowerCase()}
              </button>
            )}
            {canRetry && (
              <button disabled={busy} onClick={() => act(() => api.activity.retry(journeyId))}
                      className="flex min-h-[40px] items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                <RotateCcw className="h-3.5 w-3.5" />Retry {current?.label.toLowerCase()}
              </button>
            )}
            {j && j.status !== "active" && (
              <button disabled={busy}
                      onClick={() => act(async () => { await api.activity.remove(journeyId); onClose(); return {}; })}
                      className="ml-auto flex min-h-[40px] items-center gap-1.5 rounded-lg px-3 text-sm text-gray-500 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-600">
                <Trash2 className="h-3.5 w-3.5" />Remove
              </button>
            )}
          </div>
        </footer>
      </aside>
    </div>
  );
}
