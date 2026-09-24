import { useEffect, useState } from "react";
import { useSaveNote } from "../hooks/useTrades";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";
import { selectableModels, useModels } from "../hooks/useModels";
import { useReviewVocab } from "../hooks/useReplays";
import { BadgeInput, BadgeList } from "./BadgeInput";
import { AxisRow, DISCIPLINE_LABEL, LevelRow, SETUP_LABEL } from "./charts/ReviewCard";
import type { LevelCandidate } from "../hooks/useReplays";

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
  initialSetup = null,
  initialDiscipline = null,
  initialWatchedLevels = [],
  levelCandidates = [],
}: {
  tradeKey: string;
  initialNote: string;
  initialTags: string[];
  initialSetups: string[];
  initialConfluences: string[];
  initialModelId: number | null;
  initialRulesMet: number[];
  initialSetup?: string | null;
  initialDiscipline?: string | null;
  initialWatchedLevels?: string[];
  /** The measured offer (level_store.candidates_for, off the detail payload).
   *  With the trade's replay open right above this form, the chart the pick
   *  needs is in view — the reason this used to be replay-rail-only. */
  levelCandidates?: LevelCandidate[];
}) {
  const [note, setNote] = useState(initialNote);
  const [tags, setTags] = useState<string[]>(initialTags);
  const [modelId, setModelId] = useState<number | null>(initialModelId);
  const [rulesMet, setRulesMet] = useState<number[]>(initialRulesMet);
  const [setup, setSetup] = useState<string | null>(initialSetup);
  const [discipline, setDiscipline] = useState<string | null>(initialDiscipline);
  const [watchedLevels, setWatchedLevels] = useState<string[]>(initialWatchedLevels);
  const save = useSaveNote(tradeKey);
  const { scope } = useFilters();
  const { data: opts } = useFiltersData(scope);
  const { data: models = [] } = useModels();
  const vocab = useReviewVocab();

  // Reset the form when switching to a different trade.
  useEffect(() => {
    setNote(initialNote);
    setTags(initialTags);
    setModelId(initialModelId);
    setRulesMet(initialRulesMet);
    setSetup(initialSetup);
    setDiscipline(initialDiscipline);
    setWatchedLevels(initialWatchedLevels);
  }, [tradeKey, initialNote, initialTags, initialModelId, initialRulesMet,
      initialSetup, initialDiscipline, initialWatchedLevels]);

  const model = models.find((m) => m.id === modelId) ?? null;
  const rules = model?.rules ?? [];
  // An archived model stays offered while this trade is bound to it, so the
  // binding and its checklist remain visible and editable.
  const options = selectableModels(models, modelId);
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
      // Partial on the server: null means unchanged. Un-answering is not a
      // move (the review gate requires these), so a chip toggled off simply
      // isn't sent and springs back on the next load.
      setup,
      discipline,
      // Except the levels, whose list REPLACES the stored set — deselecting
      // one of several has to persist. Sent only once something is picked, so
      // an untouched form can't clear a pick made elsewhere mid-edit.
      watched_levels: watchedLevels.length ? watchedLevels : null,
    });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
      </div>

      {/* Folded away, because the binding is answered once and the checklist is
          long, while the note and the tags are what a review comes back to. The
          summary carries the whole answer — which model, and how much of its
          checklist was met — so folding it costs no information, only room. */}
      <details className="journal-fold">
        <summary title="Which model this trade was taken on, and which of its entry rules it met">
          {model
            ? `Model: ${model.name}${rules.length ? ` (${rulesMet.length}/${rules.length})` : ""}`
            : "Model: off-model"}
        </summary>

        <div className="field" style={{ margin: "8px 0 10px" }}>
          <label>Model</label>
          <select
            value={modelId == null ? OFF_MODEL : String(modelId)}
            onChange={(e) => pickModel(e.target.value)}
          >
            <option value={OFF_MODEL}>Off-model</option>
            {options.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {m.name}
                {m.archived ? " (archived)" : ""}
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
      </details>

      {/* The review's answers — the same chips, labels and vocabulary as the
          replay/drill/recall pickers (AxisRow and LevelRow are theirs). These
          used to be answered through the tag box, which is how the tag
          vocabulary grew to 70 entries carrying four different questions; the
          tags below are for everything they can't say. The level pick belongs
          where the chart is — which, since the replay moved in above this
          form, is here. Only the grade stays out: it is answered blind at the
          recall front. */}
      <div className="field" style={{ marginBottom: 10 }} data-journal-levels>
        <label>Watched level</label>
        <LevelRow
          levels={levelCandidates}
          picked={watchedLevels}
          noLevel={vocab.data?.no_level ?? "none"}
          onChange={setWatchedLevels}
        />
      </div>
      <div className="field" style={{ marginBottom: 10 }} data-journal-setup>
        <label>Setup</label>
        <AxisRow
          options={vocab.data?.setups ?? []}
          labels={SETUP_LABEL}
          value={setup}
          attr="setup"
          onPick={setSetup}
        />
      </div>
      <div className="field" style={{ marginBottom: 10 }} data-journal-discipline>
        <label>Discipline</label>
        <AxisRow
          options={vocab.data?.disciplines ?? []}
          labels={DISCIPLINE_LABEL}
          value={discipline}
          attr="discipline"
          onPick={setDiscipline}
        />
      </div>

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
