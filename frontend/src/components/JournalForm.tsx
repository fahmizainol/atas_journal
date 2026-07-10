import { useEffect, useState } from "react";
import { useSaveNote } from "../hooks/useTrades";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";
import { useModels } from "../hooks/useModels";
import { BadgeInput, BadgeList } from "./BadgeInput";

const OFF_MODEL = "";

// First WRITE path: a trade's note, its model, and which of that model's entry
// rules it met.
//
// `tradeKey` must be the row's `logical_trade_key`, not `trade_key` — journaling
// binds to the logical trade so it survives a logical <-> ATAS view switch.
//
// Setups and confluences used to be free-text badge fields, which is how the
// taxonomy grew to 45 confluences with duplicate pairs. A trade now carries
// exactly one model (or none) plus a fixed checklist. The archived era's badges
// still render, read-only, so old trades stay legible. Tags stay free-text —
// they were never taxonomy-registered.
export function JournalForm({
  tradeKey,
  initialNote,
  initialTags,
  initialSetups,
  initialConfluences,
  initialModelId,
  initialRulesMet,
}: {
  tradeKey: string;
  initialNote: string;
  initialTags: string[];
  initialSetups: string[];
  initialConfluences: string[];
  initialModelId: number | null;
  initialRulesMet: number[];
}) {
  const [note, setNote] = useState(initialNote);
  const [tags, setTags] = useState<string[]>(initialTags);
  const [modelId, setModelId] = useState<number | null>(initialModelId);
  const [rulesMet, setRulesMet] = useState<number[]>(initialRulesMet);
  const save = useSaveNote(tradeKey);
  const { scope } = useFilters();
  const { data: opts } = useFiltersData(scope);
  const { data: models = [] } = useModels();

  // Reset the form when switching to a different trade.
  useEffect(() => {
    setNote(initialNote);
    setTags(initialTags);
    setModelId(initialModelId);
    setRulesMet(initialRulesMet);
  }, [tradeKey, initialNote, initialTags, initialModelId, initialRulesMet]);

  const model = models.find((m) => m.id === modelId) ?? null;
  const rules = model?.rules ?? [];
  const legacy = initialSetups.length > 0 || initialConfluences.length > 0;

  const pickModel = (raw: string) => {
    setModelId(raw === OFF_MODEL ? null : Number(raw));
    setRulesMet([]); // the old model's checks mean nothing against the new one
  };

  const toggleRule = (id: number) =>
    setRulesMet((prev) => (prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id]));

  const onSave = (e: React.FormEvent) => {
    e.preventDefault();
    // setups/confluences are echoed back untouched: the archived era keeps its
    // badges, but nothing in this form can add to them.
    save.mutate({
      note,
      tags,
      setups: initialSetups,
      confluences: initialConfluences,
      model_id: modelId,
      rules_met: rulesMet,
    });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
      </div>

      <div className="field" style={{ marginBottom: 10 }}>
        <label>Model</label>
        <select
          value={modelId == null ? OFF_MODEL : String(modelId)}
          onChange={(e) => pickModel(e.target.value)}
        >
          <option value={OFF_MODEL}>Off-model</option>
          {models.map((m) => (
            <option key={m.id} value={String(m.id)}>
              {m.name}
            </option>
          ))}
        </select>
      </div>

      {model && (
        <div className="field" style={{ marginBottom: 10 }}>
          <label>
            Rules met ({rulesMet.length}/{rules.length})
          </label>
          {rules.length === 0 ? (
            <div className="section-cap">
              “{model.name}” declares no entry rules yet — add them on the Models tab.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {rules.map((r) => (
                <label key={r.id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={rulesMet.includes(r.id)}
                    onChange={() => toggleRule(r.id)}
                  />
                  <span>{r.label}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="field" style={{ marginBottom: 10 }}>
        <label>Tags</label>
        <BadgeInput
          value={tags}
          onChange={setTags}
          suggestions={opts?.tags ?? []}
          placeholder="add a tag…"
        />
      </div>

      {legacy && (
        <div className="field" style={{ marginBottom: 10 }}>
          <label>Legacy badges (read-only)</label>
          <div className="section-cap" style={{ marginBottom: 4 }}>
            Setups and confluences from before the model cutover. Kept for the
            record; superseded by the model + rule checklist above.
          </div>
          {initialSetups.length > 0 && <BadgeList items={initialSetups} />}
          {initialConfluences.length > 0 && <BadgeList items={initialConfluences} />}
        </div>
      )}

      <button type="submit" className="btn-accent" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save"}
      </button>
      {save.isSuccess && <span className="pos" style={{ marginLeft: 10 }}>Saved.</span>}
    </form>
  );
}
