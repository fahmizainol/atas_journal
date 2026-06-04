import { useEffect, useState } from "react";
import { useSaveNote } from "../hooks/useTrades";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";
import { BadgeInput } from "./BadgeInput";

// First WRITE path: save a trade's note + badge-based tags / playbooks / confluences.
export function JournalForm({
  tradeKey,
  initialNote,
  initialTags,
  initialPlaybooks,
  initialConfluences,
}: {
  tradeKey: string;
  initialNote: string;
  initialTags: string[];
  initialPlaybooks: string[];
  initialConfluences: string[];
}) {
  const [note, setNote] = useState(initialNote);
  const [tags, setTags] = useState<string[]>(initialTags);
  const [playbooks, setPlaybooks] = useState<string[]>(initialPlaybooks);
  const [confluences, setConfluences] = useState<string[]>(initialConfluences);
  const save = useSaveNote(tradeKey);
  // Known values for autocomplete (global; /filters scans all notes).
  const { scope } = useFilters();
  const { data: opts } = useFiltersData(scope);

  // Reset the form when switching to a different trade.
  useEffect(() => {
    setNote(initialNote);
    setTags(initialTags);
    setPlaybooks(initialPlaybooks);
    setConfluences(initialConfluences);
  }, [tradeKey, initialNote, initialTags, initialPlaybooks, initialConfluences]);

  const onSave = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate({ note, tags, playbooks, confluences });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
      </div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Playbooks</label>
        <BadgeInput
          value={playbooks}
          onChange={setPlaybooks}
          suggestions={opts?.playbooks ?? []}
          placeholder="failed auction, trendlines…"
        />
      </div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Confluences</label>
        <BadgeInput
          value={confluences}
          onChange={setConfluences}
          suggestions={opts?.confluences ?? []}
          placeholder="vwap, cvd, vp ON…"
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
      <button type="submit" className="btn-accent" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save"}
      </button>
      {save.isSuccess && <span className="pos" style={{ marginLeft: 10 }}>Saved.</span>}
    </form>
  );
}
