import { useModels } from "../hooks/useModels";
import { usePatchSession, useSessions } from "../hooks/useSessions";
import type { SessionMode } from "../lib/types";

const MODES: { value: SessionMode; label: string; title: string }[] = [
  { value: "live", label: "Live", title: "Prop firm / real money." },
  { value: "replay", label: "Replay", title: "A simulated re-run of a past session." },
  {
    value: "backtest",
    label: "Backtest",
    title: "One model exercised exclusively — it binds every trade in this session.",
  },
];

// Per-attempt session controls: what this export *is* (live money, a replay, or
// a single-model backtest) and whether it counts toward the default statistics.
// Keyed by source_file, the same key the video link and bookmarks already use.
export function SessionControl({ sourceFile }: { sourceFile: string }) {
  const { data: sessions = [] } = useSessions();
  const { data: models = [] } = useModels();
  const patch = usePatchSession();
  const session = sessions.find((s) => s.source_file === sourceFile);

  if (!session) return null;

  const setMode = (mode: SessionMode) => {
    // A backtest binds a model session-wide, so it can't be chosen without one.
    const model_id =
      mode === "backtest" ? session.model_id ?? models[0]?.id ?? null : undefined;
    if (mode === "backtest" && model_id == null) {
      window.alert("Create a model first — a backtest session has to bind one.");
      return;
    }
    patch.mutate({ sourceFile, patch: { mode, model_id } });
  };

  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Session type</label>
          <div className="radio-group">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                className={session.mode === m.value ? "active" : ""}
                onClick={() => setMode(m.value)}
                disabled={patch.isPending}
                title={m.title}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {session.mode === "backtest" && (
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Model under test</label>
            <select
              value={session.model_id == null ? "" : String(session.model_id)}
              disabled={patch.isPending}
              onChange={(e) =>
                patch.mutate({
                  sourceFile,
                  patch: { mode: "backtest", model_id: Number(e.target.value) },
                })
              }
            >
              {models.map((m) => (
                <option key={m.id} value={String(m.id)}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="field" style={{ marginBottom: 0 }}>
          <label>Archive</label>
          <button
            type="button"
            className={session.archived ? "active" : ""}
            disabled={patch.isPending}
            onClick={() => patch.mutate({ sourceFile, patch: { archived: !session.archived } })}
            title="Archived sessions stay browsable but leave the default statistics. Nothing is deleted."
          >
            {session.archived ? "Archived" : "Active"}
          </button>
        </div>

        {session.account && (
          <div className="section-cap">
            Account <code>{session.account}</code>
          </div>
        )}
      </div>
      {session.archived && (
        <div className="section-cap" style={{ marginTop: 6 }}>
          This attempt is archived — turn on the Archive filter above to see it in
          the aggregates.
        </div>
      )}
    </div>
  );
}
