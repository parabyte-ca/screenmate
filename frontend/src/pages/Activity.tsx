import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Activity as ActivityIcon, AlertTriangle, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { api, type ActivityFilter, type Journey, type ProgressEvent } from "../api/client";
import { useEvent, useLiveConnected } from "../events";
import { fmtAgo, fmtSize } from "../utils";
import JourneyDrawer from "../components/JourneyDrawer";
import {
  applyProgress, fmtEta, fmtSpeed, ProgressBar, STATUS_STYLE, StatusIcon, StepStrip,
} from "../components/journey";

const FILTERS: { value: ActivityFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "failed", label: "Failed" },
  { value: "done_today", label: "Done today" },
];

function matches(j: Journey, f: ActivityFilter) {
  if (f === "all") return true;
  if (f === "active") return j.status === "active";
  if (f === "failed") return j.status === "failed";
  return j.status === "done" && !!j.updated_at && new Date(j.updated_at).toDateString() === new Date().toDateString();
}

function JourneyCard({ j, onOpen }: { j: Journey; onOpen: () => void }) {
  const cur = j.current_step >= 0 ? j.steps[j.current_step] : null;
  const running = cur?.status === "running";
  const speed = running ? fmtSpeed(cur?.speed) : null;
  const eta = running ? fmtEta(cur?.eta) : null;
  return (
    <li>
      <button onClick={onOpen}
              className="flex w-full gap-3 px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/60 focus:outline-none focus-visible:bg-gray-50 dark:focus-visible:bg-gray-800/60">
        {j.poster_url ? (
          <img src={j.poster_url} alt="" loading="lazy" className="h-14 w-10 shrink-0 rounded object-cover" />
        ) : (
          <div className="flex h-14 w-10 shrink-0 items-center justify-center rounded bg-gray-100 dark:bg-gray-800 text-gray-400">
            <ActivityIcon className="h-4 w-4" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <p className="flex-1 text-sm font-medium text-gray-900 dark:text-gray-100 line-clamp-1">{j.title}</p>
            <span className="shrink-0 text-xs text-gray-400">{fmtAgo(j.updated_at)}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <StepStrip steps={j.steps} current={j.current_step} />
            {cur && (
              <span className={`inline-flex items-center gap-1 text-xs ${STATUS_STYLE[cur.status].text}`}>
                <StatusIcon status={cur.status} className="h-3 w-3" />
                {cur.label} · {STATUS_STYLE[cur.status].label}
              </span>
            )}
            {j.saved_bytes > 0 && (
              <span className="text-xs text-green-600 dark:text-green-400">−{fmtSize(j.saved_bytes)}</span>
            )}
          </div>
          {cur?.stale && (
            <p className="mt-1.5 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3 w-3" />Stalled — open for Re-search
            </p>
          )}
          {running && (
            <div className="mt-2">
              <ProgressBar pct={cur?.progress ?? 0} />
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {Math.round(cur?.progress ?? 0)}%{speed && ` · ${speed}`}{eta && ` · ETA ${eta}`}
              </p>
            </div>
          )}
          {cur?.status === "failed" && cur.detail && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400 line-clamp-1">{cur.detail}</p>
          )}
        </div>
      </button>
    </li>
  );
}

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const filter = (params.get("filter") as ActivityFilter) || "all";
  const openId = params.get("open") ? Number(params.get("open")) : null;
  const [items, setItems] = useState<Journey[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const live = useLiveConnected();
  const itemsRef = useRef<Journey[]>([]);
  itemsRef.current = items;

  const load = useCallback(async () => {
    try {
      const r = await api.activity.list(filter, 100);
      setItems(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Live updates: upsert snapshots, apply transient progress, drop removed rows.
  useEvent<Journey>("journey.updated", (snap) => {
    const had = itemsRef.current.some((j) => j.id === snap.id);
    const keep = matches(snap, filter);
    if (had !== keep) setTotal((t) => t + (keep ? 1 : -1));
    setItems((cur) => {
      const rest = cur.filter((j) => j.id !== snap.id);
      return keep ? [snap, ...rest] : rest;
    });
  });
  useEvent<ProgressEvent>("progress", (ev) => {
    if (ev.journey_id == null) return;
    setItems((cur) => cur.map((j) => (j.id === ev.journey_id ? applyProgress(j, ev) : j)));
  });
  useEvent<{ id: number }>("journey.removed", ({ id }) => {
    if (itemsRef.current.some((j) => j.id === id)) setTotal((t) => t - 1);
    setItems((cur) => cur.filter((j) => j.id !== id));
  });
  useEvent("resync", () => load());

  const setFilter = (f: ActivityFilter) => {
    const next = new URLSearchParams(params);
    if (f === "all") next.delete("filter"); else next.set("filter", f);
    setParams(next, { replace: true });
  };
  const setOpen = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id == null) next.delete("open"); else next.set("open", String(id));
    setParams(next, { replace: true });
  };

  const clearFinished = async () => {
    if (!confirm("Remove every finished, failed and cancelled item from Activity?")) return;
    await api.activity.clearFinished();
    load();
  };

  return (
    <div className="mx-auto max-w-4xl p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="flex items-center gap-2 text-xl font-bold text-gray-900 dark:text-gray-100">
          Activity
          <span title={live ? "Live" : "Reconnecting…"}
                className={`h-2 w-2 rounded-full ${live ? "bg-green-500" : "bg-gray-400"}`} />
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={load} title="Refresh"
                  className="flex min-h-[40px] min-w-[40px] items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800">
            <RefreshCw className="h-4 w-4" />
          </button>
          <button onClick={clearFinished}
                  className="flex min-h-[40px] items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800">
            <Trash2 className="h-3.5 w-3.5" />Clear finished
          </button>
        </div>
      </div>

      <div className="mb-3 flex gap-2 overflow-x-auto" role="tablist">
        {FILTERS.map((f) => (
          <button key={f.value} role="tab" aria-selected={filter === f.value} onClick={() => setFilter(f.value)}
                  className={`shrink-0 rounded-full px-3 py-1.5 text-sm transition-colors ${
                    filter === f.value
                      ? "bg-blue-600 text-white"
                      : "bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}>
            {f.label}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
        {loading ? (
          <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-blue-600" /></div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center text-sm text-gray-500 dark:text-gray-400">
            <ActivityIcon className="mx-auto mb-2 h-6 w-6 text-gray-300 dark:text-gray-600" />
            {filter === "all"
              ? "Nothing yet. Downloads and encodes will show up here as they happen."
              : "Nothing matches this filter."}
          </div>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {items.map((j) => <JourneyCard key={j.id} j={j} onOpen={() => setOpen(j.id)} />)}
          </ul>
        )}
      </div>
      {total > items.length && (
        <p className="mt-2 text-center text-xs text-gray-400">Showing the latest {items.length} of {total}</p>
      )}

      {openId != null && <JourneyDrawer journeyId={openId} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
