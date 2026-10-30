import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ActivityStats } from "../api/client";
import { useEvent, useLiveConnected } from "../events";
import { fmtSize } from "../utils";

/** "3 active · 1 failed · 12.4 GB saved today" — live, from the stats event. */
export default function StatusPill({ compact = false }: { compact?: boolean }) {
  const [stats, setStats] = useState<ActivityStats | null>(null);
  const live = useLiveConnected();

  useEffect(() => {
    api.activity.stats().then(setStats).catch(() => {});
  }, []);
  useEvent<ActivityStats>("stats", setStats);

  const active = stats?.active ?? 0;
  const failed = stats?.failed_24h ?? 0;
  const saved = stats?.saved_today_bytes ?? 0;
  const label = [
    `${active} active`,
    failed ? `${failed} failed` : null,
    saved ? `${fmtSize(saved)} saved today` : null,
    stats?.queue_paused ? "queue paused" : null,
  ].filter(Boolean).join(" · ");

  const dot = (
    <span
      className={`relative inline-flex h-2 w-2 shrink-0 rounded-full ${
        failed ? "bg-red-500" : active ? "bg-blue-500" : live ? "bg-green-500" : "bg-gray-400"
      }`}
    >
      {active > 0 && live && (
        <span className="absolute inset-0 animate-ping rounded-full bg-blue-400 opacity-60" />
      )}
    </span>
  );

  if (compact) {
    return (
      <Link to="/activity" title={`${label}${live ? "" : " (reconnecting…)"}`}
            className="mx-auto flex h-6 w-6 items-center justify-center rounded-md hover:bg-gray-100 dark:hover:bg-gray-800">
        {dot}
      </Link>
    );
  }

  return (
    <Link
      to="/activity"
      title={`${label}${live ? "" : " (reconnecting…)"}`}
      className="flex items-center gap-2 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1 text-xs text-gray-600 dark:text-gray-300 hover:border-blue-300 dark:hover:border-blue-700 transition-colors"
    >
      {dot}
      <span className="truncate">{stats ? label : "Loading…"}</span>
    </Link>
  );
}
