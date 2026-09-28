import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { notifyUnauthorized, type LiveEvent } from "./api";

type Listener = (e: LiveEvent) => void;

interface LiveCtx {
  connected: boolean;
  subscribe: (fn: Listener) => () => void;
}

const Ctx = createContext<LiveCtx | null>(null);

/** One shared WebSocket to /api/live with exponential-backoff reconnect. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const [connected, setConnected] = useState(false);
  const [ctx] = useState<Omit<LiveCtx, "connected">>(() => ({
    subscribe: (fn: Listener) => {
      listeners.current.add(fn);
      return () => listeners.current.delete(fn);
    },
  }));

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/live`);
      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      ws.onmessage = (m) => {
        let ev: LiveEvent;
        try {
          ev = JSON.parse(m.data as string) as LiveEvent;
        } catch {
          return;
        }
        listeners.current.forEach((fn) => fn(ev));
      };
      ws.onclose = (e) => {
        setConnected(false);
        ws = null;
        if (stopped) return;
        if (e.code === 4401) {
          notifyUnauthorized();
          return;
        }
        const delay = Math.min(30000, 1000 * 2 ** attempt) * (0.75 + Math.random() * 0.5);
        attempt++;
        timer = window.setTimeout(connect, delay);
      };
    };

    connect();
    // Also reconnect quickly when the tab becomes visible again.
    const onVisible = () => {
      if (document.visibilityState === "visible" && !ws && !stopped) {
        window.clearTimeout(timer);
        attempt = 0;
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      ws?.close();
    };
  }, []);

  return <Ctx.Provider value={{ connected, subscribe: ctx.subscribe }}>{children}</Ctx.Provider>;
}

/** Subscribe to live events. The handler may change between renders (latest one is used). */
export function useLive(handler: Listener): { connected: boolean } {
  const ctx = useContext(Ctx);
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => ctx?.subscribe((e) => ref.current(e)), [ctx]);
  return { connected: ctx?.connected ?? false };
}
