import { useState } from "react";
import { downloadUrlFor } from "../api.js";
import DocumentPreviewModal from "./DocumentPreviewModal.jsx";

function prettyDocType(docType) {
  return docType
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join("_");
}

export default function DocumentCard({ result }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const filename = `${prettyDocType(result.doc_type)}.docx`;
  const href = downloadUrlFor(result.download_url);
  const hasNotes = result.assumptions.length > 0 || result.open_questions.length > 0;

  return (
    <div className="panel document-card">
      <div className="panel-head">
        <span>GENERATED DOCUMENT</span>
        <span className="tag tag--success">READY</span>
      </div>

      <div className="document-row">
        <span className="document-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <rect x="3" y="2" width="18" height="20" rx="2" fill="#2b579a" />
            <text x="12" y="15" textAnchor="middle" fontSize="8" fontWeight="700" fill="#fff" fontFamily="sans-serif">
              W
            </text>
          </svg>
        </span>
        <div>
          <p className="document-name">{filename}</p>
          <p className="document-sub">Ready to download · doc_id {result.document_id.slice(0, 8)}</p>
        </div>
      </div>

      <div className="document-actions">
        <a className="btn-primary btn-download" href={href}>
          <span aria-hidden="true">↓</span> DOWNLOAD DOCX
        </a>
        <button type="button" className="btn-secondary btn-preview" onClick={() => setPreviewOpen(true)}>
          PREVIEW DOCUMENT
        </button>
      </div>

      {previewOpen && <DocumentPreviewModal href={href} onClose={() => setPreviewOpen(false)} />}

      {hasNotes && (
        <details className="notes">
          <summary>ASSUMPTIONS &amp; OPEN QUESTIONS ({result.assumptions.length + result.open_questions.length})</summary>
          {result.assumptions.length > 0 && (
            <>
              <p className="notes-label">Assumptions</p>
              <ul>
                {result.assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {result.open_questions.length > 0 && (
            <>
              <p className="notes-label">Open questions</p>
              <ul>
                {result.open_questions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </details>
      )}
    </div>
  );
}
