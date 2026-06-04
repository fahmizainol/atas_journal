import { useState } from "react";

// Controlled list-of-strings editor. Typing a name then "," or Enter commits it
// as a badge; Backspace on an empty input removes the last badge; each badge has
// an X (shown on hover/focus) to delete it. Dedupes and trims; empties ignored.
export function BadgeInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  const commit = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    if (!value.includes(v)) onChange([...value, v]);
    setDraft("");
  };

  const remove = (badge: string) => onChange(value.filter((b) => b !== badge));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "," || e.key === "Enter") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && draft === "" && value.length) {
      e.preventDefault();
      onChange(value.slice(0, -1));
    }
  };

  // A trailing comma in a paste (or fast typing) also commits.
  const onChangeDraft = (e: React.ChangeEvent<HTMLInputElement>) => {
    const text = e.target.value;
    if (text.includes(",")) {
      const parts = text.split(",");
      const last = parts.pop() ?? "";
      parts.forEach(commit);
      setDraft(last);
    } else {
      setDraft(text);
    }
  };

  return (
    <div className="badge-input">
      {value.map((b) => (
        <span key={b} className="badge">
          {b}
          <button
            type="button"
            className="badge-x"
            aria-label={`Remove ${b}`}
            onClick={() => remove(b)}
          >
            ×
          </button>
        </span>
      ))}
      <input
        className="badge-input-field"
        value={draft}
        onChange={onChangeDraft}
        onKeyDown={onKeyDown}
        onBlur={() => commit(draft)}
        placeholder={value.length === 0 ? placeholder : ""}
      />
    </div>
  );
}

// Read-only badge row for tables (no input, no remove control).
export function BadgeList({ items }: { items: string[] }) {
  if (!items || items.length === 0) return <span className="muted">—</span>;
  return (
    <span className="badge-list">
      {items.map((b) => (
        <span key={b} className="badge badge-sm">
          {b}
        </span>
      ))}
    </span>
  );
}
