import { ServiceStatusIndicator, table } from "@/components/ui";
import { num } from "@/lib/format";
import type { ServiceSnapshot } from "@/types/api";

/** Before/after values exactly as stored by the backend. */
export function SnapshotCompare({ before, after }: { before: ServiceSnapshot; after: ServiceSnapshot }) {
  const tone = (s: ServiceSnapshot) => (s.status === "HEALTHY" ? "text-emerald-700" : s.status ? "text-red-700" : "");
  const rows = [
    { metric: "Service", before: <ServiceStatusIndicator status={before.status} />, after: <ServiceStatusIndicator status={after.status} /> },
    { metric: "Error rate", before: num(before.error_rate, "%"), after: num(after.error_rate, "%"), mono: true },
    { metric: "Latency", before: num(before.latency_ms, " ms"), after: num(after.latency_ms, " ms"), mono: true },
    { metric: "Deployment", before: before.active_version ?? "—", after: after.active_version ?? "—", mono: true, plain: true },
  ];
  return (
    <div className={table.wrap}>
      <table className={`${table.table} max-w-lg`}>
        <thead>
          <tr>
            <th scope="col" className={table.th}>
              <span className="sr-only">Metric</span>
            </th>
            <th scope="col" className={`${table.th} text-right`}>Before</th>
            <th scope="col" className={`${table.th} text-right`}>After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.metric}>
              <th scope="row" className={`${table.td} font-normal text-muted`}>{row.metric}</th>
              <td className={`${table.td} text-right ${row.mono ? table.mono : ""} ${row.mono && !row.plain ? tone(before) : ""}`}>
                {row.before}
              </td>
              <td className={`${table.td} text-right ${row.mono ? table.mono : ""} ${row.mono && !row.plain ? tone(after) : ""}`}>
                {row.after}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
