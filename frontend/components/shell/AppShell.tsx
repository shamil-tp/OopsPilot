"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Dot, WindowDots, type Tone } from "@/components/ui";
import { OpsPilotLogo } from "@/components/shell/Logo";
import { PixelIcon, type PixelIconName } from "@/components/shell/PixelIcon";
import { useSystemHealth } from "@/hooks/useSystemHealth";
import { API_URL } from "@/lib/config";

const NAV: { href: string; label: string; short?: string; icon: PixelIconName; active: (path: string) => boolean }[] = [
  { href: "/", label: "Overview", icon: "server", active: (path: string) => path === "/" },
  { href: "/incidents", label: "Incidents", icon: "bug", active: (path: string) => path.startsWith("/incidents") },
  { href: "/code-reviews", label: "Code reviews", short: "Reviews", icon: "terminal", active: (path: string) => path.startsWith("/code-reviews") },
  { href: "/cicd", label: "CI/CD", short: "CI/CD", icon: "rocket", active: (path: string) => path.startsWith("/cicd") },
];

function NavLinks({ layout }: { layout: "sidebar" | "bar" }) {
  const path = usePathname();
  return (
    <>
      {NAV.map((item) => {
        const active = item.active(path);
        const base =
          layout === "sidebar"
            ? "flex items-center gap-2.5 border-l-2 px-3 py-1.5 text-sm"
            : "flex items-center gap-1.5 border-b-2 px-2.5 py-1 text-xs whitespace-nowrap";
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`${base} lowercase ${
              active
                ? "border-term bg-term/10 text-term glow"
                : "border-transparent text-muted hover:bg-hover hover:text-ink"
            }`}
          >
            {layout === "sidebar" && (
              <span aria-hidden className="w-3 text-term">
                {active ? "▶" : ""}
              </span>
            )}
            <PixelIcon name={item.icon} className={`h-4 w-4 shrink-0 ${layout === "bar" ? "hidden sm:block" : ""}`} />
            {layout === "bar" ? (item.short ?? item.label) : item.label}
          </Link>
        );
      })}
    </>
  );
}

/** Backend, database and AI provider state, polled every 10 s. */
function SystemIndicators() {
  const { health, error, loading } = useSystemHealth();
  const db = health?.database;
  const ai = health?.ai;

  const items: { name: string; tone: Tone; value: string }[] = [
    {
      name: "API",
      tone: loading ? "neutral" : error ? "critical" : "ok",
      value: loading ? "checking" : error ? "unreachable" : `v${health?.version}`,
    },
    {
      name: "Database",
      tone: !db ? "neutral" : db.status === "ok" ? "ok" : "critical",
      value: !db ? "—" : db.status === "ok" ? `${Math.round(db.latency_ms ?? 0)} ms` : "unavailable",
    },
    {
      name: "AI",
      tone: !ai ? "neutral" : ai.configured_keys > 0 ? "ok" : "warn",
      value: !ai ? "—" : `${ai.model} · ${ai.configured_keys} key${ai.configured_keys === 1 ? "" : "s"}`,
    },
  ];

  return (
    <ul aria-label="System status" className="flex items-center gap-2.5 text-xs sm:gap-4">
      {items.map((item) => (
        <li key={item.name} className="flex items-center gap-1.5" title={`${item.name}: ${item.value}`}>
          <Dot tone={item.tone} />
          <span aria-hidden className="hidden text-muted uppercase sm:inline">
            {item.name}
          </span>
          <span aria-hidden className="hidden font-mono text-term xl:inline">
            {item.value}
          </span>
          <span className="sr-only">{`${item.name}: ${item.value}`}</span>
        </li>
      ))}
    </ul>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-10 focus:bg-panel focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-line-strong bg-panel/95 backdrop-blur print:hidden">
        <div className="flex h-14 items-center gap-3 px-4 sm:gap-4 sm:px-6">
          <WindowDots />
          <Link href="/" className="flex items-center gap-2 font-pixel text-2xl leading-none text-term uppercase glow">
            <OpsPilotLogo className="h-7 w-7 shrink-0" />
            <span className="hidden sm:inline">OpsPilot</span>
            <span aria-hidden className="hidden font-mono text-xs text-muted normal-case md:inline">— zsh — prod</span>
          </Link>
          <nav aria-label="Primary" className="flex min-w-0 items-center gap-0.5 overflow-x-auto sm:gap-1 lg:hidden">
            <NavLinks layout="bar" />
          </nav>
          <div className="ml-auto shrink-0">
            <SystemIndicators />
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-56 shrink-0 flex-col border-r border-line-strong bg-panel lg:flex print:hidden">
          <p aria-hidden className="px-4 pt-4 pb-1 text-xs text-muted">
            <span className="text-term">❯</span> ls ~/opspilot
          </p>
          <nav aria-label="Primary" className="flex flex-col gap-0.5 py-1">
            <NavLinks layout="sidebar" />
          </nav>
          <div className="mt-auto space-y-2 border-t border-line-strong bg-canvas/60 p-4 text-xs text-muted">
            <div className="flex items-center gap-1.5 font-pixel text-xl leading-none text-term uppercase">
              <OpsPilotLogo className="h-5 w-5 shrink-0" />
              <span>OpsPilot</span>
            </div>
            <p># AI-Powered Autonomous Incident Response &amp; DevOps Copilot</p>
            <a href={`${API_URL}/docs`} target="_blank" rel="noreferrer" className="inline-block text-term hover:underline">
              $ open api-docs ↗
            </a>
          </div>
        </aside>

        <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8 print:p-0">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
