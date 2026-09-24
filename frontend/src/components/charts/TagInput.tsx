// Free-form tag chips with autocomplete off the shared vocabulary.
//
// Deliberately not a curated picker: tags are the taxonomy that grows by being
// typed (plan decision V5 — docs/review-revamp-plan.md), so the input accepts
// anything and the datalist only *offers* what has been used before. Setups and
// confluences keep their curated pickers elsewhere; this is the other thing.

import { useId, useState } from "react";
import { palette } from "../../theme";

export function TagInput({
  value,
  onChange,
  suggestions,
  placeholder,
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  /** The shared vocabulary, most-used first. Already-chosen tags are filtered
   *  out of the offer list, not out of the vocabulary. */
  suggestions: string[];
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const listId = useId();

  const add = (raw: string) => {
    const t = raw.trim();
    if (!t) return;
    if (!value.includes(t)) onChange([...value, t]);
    setDraft("");
  };

  return (
    <div data-tag-input style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
      {value.map((t) => (
        <span
          key={t}
          data-tag={t}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            fontSize: 11,
            padding: "1px 6px",
            borderRadius: 9,
            border: `1px solid ${palette.cardBorder}`,
            color: palette.text,
          }}
        >
          {t}
          <button
            type="button"
            onClick={() => onChange(value.filter((x) => x !== t))}
            title={`Remove "${t}"`}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              color: palette.muted,
              fontSize: 11,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </span>
      ))}
      <input
        list={listId}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          // Enter and comma both commit — comma because a tag list is the one
          // field people type like a sentence. Never let Enter submit anything
          // above this input.
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => add(draft)}
        placeholder={placeholder ?? (value.length ? "" : "add a tag…")}
        style={{ flex: 1, minWidth: 90, fontSize: 11 }}
      />
      <datalist id={listId}>
        {suggestions
          .filter((s) => !value.includes(s))
          .map((s) => (
            <option key={s} value={s} />
          ))}
      </datalist>
    </div>
  );
}
