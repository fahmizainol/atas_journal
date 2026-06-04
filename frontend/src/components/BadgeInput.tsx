import { useMemo, useRef, useState } from "react";

// Controlled list-of-strings editor. Typing a name then "," or Enter commits it
// as a badge; Backspace on an empty input removes the last badge; each badge has
// an X (shown on hover/focus) to delete it. Dedupes and trims; empties ignored.
//
// `suggestions` are already-existing values (tags/playbooks/confluences): a
// dropdown of matches opens on focus and filters as you type. Pick with the
// mouse or ↑/↓ + Enter.
export function BadgeInput({
  value,
  onChange,
  placeholder,
  suggestions = [],
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1); // highlighted suggestion index
  const inputRef = useRef<HTMLInputElement>(null);

  // Known values not yet picked, matching the current draft (case-insensitive).
  const matches = useMemo(() => {
    const picked = new Set(value.map((v) => v.toLowerCase()));
    const q = draft.trim().toLowerCase();
    return suggestions
      .filter((s) => !picked.has(s.toLowerCase()))
      .filter((s) => (q ? s.toLowerCase().includes(q) : true))
      .slice(0, 8);
  }, [suggestions, value, draft]);

  const showMenu = open && matches.length > 0;

  const commit = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    if (!value.includes(v)) onChange([...value, v]);
    setDraft("");
    setActive(-1);
  };

  const remove = (badge: string) => onChange(value.filter((b) => b !== badge));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (showMenu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      setActive((i) => {
        const n = matches.length;
        return e.key === "ArrowDown" ? (i + 1) % n : (i - 1 + n) % n;
      });
      return;
    }
    if (e.key === "," || e.key === "Enter") {
      e.preventDefault();
      if (showMenu && active >= 0) commit(matches[active]);
      else commit(draft);
    } else if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    } else if (e.key === "Backspace" && draft === "" && value.length) {
      e.preventDefault();
      onChange(value.slice(0, -1));
    }
  };

  // A trailing comma in a paste (or fast typing) also commits.
  const onChangeDraft = (e: React.ChangeEvent<HTMLInputElement>) => {
    const text = e.target.value;
    setOpen(true);
    setActive(-1);
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
      <div className="badge-input-wrap">
        <input
          ref={inputRef}
          className="badge-input-field"
          value={draft}
          onChange={onChangeDraft}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          // Delay so a suggestion's mousedown/click resolves before we commit/close.
          onBlur={() => {
            commit(draft);
            setOpen(false);
          }}
          placeholder={value.length === 0 ? placeholder : ""}
        />
        {showMenu && (
          <ul className="badge-menu">
            {matches.map((s, i) => (
              <li
                key={s}
                className={`badge-menu-item${i === active ? " active" : ""}`}
                // mousedown (not click) so it fires before the input's blur.
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(s);
                  inputRef.current?.focus();
                }}
                onMouseEnter={() => setActive(i)}
              >
                {s}
              </li>
            ))}
          </ul>
        )}
      </div>
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
