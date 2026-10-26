export default function ErrorPanel({ onRetry }) {
  return (
    <div className="panel error-panel" role="alert">
      <div className="panel-head">
        <span>AGENT ERROR</span>
        <span className="tag tag--error">FAILED</span>
      </div>
      <p className="error-title">Couldn&rsquo;t process your request</p>
      <p className="error-message">We couldn&rsquo;t process your request. Please try again.</p>
      <button type="button" className="btn-secondary" onClick={onRetry}>
        TRY AGAIN
      </button>
    </div>
  );
}
