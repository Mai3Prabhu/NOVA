import { useCallback, useRef, useState } from "react";
import { runAgent } from "../api.js";

// Shown for any failure (network, validation, planning, execution, ...).
// The real cause is never rendered - it's still visible in the browser's
// network tab and the backend's own logs/response body for debugging, it's
// just not surfaced in this UI.
const FRIENDLY_ERROR_MESSAGE = "We couldn't process your request. Please try again.";

/**
 * Drives document generation.
 *
 * `result` always holds the most recently *successful* response - it is
 * never cleared just because a new request started or failed, so a working
 * document (and its preview/download) stays available while a generate
 * call is in flight, or if that call fails.
 */
export function useDocumentWorkflow() {
  const [result, setResult] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const controllerRef = useRef(null);
  const lastRequestRef = useRef(null); // for "Try Again"

  const generate = useCallback(async (requestText) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    lastRequestRef.current = requestText;
    setIsSubmitting(true);
    setError(null);

    try {
      const data = await runAgent(requestText, { signal: controller.signal });
      setResult(data);
      return true;
    } catch (err) {
      if (err.name === "AbortError") return false;
      console.error("Agent request failed:", err); // dev-console only, not rendered
      setError(FRIENDLY_ERROR_MESSAGE);
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  const retry = useCallback(() => {
    if (!lastRequestRef.current) return Promise.resolve(false);
    return generate(lastRequestRef.current);
  }, [generate]);

  const clear = useCallback(() => {
    controllerRef.current?.abort();
    lastRequestRef.current = null;
    setResult(null);
    setIsSubmitting(false);
    setError(null);
  }, []);

  return {
    result,
    latestDocumentId: result?.document_id ?? null,
    isSubmitting,
    error,
    generate,
    retry,
    clear,
  };
}
