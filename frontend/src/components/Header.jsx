import { useHealthStatus } from "../hooks/useHealthStatus.js";

export default function Header() {
  const online = useHealthStatus();

  return (
    <header className="site-header">
      <a href="#home" className="brand">
        <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
          <rect width="32" height="32" rx="6" fill="#070a16" />
          <rect x="4" y="4" width="24" height="24" rx="3" fill="none" stroke="#5b7fff" strokeWidth="2" />
          <rect x="10" y="10" width="5" height="5" fill="#4deeff" />
          <rect x="17" y="10" width="5" height="5" fill="#5b7fff" />
          <rect x="10" y="17" width="5" height="5" fill="#5b7fff" />
          <rect x="17" y="17" width="5" height="5" fill="#4deeff" />
        </svg>
        <span className="brand-name">NOVA_001</span>
        <span className="brand-sub">/ AI DOCUMENT AGENT</span>
      </a>

      <div
        className={`status-pill status-pill--${online === null ? "checking" : online ? "online" : "offline"}`}
        role="status"
      >
        <span className="status-dot" aria-hidden="true" />
        {online === null ? "CHECKING" : online ? "SYSTEM ONLINE" : "SYSTEM OFFLINE"}
      </div>
    </header>
  );
}
