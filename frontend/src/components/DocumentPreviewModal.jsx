import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { renderAsync } from "docx-preview";
import "./modal.css";

/** Renders the real generated .docx (fetched from the existing download
 * endpoint) client-side - no backend change, no fabricated content. */
export default function DocumentPreviewModal({ href, onClose }) {
  const containerRef = useRef(null);
  const panelRef = useRef(null);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setStatus("loading");
      setError(null);
      try {
        const response = await fetch(href);
        if (!response.ok) {
          throw new Error(`Could not fetch the document (status ${response.status}).`);
        }
        const buffer = await response.arrayBuffer();
        if (cancelled || !containerRef.current) return;

        containerRef.current.innerHTML = "";
        await renderAsync(buffer, containerRef.current, containerRef.current, {
          inWrapper: true,
          ignoreHeight: true,
        });
        if (!cancelled) setStatus("ready");
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Could not render the document preview.");
          setStatus("error");
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [href]);

  useEffect(() => {
    panelRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleKeyDown(event) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  function handleOverlayMouseDown(event) {
    if (event.target === event.currentTarget) onClose();
  }

  return createPortal(
    <div className="modal-overlay" onMouseDown={handleOverlayMouseDown}>
      <div
        className="modal-panel panel"
        role="dialog"
        aria-modal="true"
        aria-label="Document preview"
        ref={panelRef}
        tabIndex={-1}
      >
        <div className="panel-head modal-head">
          <span>DOCUMENT PREVIEW</span>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close preview">
            ✕
          </button>
        </div>

        <div className="modal-body">
          {status === "loading" && <p className="modal-status">Loading preview…</p>}
          {status === "error" && <p className="modal-status modal-status--error">{error}</p>}
          <div
            className="docx-preview-container"
            ref={containerRef}
            hidden={status !== "ready"}
          />
        </div>

        <div className="modal-footer">
          <a className="btn-primary btn-download" href={href}>
            <span aria-hidden="true">↓</span> DOWNLOAD WORD DOCUMENT
          </a>
        </div>
      </div>
    </div>,
    document.body,
  );
}
