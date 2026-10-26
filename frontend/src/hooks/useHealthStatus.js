import { useEffect, useState } from "react";
import { checkHealth } from "../api.js";

const POLL_MS = 30000;

/** Real GET /health polling - never claims "online" without a live check. */
export function useHealthStatus() {
  const [online, setOnline] = useState(null); // null = checking

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function poll() {
      const ok = await checkHealth({ signal: controller.signal });
      if (!cancelled) setOnline(ok);
    }

    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, []);

  return online;
}
