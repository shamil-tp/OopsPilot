import { ServiceStatusIndicator, table } from "@/components/ui";
import { num } from "@/lib/format";
import type { ServiceSnapshot } from "@/types/api";

/**
 * Before/after values exactly as stored by the backend. `operator`: a real application, where the
 * error figure is the share of failed health checks (a rolling window of 10) and a deployment switch
 * done by the operator in the hosting platform is not observable.
 */
export function SnapshotCompare({
  before,
  after,
  operator = false,
}: {
  before: ServiceSnapshot;
  after: ServiceSnapshot;
  operator?: boolean;
}) {
  const tone = (s: ServiceSnapshot) => (s.status === "HEALTHY" ? "text-emerald-700" : s.status ? "text-red-700" : "");
  const unchanged = operator && after.active_version === before.active_version;
  const rows = [
    { metric: "Service", before: <ServiceStatusIndicator status={before.status} />, after: <ServiceStatusIndicator status={after.status} /> },
    operator
      ? { metric: "Failed checks (last 10)", before: num(before.error_rate, "%"), after: num(after.error_rate, "%"), mono: true, plain: true }
      : { metric: "Error rate", before: num(before.error_rate, "%"), after: num(after.error_rate, "%"), mono: true },
    { metric: "Latency", before: num(before.latency_ms, " ms"), after: num(after.latency_ms, " ms"), mono: true },
    {
      metric: "Deployment",
      before: before.active_version ?? "—",
      after: unchanged ? <span className="font-sans text-xs text-muted">not observable</span> : (after.active_version ?? "—"),
      mono: true,
      plain: true,
    },
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
