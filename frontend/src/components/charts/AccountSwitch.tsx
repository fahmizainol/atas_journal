// Which account you are trading, and the form that makes another one.
//
// A `<select>` rather than the two-state toggle button this replaced, and it is
// deliberately the same control Live's `RoutingPanel.AccountSwitch` uses: the
// two terminals are one product, and picking an account should not be a
// different gesture depending on which tab you are on.
//
// **Switching is a navigation, not a state change.** The account is fixed for
// the whole of a sitting — it is stamped on the attempt when it opens and can
// never be moved (`replays.account_id_of`) — so the route carries it and the
// router's `key` makes the switch a real unmount. Without that, React would
// reconcile: same engine, same order log, same still-armed recorder, now
// pointing at a different ledger.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccountManager } from "../accounts/AccountManager";
import { useReplayAccounts } from "../../hooks/useReplayAccounts";

/** Where an account lives. The two built-ins keep the paths they have always
 *  had so every existing link, bookmark and browser check still resolves. */
export function pathOfAccount(id: string): string {
  if (id === "paper") return "/charts/replay/paper";
  if (id === "funded") return "/charts/replay";
  return `/charts/replay/a/${encodeURIComponent(id)}`;
}

export function AccountSwitch({
  accountId,
  /** Null while a sitting is open or a review is owed — the account cannot move
   *  under a sitting it is pricing, and the caller is what knows that. */
  onSwitch,
}: {
  accountId: string;
  onSwitch: (() => void) | null;
}) {
  const navigate = useNavigate();
  const q = useReplayAccounts();
  const [managing, setManaging] = useState(false);

  const rows = q.data?.accounts ?? [];
  // Archived accounts still price their own history and are still reachable by
  // URL — they are just not something you pick into. The current one stays in
  // the list even if archived, or the select would render blank on it.
  const shown = rows.filter((a) => !a.archived || a.id === accountId);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <select
        value={accountId}
        disabled={!onSwitch || q.isLoading}
        data-account-switch
        title={
          onSwitch
            ? "Which account this sitting is priced on. Each one is a separate walk over the sittings stamped with it — none of them can move another."
            : "Refused while a sitting is open or a review is owed. An account is fixed for the whole of a sitting: it is stamped on the attempt when it opens and never moves."
        }
        onChange={(e) => {
          const id = e.target.value;
          if (id === accountId) return;
          onSwitch?.();
          navigate(pathOfAccount(id));
        }}
        style={{ fontSize: 11, maxWidth: 190 }}
      >
        {shown.map((a) => (
          <option key={a.id} value={a.id}>
            {a.id === "paper" ? "📝 " : ""}
            {a.label}
            {a.archived ? "  [archived]" : ""}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn"
        style={{ fontSize: 10, padding: "2px 6px" }}
        onClick={() => setManaging((v) => !v)}
        title="Add an account, or change this one's numbers."
        data-account-manage
      >
        {managing ? "close" : "accounts…"}
      </button>
      {managing && (
        <AccountManager
          accounts={rows}
          templates={q.data?.templates ?? []}
          current={accountId}
          onDone={() => setManaging(false)}
        />
      )}
    </div>
  );
}
