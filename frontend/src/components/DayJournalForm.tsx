import { useEffect, useState } from "react";
import { useDayNote, useSaveDayNote } from "../hooks/useCalendar";

// Per-day note + comma-separated tags. Mirrors JournalForm but keyed by date.
export function DayJournalForm({ date }: { date: string }) {
  const { data } = useDayNote(date);
  const save = useSaveDayNote(date);
  const [note, setNote] = useState("");
  const [tags, setTags] = useState("");

  useEffect(() => {
    if (!data) return;
    setNote(data.note ?? "");
    setTags((data.tags ?? []).join(", "));
  }, [date, data]);

  const onSave = (e: React.FormEvent) => {
    e.preventDefault();
    const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
    save.mutate({ note, tags: tagList });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Day journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4} />
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
