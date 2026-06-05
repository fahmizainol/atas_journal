import { useState } from "react";
import type { TaxonomyKind } from "../hooks/useTaxonomy";
import {
  useCreateTaxonomy,
  useDeleteTaxonomy,
  useTaxonomyList,
  useUpdateTaxonomy,
} from "../hooks/useTaxonomy";
import type { TaxonomyItem } from "../lib/types";

// One editable row: a name + description that commits on Save (only when dirty)
// and confirms before Delete. Local draft state so edits don't fight the query
// cache mid-typing; resets to the server value whenever that value changes.
function TaxonomyRow({
  item,
  noun,
  onSave,
  onDelete,
  busy,
}: {
  item: TaxonomyItem;
  noun: string;
  onSave: (name: string, newName: string, description: string) => void;
  onDelete: (name: string) => void;
  busy: boolean;
}) {
  const [name, setName] = useState(item.name);
  const [desc, setDesc] = useState(item.description);
  const dirty = name.trim() !== item.name || desc !== item.description;

  const save = () => {
    if (!name.trim() || !dirty) return;
    onSave(item.name, name.trim(), desc);
  };
  const del = () => {
    if (
      window.confirm(
        `Delete ${noun} "${item.name}"? It will be removed from any trades tagged ` +
          `with it (the trades stay). This can't be undone.`,
      )
    )
      onDelete(item.name);
  };

  return (
    <tr>
      <td style={{ width: "26%" }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: "100%" }}
        />
      </td>
      <td>
        <input
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="description…"
          style={{ width: "100%" }}
        />
      </td>
      <td style={{ whiteSpace: "nowrap", width: 1 }}>
        <button
          type="button"
          className="btn-accent"
          onClick={save}
          disabled={busy || !dirty || !name.trim()}
          style={{ marginRight: 6 }}
        >
          Save
        </button>
        <button type="button" className="btn-danger" onClick={del} disabled={busy}>
          Delete
        </button>
      </td>
    </tr>
  );
}

// Collapsible CRUD panel for a setup/confluence master list. Sits above the
// stats on each tab; the inline badge field on a trade is the other create path.
export function TaxonomyManager({ kind, noun }: { kind: TaxonomyKind; noun: string }) {
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const { data: items = [], isLoading } = useTaxonomyList(kind);
  const create = useCreateTaxonomy(kind);
  const update = useUpdateTaxonomy(kind);
  const del = useDeleteTaxonomy(kind);
  const busy = create.isPending || update.isPending || del.isPending;

  const nounPlural = `${noun}s`;
  const exists = items.some((i) => i.name.toLowerCase() === newName.trim().toLowerCase());

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name || exists) return;
    create.mutate(
      { name, description: newDesc.trim() },
      {
        onSuccess: () => {
          setNewName("");
          setNewDesc("");
        },
      },
    );
  };

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <button
        type="button"
        className={open ? "active" : ""}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▾" : "▸"} Manage {nounPlural} ({items.length})
      </button>

      {open && (
        <div style={{ marginTop: 12 }}>
          <form
            onSubmit={add}
            style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 12 }}
          >
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={`new ${noun} name…`}
              style={{ flex: "0 0 26%" }}
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="description (optional)…"
              style={{ flex: 1 }}
            />
            <button
              type="submit"
              className="btn-accent"
              disabled={busy || !newName.trim() || exists}
            >
              Add
            </button>
          </form>
          {exists && (
            <div className="section-cap neg" style={{ marginBottom: 8 }}>
              A {noun} named “{newName.trim()}” already exists.
            </div>
          )}

          {isLoading ? (
            <div className="section-cap">Loading…</div>
          ) : items.length === 0 ? (
            <div className="section-cap">No {nounPlural} yet — add one above.</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Description</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <TaxonomyRow
                    key={it.name}
                    item={it}
                    noun={noun}
                    busy={busy}
                    onSave={(name, newName, description) =>
                      update.mutate({ name, new_name: newName, description })
                    }
                    onDelete={(name) => del.mutate(name)}
                  />
                ))}
              </tbody>
            </table>
          )}
          <div className="section-cap" style={{ marginTop: 10 }}>
            Renaming a {noun} updates it on every trade tagged with it. Deleting
            removes the tag from those trades but keeps the trades.
          </div>
        </div>
      )}
    </div>
  );
}
