import "./robot.css";
import HelperRobot from "./HelperRobot.jsx";

/**
 * status: "idle" | "loading" | "success" | "error"
 * Visual states only - never implies the document exists until the parent
 * has the real API response in hand.
 */
export default function RobotMascot({ status }) {
  const active = status === "loading";
  const success = status === "success";

  return (
    <div
      className={`robot-stage${active ? " is-active" : ""}${success ? " is-success" : ""}`}
      aria-hidden="true"
    >
      <HelperRobot position="left" visible={active} delay={0} />
      <HelperRobot position="right" visible={active} delay={120} />
      <HelperRobot position="top" visible={active} delay={220} />

      <svg className="robot-svg" viewBox="0 0 220 260" role="img">
        <defs>
          <linearGradient id="bodyMetal" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#d4ddff" />
            <stop offset="35%" stopColor="#8fa3e0" />
            <stop offset="72%" stopColor="#47568c" />
            <stop offset="100%" stopColor="#1f2746" />
          </linearGradient>
          <linearGradient id="darkMetal" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#5c6ba3" />
            <stop offset="100%" stopColor="#1a2038" />
          </linearGradient>
          <linearGradient id="helperMetal" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#b9c6ff" />
            <stop offset="100%" stopColor="#333f6b" />
          </linearGradient>
          <radialGradient id="pedestalGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(77,238,255,0.5)" />
            <stop offset="100%" stopColor="rgba(77,238,255,0)" />
          </radialGradient>
          <filter id="glow" x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur stdDeviation="3.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <ellipse cx="110" cy="236" rx="78" ry="16" fill="url(#pedestalGlow)" />
        <rect x="46" y="224" width="128" height="12" rx="5" fill="url(#darkMetal)" stroke="rgba(124,155,255,0.4)" />
        <rect x="64" y="228" width="8" height="4" className="robot-light" fill="#4deeff" filter="url(#glow)" />
        <rect x="148" y="228" width="8" height="4" className="robot-light" fill="#4deeff" filter="url(#glow)" />

        {/* legs */}
        <rect x="74" y="178" width="22" height="48" rx="5" fill="url(#darkMetal)" />
        <rect x="124" y="178" width="22" height="48" rx="5" fill="url(#darkMetal)" />

        {/* left arm */}
        <rect x="26" y="130" width="24" height="52" rx="9" fill="url(#bodyMetal)" />
        <rect x="22" y="176" width="30" height="16" rx="6" fill="url(#darkMetal)" />

        {/* right arm, bent upward holding a small tablet */}
        <g transform="rotate(-18 168 150)">
          <rect x="160" y="112" width="22" height="50" rx="9" fill="url(#bodyMetal)" />
        </g>
        <rect x="150" y="96" width="34" height="46" rx="6" fill="#0a0e1c" stroke="rgba(124,155,255,0.5)" strokeWidth="1.5" />
        <rect x="156" y="104" width="22" height="3.5" rx="1.5" className="robot-light" fill="#4deeff" />
        <rect x="156" y="112" width="22" height="3.5" rx="1.5" className="robot-light" fill="#5b7fff" />
        <rect x="156" y="120" width="16" height="3.5" rx="1.5" className="robot-light" fill="#5b7fff" />

        {/* torso */}
        <rect x="52" y="118" width="116" height="78" rx="18" fill="url(#bodyMetal)" />
        <rect x="52" y="118" width="116" height="10" rx="5" fill="rgba(255,255,255,0.18)" />
        <circle cx="110" cy="160" r="17" fill="#060915" stroke="rgba(124,155,255,0.5)" strokeWidth="2" />
        <circle cx="110" cy="160" r="9" className="robot-core" fill="#4deeff" filter="url(#glow)" />

        {/* head */}
        <rect x="62" y="48" width="96" height="76" rx="20" fill="url(#bodyMetal)" />
        <rect x="62" y="48" width="96" height="10" rx="5" fill="rgba(255,255,255,0.2)" />
        <rect x="100" y="24" width="4" height="24" fill="url(#darkMetal)" />
        <circle cx="102" cy="22" r="6" className="robot-antenna-tip" fill="#4deeff" filter="url(#glow)" />

        <rect x="76" y="66" width="68" height="40" rx="12" fill="#060915" stroke="rgba(124,155,255,0.4)" strokeWidth="1.5" />
        <rect x="88" y="80" width="18" height="18" rx="4" className="robot-eye robot-eye--left" fill="#4deeff" filter="url(#glow)" />
        <rect x="114" y="80" width="18" height="18" rx="4" className="robot-eye robot-eye--right" fill="#4deeff" filter="url(#glow)" />
      </svg>
    </div>
  );
}
