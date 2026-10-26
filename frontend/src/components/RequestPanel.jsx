const MAX_LENGTH = 2000;

const SAMPLE_PROMPTS = [
  { icon: "doc", label: "Project proposal for a campus hackathon" },
  { icon: "doc", label: "Meeting minutes from a team stand-up" },
  { icon: "chart", label: "Product launch plan with timeline and risks" },
];

function SampleIcon({ kind }) {
  if (kind === "chart") {
    return (
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1" y="9" width="3" height="6" fill="currentColor" />
        <rect x="6.5" y="5" width="3" height="10" fill="currentColor" />
        <rect x="12" y="2" width="3" height="13" fill="currentColor" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="1" width="12" height="14" rx="1" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="4.5" y="4.5" width="7" height="1.4" fill="currentColor" />
      <rect x="4.5" y="7.5" width="7" height="1.4" fill="currentColor" />
      <rect x="4.5" y="10.5" width="4.5" height="1.4" fill="currentColor" />
    </svg>
  );
}

export default function RequestPanel({
  value,
  onChange,
  onSubmit,
  busy,
  busyLabel = "INITIALIZING…",
  onClear,
  canClear,
}) {
  const trimmedLength = value.trim().length;
  const canSubmit = trimmedLength > 0 && value.length <= MAX_LENGTH && !busy;

  function handleSubmit(event) {
    event.preventDefault();
    if (canSubmit) onSubmit(value.trim());
  }

  return (
    <form className="panel request-panel" onSubmit={handleSubmit}>
      <div className="panel-head">
        <span>YOUR REQUEST</span>
        <span className="tag">TXT</span>
      </div>

      <div className="textarea-wrap">
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value.slice(0, MAX_LENGTH))}
          placeholder="What document do you need?"
          maxLength={MAX_LENGTH}
          rows={4}
          aria-label="Describe the document you need"
          disabled={busy}
        />
        <span className="char-count">
          {value.length}/{MAX_LENGTH}
        </span>
      </div>

      <div className="request-actions">
        <button type="submit" className="btn-primary" disabled={!canSubmit}>
          {busy ? busyLabel : "INITIALIZE AGENT"}
          {!busy && <span aria-hidden="true"> →</span>}
        </button>
        <button
          type="button"
          className="btn-secondary btn-clear"
          onClick={onClear}
          disabled={busy || !canClear}
        >
          CLEAR
        </button>
      </div>

      <div id="examples">
        <p className="eyebrow sample-label">· TRY A SAMPLE REQUEST</p>
        <ul className="sample-list">
          {SAMPLE_PROMPTS.map((sample) => (
            <li key={sample.label}>
              <button
                type="button"
                className="sample-row"
                onClick={() => onChange(sample.label)}
                disabled={busy}
              >
                <span className="sample-icon">
                  <SampleIcon kind={sample.icon} />
                </span>
                <span>{sample.label}</span>
                <span className="sample-arrow" aria-hidden="true">
                  →
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </form>
  );
}
