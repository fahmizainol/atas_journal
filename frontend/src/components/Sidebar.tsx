import { useRef } from "react";
import { useFilters } from "../hooks/useFilters";
import { useMeta } from "../hooks/useMeta";
import { useImportDir, useUpload } from "../hooks/useImport";

// Data controls: import/upload, display-tz selector, connection status.
export function Sidebar() {
  const { data: meta } = useMeta();
  const { scope, setTz } = useFilters();
  const tz = scope.tz || meta?.default_tz || "";
  const importDir = useImportDir();
  const upload = useUpload();
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <aside className="sidebar">
      <div className="app-header">
        <div className="logo">A</div>
        <div>
          <div className="title">ATAS Journal</div>
          <div className="subtitle">NQ futures review</div>
        </div>
      </div>

      <h3>Data</h3>
      <button
        style={{ width: "100%", marginBottom: 8 }}
        disabled={importDir.isPending}
        onClick={() => importDir.mutate()}
      >
        {importDir.isPending ? "Importing…" : "Import from data/imports/"}
      </button>
      {importDir.data && (
        <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
          Imported {importDir.data.files} file(s); {importDir.data.total_fills} new fills.
        </div>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx"
        multiple
        style={{ display: "none" }}
        onChange={(e) => e.target.files && e.target.files.length > 0 && upload.mutate(e.target.files)}
      />
      <button
        style={{ width: "100%", marginBottom: 4 }}
        disabled={upload.isPending}
        onClick={() => fileRef.current?.click()}
      >
        {upload.isPending ? "Uploading…" : "Upload ATAS .xlsx"}
      </button>

      <h3 style={{ marginTop: 24 }}>Display timezone</h3>
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
