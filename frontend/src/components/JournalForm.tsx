import { useEffect, useState } from "react";
import { useSaveNote } from "../hooks/useTrades";

// First WRITE path: save a trade's note + comma-separated tags.
export function JournalForm({
  tradeKey,
  initialNote,
  initialTags,
}: {
  tradeKey: string;
  initialNote: string;
  initialTags: string[];
}) {
  const [note, setNote] = useState(initialNote);
  const [tags, setTags] = useState(initialTags.join(", "));
  const save = useSaveNote(tradeKey);

  // Reset the form when switching to a different trade.
  useEffect(() => {
    setNote(initialNote);
    setTags(initialTags.join(", "));
  }, [tradeKey, initialNote, initialTags]);

  const onSave = (e: React.FormEvent) => {
    e.preventDefault();
    const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
    save.mutate({ note, tags: tagList });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
      </div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Tags (comma-separated)</label>
        <input value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <button type="submit" className="btn-accent" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save"}
      </button>
      {save.isSuccess && <span className="pos" style={{ marginLeft: 10 }}>Saved.</span>}
    </form>
  );
}
