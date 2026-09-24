// The account manager: make an account, change its numbers, retire it.
//
// Extracted from `charts/AccountSwitch` so the Accounts page and the chart's
// switcher render the same form rather than two that drift. The switcher shows
// it in a popover while you are trading; the page shows it full width while you
// are reading. Neither owns it, which is the point — an account's numbers are
// the same numbers wherever you are standing.
//
// It manages accounts and nothing else. What an account *did* is the Accounts
// page's business (`pages/AccountDetail`), and pricing a sitting is the
// account's own walk — see `journal/replay_account.py`.

import { useState } from "react";
import {
  useAccountEdits,
  type AccountNumbers,
  type AccountRow,
  type AccountTemplate,
} from "../../hooks/useReplayAccounts";
import { fmtRecord } from "../../lib/replayAccount";
import { palette } from "../../theme";

const money = (n: number) => `$${Math.round(n).toLocaleString()}`;

const FIELDS: { key: keyof AccountNumbers; label: string; hint: string }[] = [
  { key: "start", label: "Start", hint: "What the account opens at." },
  { key: "max_loss", label: "Max loss", hint: "The distance from the peak to the floor — the drawdown." },
  { key: "trail_cap", label: "Trail cap", hint: "Once the peak reaches this the floor stops following and locks under it." },
  { key: "day_loss", label: "Daily loss", hint: "Reaching it closes what is open and ends the day. The account survives." },
  { key: "day_goal", label: "Day goal", hint: "Blank for none. Reaching it on booked P&L makes it the day's floor — so arming it effectively ends the day." },
  { key: "profit_target", label: "Target", hint: "What passing looks like." },
  { key: "max_minis", label: "Max minis", hint: "Contract cap." },
  { key: "max_micros", label: "Max micros", hint: "Contract cap." },
];

export function AccountManager({
  accounts,
  templates,
  current,
  onDone,
}: {
  accounts: AccountRow[];
  templates: AccountTemplate[];
  current: string;
  onDone: () => void;
}) {
  const { create, patch, remove } = useAccountEdits();
  const [editing, setEditing] = useState<string | null>(current);
  const [tplKey, setTplKey] = useState(templates[0]?.key ?? "lucid_pro");
  const [label, setLabel] = useState("");
  const [draft, setDraft] = useState<Partial<AccountNumbers> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const row = accounts.find((a) => a.id === editing) ?? null;
  const tpl = templates.find((t) => t.key === tplKey) ?? null;
  const nums = { ...(row?.numbers ?? tpl?.defaults), ...(draft ?? {}) } as AccountNumbers;

  const fail = (e: unknown) => setErr(e instanceof Error ? e.message : String(e));

  return (
    <div
      className="panel"
      style={{ width: "100%", marginTop: 6, padding: 8, fontSize: 11 }}
      data-account-manager
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6 }}>
        <select
          value={editing ?? "__new"}
          onChange={(e) => {
            setEditing(e.target.value === "__new" ? null : e.target.value);
            setDraft(null);
            setErr(null);
          }}
          style={{ fontSize: 11 }}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
              {a.archived ? " [archived]" : ""}
            </option>
          ))}
          <option value="__new">+ new account…</option>
        </select>
        {!editing && (
          <>
            <select value={tplKey} onChange={(e) => setTplKey(e.target.value)} style={{ fontSize: 11 }}>
              {templates.map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
            <input
              value={label}
              placeholder="name"
              onChange={(e) => setLabel(e.target.value)}
              style={{ fontSize: 11, width: 120 }}
            />
          </>
        )}
      </div>

      {/* The rule shape, stated rather than implied. It is the one thing that
          cannot be edited afterwards — its sittings were traded under it. */}
      {(row || tpl) && (
        <div style={{ color: palette.muted, lineHeight: 1.5, marginBottom: 6 }}>
          <b>{row ? templates.find((t) => t.key === row.template)?.label ?? row.template : tpl?.label}</b>
          {" — "}
          {(row
            ? templates.find((t) => t.key === row.template)?.note
            : tpl?.note) ?? ""}
          {row && <> Fixed when the account opened; make a new one to change it.</>}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
        {FIELDS.map((f) => (
          <label key={f.key} title={f.hint} style={{ display: "block" }}>
            <div style={{ color: palette.muted, fontSize: 10 }}>{f.label}</div>
            <input
              type="number"
              value={nums[f.key] == null ? "" : String(nums[f.key])}
              placeholder={f.key === "day_goal" ? "none" : ""}
              onChange={(e) => {
                const raw = e.target.value;
                setDraft((d) => ({
                  ...(d ?? {}),
                  [f.key]: raw === "" ? null : Number(raw),
                }));
              }}
              style={{ fontSize: 11, width: "100%" }}
            />
          </label>
        ))}
      </div>

      {err && <div style={{ color: palette.red, marginTop: 6, lineHeight: 1.5 }}>{err}</div>}

      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn"
          style={{ fontSize: 11 }}
          disabled={create.isPending || patch.isPending || (!editing && !label.trim())}
          onClick={() => {
            setErr(null);
            const numbers = draft ?? undefined;
            if (editing && row) {
              patch.mutate({ id: editing, label: row.label, numbers }, { onError: fail, onSuccess: onDone });
            } else {
              create.mutate({ label: label.trim(), template: tplKey, numbers }, { onError: fail, onSuccess: onDone });
            }
          }}
        >
          {editing ? "Save" : "Create"}
        </button>
        {row && row.id !== "funded" && row.id !== "paper" && (
          <>
            <button
              type="button"
              className="btn"
              style={{ fontSize: 11 }}
              onClick={() => {
                setErr(null);
                patch.mutate(
                  { id: row.id, label: row.label, archived: !row.archived },
                  { onError: fail },
                );
              }}
              title="Hidden from the switcher, still prices every sitting it took. This is what you do instead of deleting an account that has traded."
            >
              {row.archived ? "Un-archive" : "Archive"}
            </button>
            {row.epochs === 0 && (
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, color: palette.red }}
                onClick={() => {
                  setErr(null);
                  remove.mutate(row.id, { onError: fail, onSuccess: onDone });
                }}
                title="Only ever offered on an account that has never traded — anything else is archived, or its sittings would be priced by nobody."
              >
                Delete
              </button>
            )}
          </>
        )}
        {row && (
          <span style={{ color: palette.muted, alignSelf: "center" }}>
            {row.epochs === 0
              ? "never traded"
              : [
                  `${row.epochs} life${row.epochs === 1 ? "" : "s"}`,
                  // Which is the whole reason to keep several accounts open:
                  // the lives are the reps and this is what they came to.
                  fmtRecord(row.record),
                  `start ${money(row.numbers.start)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
          </span>
        )}
      </div>
    </div>
  );
}
