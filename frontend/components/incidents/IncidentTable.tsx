import Link from "next/link";

import { Empty, SeverityLabel, StatusIndicator, table } from "@/components/ui";
import { shortDateTime } from "@/lib/format";
import type { Incident } from "@/types/api";

export function IncidentTable({ incidents, empty }: { incidents: Incident[]; empty: string }) {
  if (incidents.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className={table.wrap}>
      <table className={table.table}>
        <thead>
          <tr>
            <th scope="col" className={table.th}>Incident</th>
            <th scope="col" className={`${table.th} hidden md:table-cell`}>Title</th>
            <th scope="col" className={`${table.th} hidden sm:table-cell`}>Service</th>
            <th scope="col" className={table.th}>Severity</th>
            <th scope="col" className={table.th}>Status</th>
            <th scope="col" className={`${table.th} text-right`}>Started (UTC)</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((incident) => (
            <tr key={incident.id} className="hover:bg-hover">
              <td className={table.td}>
                <Link href={`/incidents/${incident.id}`} className="font-mono text-[13px] font-medium whitespace-nowrap text-ink underline-offset-2 hover:underline">
                  {incident.reference}
                </Link>
              </td>
              <td className={`${table.td} hidden max-w-xs truncate md:table-cell`}>{incident.title}</td>
              <td className={`${table.td} ${table.mono} hidden sm:table-cell`}>{incident.service_name}</td>
              <td className={table.td}>
                <SeverityLabel severity={incident.severity} />
              </td>
              <td className={table.td}>
                <StatusIndicator status={incident.status} />
              </td>
              <td className={`${table.td} text-right whitespace-nowrap text-muted`}>{shortDateTime(incident.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
