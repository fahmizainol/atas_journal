import { useFilters } from "../hooks/useFilters";
import { useEdges } from "../hooks/useEdges";
import { EdgeTable } from "../components/EdgeTable";
import { useMeta } from "../hooks/useMeta";

export function Edges() {
  const { scope } = useFilters();
  const { data: meta } = useMeta();
  const { data, isLoading } = useEdges(scope);
  const tzLabel = scope.tz || meta?.default_tz || "local";

  if (isLoading) return <div className="notice">Loading…</div>;
  if (!data) return <div className="notice">No trades to display.</div>;

  return (
    <div>
      <div className="section-title">Behavioral edges</div>
      <div className="grid-2">
        <div>
          <EdgeTable title="By weekday" data={data.by_weekday} />
          <EdgeTable title="By hold time" data={data.by_hold_time} />
          <EdgeTable title="Long vs Short" data={data.by_direction} />
        </div>
        <div>
          <EdgeTable title={`By hour (${tzLabel})`} data={data.by_hour_kl} />
          <EdgeTable title="By session block (US Eastern)" data={data.by_hour_et} />
        </div>
      </div>
    </div>
  );
}
