"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Dot, type Tone } from "@/components/ui";
import { OpsPilotLogo } from "@/components/shell/Logo";
import { useSystemHealth } from "@/hooks/useSystemHealth";
import { API_URL } from "@/lib/config";

const NAV = [
  { href: "/", label: "Overview", active: (path: string) => path === "/" },
  { href: "/incidents", label: "Incidents", active: (path: string) => path.startsWith("/incidents") },
  { href: "/code-reviews", label: "Code reviews", short: "Reviews", active: (path: string) => path.startsWith("/code-reviews") },
];

function NavLinks({ layout }: { layout: "sidebar" | "bar" }) {
  const path = usePathname();
  return (
    <>
      {NAV.map((item) => {
        const active = item.active(path);
        const base =
          layout === "sidebar"
            ? "block rounded-md px-2.5 py-1.5 text-sm"
            : "rounded-md px-2 py-1 text-sm whitespace-nowrap";
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={`${base} ${active ? "bg-hover font-medium text-ink" : "text-muted hover:bg-hover hover:text-ink"}`}
          >
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
          <span aria-hidden className="hidden text-muted sm:inline">
            {item.name}
          </span>
          <span aria-hidden className="hidden font-mono text-ink xl:inline">
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
      <header className="sticky top-0 z-10 border-b border-line bg-panel print:hidden">
        <div className="flex h-12 items-center gap-3 px-4 sm:gap-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-ink">
            <OpsPilotLogo className="h-5 w-5 shrink-0" />
            <span>OpsPilot</span>
          </Link>
          <nav aria-label="Primary" className="flex min-w-0 items-center gap-0.5 sm:gap-1 lg:hidden">
            <NavLinks layout="bar" />
          </nav>
          <div className="ml-auto">
            <SystemIndicators />
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-52 shrink-0 flex-col border-r border-line bg-panel lg:flex print:hidden">
          <nav aria-label="Primary" className="flex flex-col gap-0.5 p-3">
            <NavLinks layout="sidebar" />
          </nav>
          <div className="mt-auto space-y-2 border-t border-line p-4 text-xs text-muted">
            <div className="flex items-center gap-1.5 font-medium text-ink">
              <OpsPilotLogo className="h-4 w-4 shrink-0" />
              <span>OpsPilot</span>
            </div>
            <p>AI-Powered Autonomous Incident Response &amp; DevOps Copilot</p>
            <a href={`${API_URL}/docs`} target="_blank" rel="noreferrer" className="inline-block hover:text-ink">
              API reference ↗
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
