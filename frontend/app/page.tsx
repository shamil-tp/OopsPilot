import { DashboardConsole } from "@/components/dashboard/DashboardConsole";
import { SystemStatusCard } from "@/components/SystemStatusCard";

export default function DashboardPage() {
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-1">
        <p className="font-mono text-xs tracking-widest text-cyan-400 uppercase">Incident Response</p>
        <h1 className="text-3xl font-semibold tracking-tight text-slate-50 sm:text-4xl">OpsPilot</h1>
        <p className="text-slate-400">AI-Powered Autonomous Incident Response &amp; DevOps Copilot</p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <DashboardConsole />
        <div className="flex flex-col gap-6">
          <SystemStatusCard />
        </div>
      </div>
    </main>
  );
}
