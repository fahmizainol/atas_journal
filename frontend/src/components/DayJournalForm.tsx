import { useEffect, useState } from "react";
import { useDayNote, useSaveDayNote } from "../hooks/useCalendar";
import { useFilters } from "../hooks/useFilters";
import { useFiltersData } from "../hooks/useMeta";
import { BadgeInput } from "./BadgeInput";

// Per-day note + badge tags. Mirrors JournalForm but keyed by date.
export function DayJournalForm({ date }: { date: string }) {
  const { data } = useDayNote(date);
  const save = useSaveDayNote(date);
  const { scope } = useFilters();
  const { data: opts } = useFiltersData(scope);
  const [note, setNote] = useState("");
  const [tags, setTags] = useState<string[]>([]);

  useEffect(() => {
    if (!data) return;
    setNote(data.note ?? "");
    setTags(data.tags ?? []);
  }, [date, data]);

  const onSave = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate({ note, tags });
  };

  return (
    <form className="panel" onSubmit={onSave}>
      <div className="section-title">Day journal</div>
      <div className="field" style={{ marginBottom: 10 }}>
        <label>Note</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4} />
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
