"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Dot, type Tone } from "@/components/ui";
import { useSystemHealth } from "@/hooks/useSystemHealth";
import { API_URL } from "@/lib/config";

const NAV = [
  { href: "/", label: "Overview", active: (path: string) => path === "/" },
  { href: "/incidents", label: "Incidents", active: (path: string) => path.startsWith("/incidents") },
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
            {item.label}
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
      <header className="sticky top-0 z-10 border-b border-line bg-panel">
        <div className="flex h-12 items-center gap-4 px-4 sm:px-6">
          <Link href="/" className="text-[15px] font-semibold tracking-tight text-ink">
            OpsPilot
          </Link>
          <nav aria-label="Primary" className="flex items-center gap-1 lg:hidden">
            <NavLinks layout="bar" />
          </nav>
          <div className="ml-auto">
            <SystemIndicators />
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="hidden w-52 shrink-0 flex-col border-r border-line bg-panel lg:flex">
          <nav aria-label="Primary" className="flex flex-col gap-0.5 p-3">
            <NavLinks layout="sidebar" />
          </nav>
          <div className="mt-auto space-y-2 border-t border-line p-4 text-xs text-muted">
            <p>AI-Powered Autonomous Incident Response &amp; DevOps Copilot</p>
            <a href={`${API_URL}/docs`} target="_blank" rel="noreferrer" className="inline-block hover:text-ink">
              API reference ↗
            </a>
          </div>
        </aside>

        <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
