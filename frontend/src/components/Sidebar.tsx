import { useRef, useState } from "react";
import { useFilters } from "../hooks/useFilters";
import { useMeta } from "../hooks/useMeta";
import { useDeleteAll, useImportDir, useUpload } from "../hooks/useImport";

const SOURCE_TZ_OPTIONS: { label: string; value: string }[] = [
  { label: "New York", value: "America/New_York" },
  { label: "Kuala Lumpur", value: "Asia/Kuala_Lumpur" },
];

// Data controls: import/upload, source-tz selector, display-tz selector, status.
export function Sidebar() {
  const { data: meta } = useMeta();
  const { scope, setTz } = useFilters();
  const tz = scope.tz || meta?.default_tz || "";
  const importDir = useImportDir();
  const upload = useUpload();
  const deleteAll = useDeleteAll();
  const fileRef = useRef<HTMLInputElement>(null);
  const [sourceTz, setSourceTz] = useState(SOURCE_TZ_OPTIONS[0].value);

  const onDeleteAll = () => {
    const typed = window.prompt(
      "This wipes every imported trade, execution, and statistics row, plus the " +
        "imported-files log. Notes and AI analyses are kept (they reattach on " +
        "identical re-import).\n\nType DELETE to confirm.",
    );
    if (typed !== "DELETE") return;
    deleteAll.mutate();
  };

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
      <div className="field" style={{ marginBottom: 8 }}>
        <label>Source timezone (in file)</label>
        <select
          value={sourceTz}
          onChange={(e) => setSourceTz(e.target.value)}
          style={{ width: "100%" }}
        >
          {SOURCE_TZ_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <button
        style={{ width: "100%", marginBottom: 8 }}
        disabled={importDir.isPending}
        onClick={() => importDir.mutate({ sourceTz })}
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
        onChange={(e) =>
          e.target.files &&
          e.target.files.length > 0 &&
          upload.mutate({ files: e.target.files, sourceTz })
        }
      />
      <button
        style={{ width: "100%", marginBottom: 4 }}
        disabled={upload.isPending}
        onClick={() => fileRef.current?.click()}
      >
        {upload.isPending ? "Uploading…" : "Upload ATAS .xlsx"}
      </button>
      <button
        className="btn-danger"
        style={{ width: "100%", marginTop: 8 }}
        disabled={deleteAll.isPending}
        onClick={onDeleteAll}
        title="Wipe every imported trade so the project can be re-imported from scratch."
      >
        {deleteAll.isPending ? "Deleting…" : "Delete all trades"}
      </button>
      {deleteAll.data && (
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          Removed {deleteAll.data.atas_journal} trades, {deleteAll.data.executions}{" "}
          fills, {deleteAll.data.atas_statistics} stats rows.
        </div>
      )}

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
