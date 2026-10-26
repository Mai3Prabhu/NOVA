export default function HelperRobot({ position, visible, delay = 0 }) {
  return (
    <svg
      className={`helper-robot helper-robot--${position}${visible ? " is-visible" : ""}`}
      style={{ transitionDelay: visible ? `${delay}ms` : "0ms" }}
      viewBox="0 0 60 72"
      aria-hidden="true"
    >
      <rect x="14" y="30" width="32" height="26" rx="6" fill="url(#helperMetal)" />
      <rect x="18" y="8" width="24" height="22" rx="7" fill="url(#helperMetal)" />
      <rect x="23" y="15" width="6" height="6" rx="1.5" className="helper-eye" />
      <rect x="31" y="15" width="6" height="6" rx="1.5" className="helper-eye" />
      <circle cx="30" cy="43" r="4" className="helper-core" />
      <rect x="6" y="34" width="8" height="16" rx="3" fill="url(#helperMetal)" />
      <rect x="46" y="34" width="8" height="16" rx="3" fill="url(#helperMetal)" />
      <rect x="18" y="56" width="24" height="7" rx="2" fill="url(#helperMetal)" />
    </svg>
  );
}
