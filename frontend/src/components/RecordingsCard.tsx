import { useEffect, useState } from "react";
import { useRecordingsFolder, useSaveRecordingsFolder } from "../hooks/useSettings";
import { useScanRecordings } from "../hooks/useVideo";
import type { FilterScope } from "../lib/queryKeys";
import type { ScanResult } from "../lib/types";

// Calendar-page card: set the one folder all recordings live in, then "Scan"
// to auto-link every attempt whose DD-MON-YYYY-NN.mp4 is found there. Result is
// a plain list of the days newly linked (already-linked attempts are skipped).
export function RecordingsCard({ scope }: { scope: FilterScope }) {
  const { data } = useRecordingsFolder();
  const saveFolder = useSaveRecordingsFolder();
  const scan = useScanRecordings(scope);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Seed the input from the saved value once it loads.
  const saved = data?.folder ?? "";
  useEffect(() => setDraft(saved), [saved]);

  const persistIfChanged = async () => {
    if (draft.trim() !== saved) await saveFolder.mutateAsync(draft.trim());
  };

  const runScan = async () => {
    setError(null);
    setResult(null);
    try {
      await persistIfChanged(); // scan reads the server-side setting
      setResult(await scan.mutateAsync());
    } catch {
      // The scan 400s on an unset/invalid folder; surface that as the hint.
      setError("Scan failed — set a valid recordings folder (the folder must exist).");
    }
  };

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="section-title" style={{ marginTop: 0 }}>Recordings</div>
      <div className="section-cap" style={{ marginBottom: 6 }}>
        Folder holding your session recordings (Windows <code>C:\…</code> works under WSL).
        Files are matched as <code>DD-MON-YYYY-NN.mp4</code> — e.g. <code>13-JUN-2026-01.mp4</code>.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void persistIfChanged()}
          placeholder="C:\Users\you\Videos\Recordings"
          style={{ flex: 1, minWidth: 240 }}
        />
        <button
          type="button"
          className="btn-accent"
          onClick={() => void runScan()}
          disabled={scan.isPending || !draft.trim()}
        >
          {scan.isPending ? "Scanning…" : "Scan recordings"}
        </button>
      </div>

      {error && <div className="notice neg" style={{ marginTop: 8 }}>{error}</div>}

      {result && !error && (
        result.count === 0 ? (
          <div className="notice" style={{ marginTop: 8 }}>
            No new recordings linked — everything matched is already linked, or no
            filenames matched in that folder.
          </div>
        ) : (
          <div className="notice pos" style={{ marginTop: 8 }}>
            <div style={{ marginBottom: 4 }}>
              Linked {result.count} recording{result.count === 1 ? "" : "s"}:
            </div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {result.linked.map((l) => (
                <li key={l.source_file} style={{ fontVariantNumeric: "tabular-nums" }}>
                  {l.day} · Attempt {l.attempt_no} → <code>{l.filename}</code>
                </li>
              ))}
            </ul>
          </div>
        )
      )}
    </div>
  );
}
