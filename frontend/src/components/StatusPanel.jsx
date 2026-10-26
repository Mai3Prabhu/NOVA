const TOOL_LABELS = {
  gather_context: "Gather context",
  write_section: "Write section",
  make_table: "Build table",
  build_docx: "Generate Word document",
};

const STAGES = [
  { key: "plan", label: "Planning your request", message: "Breaking your request into actionable tasks..." },
  { key: "context", label: "Gathering context", message: "Reviewing the available context..." },
  { key: "write", label: "Writing document content", message: "Drafting document sections and tables..." },
  { key: "build", label: "Generating Word document", message: "Formatting your Word document..." },
];

/**
 * Two honest modes:
 * - "stages": the backend call is synchronous, so there's no real per-task
 *   progress yet. These are informational estimates of what the agent is
 *   likely doing, not verified events - only the active stage is
 *   highlighted (with a spinner); earlier stages show as "elapsed" (never
 *   a checkmark, which is reserved for a real confirmed result), later
 *   ones as pending. The timer never reaches a "completed" state on its
 *   own; that only happens once the real API response arrives.
 * - "log" (execution_log from the real API response): shows exactly what
 *   the backend reports, success or failed, per real task.
 */
export default function StatusPanel({ mode, activeStageIndex = 0, log = [] }) {
  const isStages = mode === "stages";
  const stage = isStages ? STAGES[Math.min(activeStageIndex, STAGES.length - 1)] : null;
  const allSucceeded = !isStages && log.length > 0 && log.every((entry) => entry.status === "success");

  return (
    <div className="panel status-panel" aria-live="polite">
      <div className="panel-head">
        <span>AGENT TODO LIST</span>
        <span className="tag">{isStages ? "WORKING" : allSucceeded ? "DONE" : "LOG"}</span>
      </div>

      {isStages && <p className="status-message">{stage.message}</p>}
      {allSucceeded && <p className="status-message status-message--done">Your document is ready!</p>}

      <ul className="status-list">
        {isStages
          ? STAGES.map((s, index) => {
              const state = index < activeStageIndex ? "elapsed" : index === activeStageIndex ? "active" : "pending";
              return (
                <li key={s.key} className={`status-item status-item--${state}`}>
                  <span className={`status-icon status-icon--${state}`} aria-hidden="true">
                    {state === "active" && <span className="status-spinner" />}
                  </span>
                  <span>{s.label}</span>
                </li>
              );
            })
          : log.map((entry) => (
              <li
                key={entry.task_id}
                className={`status-item status-item--${entry.status}`}
                title={entry.summary}
              >
                <span className={`status-icon status-icon--${entry.status}`} aria-hidden="true">
                  {entry.status === "success" ? "✓" : "✕"}
                </span>
                <span>{TOOL_LABELS[entry.tool_name] ?? entry.tool_name}</span>
              </li>
            ))}
      </ul>
    </div>
  );
}
