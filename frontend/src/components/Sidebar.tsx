import { useFilters } from "../hooks/useFilters";
import { useMeta } from "../hooks/useMeta";

// Data controls: import/upload (Phase 6), display-tz selector, connection status.
export function Sidebar() {
  const { data: meta } = useMeta();
  const { scope, setTz } = useFilters();
  const tz = scope.tz || meta?.default_tz || "";

  return (
    <aside className="sidebar">
      <div className="app-header">
        <div className="logo">A</div>
        <div>
          <div className="title">ATAS Journal</div>
          <div className="subtitle">NQ futures review</div>
        </div>
      </div>

      <h3>Display timezone</h3>
      <select value={tz} onChange={(e) => setTz(e.target.value)} style={{ width: "100%" }}>
        {(meta?.display_tzs ?? []).map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <h3 style={{ marginTop: 24 }}>Status</h3>
      <div className="muted" style={{ fontSize: 12, lineHeight: 1.8 }}>
        <div>Databento: {meta?.databento_available ? "connected" : "disabled"}</div>
        <div>AI: {meta?.ai_available ? "ready" : "disabled"}</div>
      </div>
    </aside>
  );
}
