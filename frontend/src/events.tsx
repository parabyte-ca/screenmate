import { createContext, useContext, useEffect, useRef, useState } from "react";
import { authStore } from "./auth";

// One shared EventSource for the whole app (GET /api/events). The browser
// reconnects on its own and resumes with Last-Event-ID; if the stream is closed
// for good (e.g. a 401) we rebuild it with backoff.

export type EventType = "journey.updated" | "journey.removed" | "progress" | "stats" | "resync";
const TYPES: EventType[] = ["journey.updated", "journey.removed", "progress", "stats", "resync"];

type Handler = (data: any) => void;
type Registry = Map<EventType, Set<Handler>>;

interface EventsCtx {
  connected: boolean;
  registry: Registry;
}

const Ctx = createContext<EventsCtx | null>(null);

export function EventsProvider({ children }: { children: React.ReactNode }) {
  const registry = useRef<Registry>(new Map(TYPES.map((t) => [t, new Set<Handler>()]))).current;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const open = () => {
      const token = authStore.getToken();
      source = new EventSource(token ? `/api/events?token=${encodeURIComponent(token)}` : "/api/events");
      source.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      source.onerror = () => {
        setConnected(false);
        if (source?.readyState === EventSource.CLOSED && !stopped) {
          source.close();
          const delay = Math.min(30_000, 1000 * 2 ** attempt++);
          retryTimer = window.setTimeout(open, delay);
        }
      };
      for (const type of TYPES) {
        source.addEventListener(type, (e) => {
          let data: unknown = {};
          try {
            data = JSON.parse((e as MessageEvent).data || "{}");
          } catch {
            return;
          }
          registry.get(type)?.forEach((fn) => fn(data));
        });
      }
    };

    open();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      source?.close();
    };
  }, [registry]);

  return <Ctx.Provider value={{ connected, registry }}>{children}</Ctx.Provider>;
}

/** Subscribe to one event type for the lifetime of the calling component. */
export function useEvent<T = any>(type: EventType, handler: (data: T) => void) {
  const ctx = useContext(Ctx);
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!ctx) return;
    const fn: Handler = (d) => ref.current(d);
    ctx.registry.get(type)!.add(fn);
    return () => {
      ctx.registry.get(type)!.delete(fn);
    };
  }, [ctx, type]);
}

export function useLiveConnected(): boolean {
  return useContext(Ctx)?.connected ?? false;
}
