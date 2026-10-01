import type { SVGProps } from "react";

/**
 * OpsPilot Logo: Autonomous Copilot Delta Wing with AI Beacon & Telemetry Orbit.
 */
export function OpsPilotLogo({ className = "h-6 w-6", ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <defs>
        <linearGradient id="op-bg" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#1e1e24" />
          <stop offset="100%" stopColor="#09090b" />
        </linearGradient>
        <linearGradient id="op-wing-left" x1="8" y1="8" x2="22" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#2563eb" />
        </linearGradient>
        <linearGradient id="op-wing-right" x1="15" y1="8" x2="26" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#60a5fa" />
          <stop offset="100%" stopColor="#4f46e5" />
        </linearGradient>
      </defs>

      {/* Modern dark badge with rounded squircle */}
      <rect width="32" height="32" rx="7.5" fill="url(#op-bg)" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7" stroke="#27272a" strokeWidth="1" />

      {/* Telemetry Radar Arc */}
      <path
        d="M6 18 A 11 11 0 0 1 21 6.5"
        stroke="#38bdf8"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeDasharray="1.5 3"
        opacity="0.4"
      />

      {/* Supersonic Delta Wing / Autonomous Jet */}
      {/* Left wing facet */}
      <path d="M23.5 7.5 L7.5 14 L14 17.5 Z" fill="url(#op-wing-left)" />
      {/* Right wing facet */}
      <path d="M23.5 7.5 L14 17.5 L17.5 24.5 Z" fill="url(#op-wing-right)" />
      {/* Keel fold line */}
      <path d="M23.5 7.5 L14 17.5 L13 16 Z" fill="#1d4ed8" opacity="0.4" />

      {/* AI Autonomous Beacon */}
      <circle cx="23.5" cy="7.5" r="2.25" fill="#10b981" />
      <circle cx="23.5" cy="7.5" r="3.25" stroke="#34d399" strokeWidth="0.75" opacity="0.7" />
    </svg>
  );
}
