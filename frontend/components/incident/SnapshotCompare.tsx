import { ServiceStatusBadge } from "@/components/ui";
import { num } from "@/lib/format";
import type { ServiceSnapshot } from "@/types/api";

function Column({ title, snapshot, good }: { title: string; snapshot: ServiceSnapshot; good: boolean }) {
  const color = good ? "text-emerald-300" : "text-rose-300";
  return (
    <div className="flex-1 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
      <p className="mb-2 text-[11px] font-semibold tracking-widest text-slate-500 uppercase">{title}</p>
      <dl className="space-y-1.5 text-sm">
        <div className="flex justify-between gap-2">
          <dt className="text-slate-400">Status</dt>
          <dd>
            <ServiceStatusBadge status={snapshot.status} />
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-slate-400">Error rate</dt>
          <dd className={`font-mono ${color}`}>{num(snapshot.error_rate, "%")}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-slate-400">Latency</dt>
          <dd className={`font-mono ${color}`}>{num(snapshot.latency_ms, " ms")}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-slate-400">Active version</dt>
          <dd className="font-mono text-slate-200">{snapshot.active_version ?? "—"}</dd>
        </div>
      </dl>
    </div>
  );
}

/** Before/after values exactly as stored by the backend. */
export function SnapshotCompare({ before, after }: { before: ServiceSnapshot; after: ServiceSnapshot }) {
  const recovered = after.status === "HEALTHY";
  return (
    <div className="flex items-stretch gap-2">
      <Column title="Before" snapshot={before} good={before.status === "HEALTHY"} />
      <div className="flex items-center text-slate-500">→</div>
      <Column title="After" snapshot={after} good={recovered} />
    </div>
  );
}
