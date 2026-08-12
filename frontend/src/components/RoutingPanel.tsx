// Order entry — the one place in this app that can reach an exchange.
//
// It is a rail panel on the Live page: the account selector, the discipline
// layer, the book, and a typed order pad beside them. The chart's own gestures
// — the ＋Order tool, the long-press ticket, q/w/s, the BUY/SELL dock,
// space+click — reach the same endpoints through `hooks/useOrderIntent`, which
// is the one funnel each of them asks. This panel is not a second order path;
// it is the one place you can *type* an order rather than point at it.
//
// Which is why the ticket comes down as a prop. Size and the bracket belong to
// the page, so the number in the pad and the number a chart gesture sends are
// the same number — see the note on `RoutingPanel`'s `ticket`.
//
// FOUR THINGS THIS PANEL OWES THE READER, AND WHY EACH ONE IS HERE.
//
//   1. **Which account this is, permanently.** The kind badge is drawn in every
//      state, including the ones where nothing can be sent. It says what a
//      person *declared* this account to be — an untagged account reads as
//      untagged, because the failure this whole feature guards against is a
//      funded account that looked like a paper one.
//   2. **Paper until a real account is chosen.** Every session starts on paper
//      and nothing on it reaches a broker; picking a real account is the act
//      that makes the gestures live, and the selector says so in one place.
//   3. **Review, then send.** The sentence comes down from the server with a
//      single-use token, and Send posts the token alone. The client cannot
//      construct an order to send even if this component were wrong.
//   4. **The broker is the authority.** The working list and the position are
//      what Rithmic says, not what we did. `reconciled_at === null` is drawn as
//      "not asked" and never as "nothing working" — the two look identical and
//      only one of them is safe to act on.
//
// THERE IS NO ARM. There was: a typed confirmation that made this panel
// read-only for fifteen minutes at a time. It went because the deadline was the
// wrong shape — it lapsed mid-decision and stood open while nobody watched. The
// account selector is now the whole of the mode switch, and the per-account
// confirm popup (2, off by default) is the whole of the ceremony.
//
// THE KILL SWITCH IS ALWAYS THERE. Flatten is enabled whenever there is a
// connection, whatever else is true — the moment you want it most is the one
// where something is behaving in a way nobody planned.

import { useEffect, useState } from "react";
import {
  cancelBrokerOrder,
  flattenAll,
  previewOrder,
  refreshBroker,
  sendOrder,
  sendOrderNow,
  setBrokerAccount,
  setBrokerInstrument,
  saveRoutingSettings,
  setOneClick,
  tagAccount,
  useRoutingStatus,
} from "../hooks/useRouting";
import {
  PAPER,
  type BrokerOrder,
  type BrokerState,
  type GuardLevels,
  type GuardState,
  type OrderPreview,
  type RoutingStatus,
} from "../lib/routingTypes";
import { fmtPts, fmtUsd } from "../lib/simViews";
import type { LiveTicket } from "../lib/simPrefs";
import { palette } from "../theme";

const TYPES = ["market", "limit", "stop"] as const;
type OrderKind = (typeof TYPES)[number];

const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Seconds as m:ss. The review's countdown, which is the only one left. */
function mmss(s: number): string {
  const n = Math.max(0, Math.round(s));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
}

/** How this panel edits the page's ticket. One field at a time, because that is
 *  what an input change is — and it keeps the page as the only thing that ever
 *  holds a whole ticket. */
export type TicketEdit = <K extends keyof LiveTicket>(key: K, v: LiveTicket[K]) => void;

export function RoutingPanel({
  mark,
  tickSize,
  ticket,
  onTicket,
}: {
  mark: number;
  tickSize: number;
  /** **The page's ticket, not this panel's.** The pad below and every gesture on
   *  the chart send through the same endpoints, so they have to be measuring the
   *  same order — this panel used to keep its own size/stop/target and the two
   *  drifted the moment either was touched, which meant an order going out with
   *  a bracket nobody had typed. */
  ticket: LiveTicket;
  onTicket: TicketEdit;
}) {
  const q = useRoutingStatus();
  const status = q.data;
  const broker = status?.broker ?? null;
  const refetch = () => void q.refetch();

  return (
    <div className="panel" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Order entry</h3>
        {broker && <KindBadge kind={broker.kind} />}
      </div>

      {!status ? (
        <div style={{ fontSize: 12, color: palette.muted, marginTop: 8 }}>Loading…</div>
      ) : status.refusal ? (
        // The deployment cannot route. Said in full rather than hidden: the
        // reason names the env var to set, and a control that vanished would
        // read as "this build cannot do that". Paper trading on the chart is
        // unaffected and keeps working, which the message says.
        <Note tone="muted">{status.refusal}</Note>
      ) : !status.routing_session ? (
        <Note tone="muted">
          {status.session
            ? "This session was connected without routing. The ORDER plant is opened at connect and never afterwards — so a shadow session cannot acquire the ability to trade while you watch. Stop the feed and reconnect with routing on."
            : "No live session. Routing rides the tick feed's own connection: one Rithmic login is one socket, so there is no order path without a tape."}
        </Note>
      ) : broker?.error ? (
        <Note tone="red">⚠ {broker.error}</Note>
      ) : !broker?.attached ? (
        // NOT the same as "not reconciled yet", and the first cut of this panel
        // ran them together — a feed that never connected sat on "reading the
        // broker's state…" forever, which reads as "wait a second" when the
        // truth is "this is not coming back". A disconnect also lands here,
        // since `detach` runs in the feed's `finally`.
        <Note tone="orange">
          The order plant is not connected. Routing rides the tick feed's own
          socket, so it comes back when the feed does — and it comes back{" "}
          <b>having to re-read the broker</b> before it will send anything,
          because a picture of the book does not outlive the socket it came
          over. Check the feed banner below the chart.
        </Note>
      ) : (
        <Live status={status} broker={broker} mark={mark} tickSize={tickSize}
              ticket={ticket} onTicket={onTicket} onDone={refetch} />
      )}
    </div>
  );
}

/** What kind of account this is, as a colour and a word.
 *
 *  `null` is the interesting value: an account nobody has labelled. It is not a
 *  loading state and it is not "probably demo" — nothing Rithmic sends says
 *  whether an account is funded, so the app cannot work it out, and an untagged
 *  account refuses to send until a person says which it is.
 */
function KindBadge({ kind }: { kind: string | null }) {
  const spec =
    kind === PAPER
      ? { c: palette.blue, t: "Paper — a blotter folded over the tape. Nothing here reaches a broker." }
      : kind === "live"
        ? { c: palette.red, t: "Labelled LIVE. Orders on this account move real money." }
        : kind === "demo"
          ? { c: palette.green, t: "Labelled DEMO." }
          : { c: palette.orange, t: "Not labelled. Nothing Rithmic sends says whether this account is funded — tag it demo or live before it can send anything." };
  return (
    <span
      title={spec.t}
      style={{
        fontSize: 10,
        letterSpacing: 0.5,
        padding: "1px 6px",
        borderRadius: 10,
        border: `1px solid ${spec.c}`,
        color: spec.c,
      }}
    >
      {(kind ?? "untagged").toUpperCase()}
    </span>
  );
}

function Note({ tone, children }: { tone: "muted" | "orange" | "red"; children: React.ReactNode }) {
  const color = tone === "muted" ? palette.muted : tone === "orange" ? palette.orange : palette.red;
  return (
    <p style={{ fontSize: 11, color, margin: "8px 0", lineHeight: 1.5 }}>{children}</p>
  );
}

/** Everything that is only drawable once there is a connected, reconciled broker. */
function Live({
  status,
  broker,
  mark,
  tickSize,
  ticket,
  onTicket,
  onDone,
}: {
  status: RoutingStatus;
  broker: BrokerState;
  mark: number;
  tickSize: number;
  ticket: LiveTicket;
  onTicket: TicketEdit;
  onDone: () => void;
}) {
  const paper = broker.paper;
  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          fontSize: 11,
          color: palette.muted,
          margin: "6px 0 10px",
        }}
      >
        <InstrumentSwitch broker={broker} onDone={onDone} />
        <AccountSwitch broker={broker} onDone={onDone} />
        {!paper && <RefreshButton onDone={onDone} />}
      </div>

      {paper ? (
        // Everything below this point is about a broker, and paper has none.
        // The blotter, the position and the trades for paper are the page's
        // own — drawn on the chart and in the setup drawer, where they always
        // were. Saying so beats an empty panel that reads as "nothing working".
        <PaperNote broker={broker} />
      ) : broker.kind === null ? (
        <TagAccount broker={broker} onDone={onDone} />
      ) : !broker.ready ? (
        // Between choosing an account and the reconciliation landing. Short,
        // but it is the window in which the panel genuinely does not know what
        // is working, and saying "no orders" here would be a lie with a live
        // position behind it.
        <Note tone="orange">
          Reading the broker's state — what is working, what is held. Nothing can
          be sent until that has come back, because sending first would mean
          trading against a picture this process made up.
        </Note>
      ) : (
        <>
          <Position broker={broker} />
          <Guard guard={broker.guard} onDone={onDone} />
          <OneClick broker={broker} onDone={onDone} />
          <Ticket status={status} broker={broker} mark={mark}
                  tickSize={tickSize} ticket={ticket} onTicket={onTicket}
                  onDone={onDone} />
          <Working broker={broker} onDone={onDone} />
          <Flatten broker={broker} onDone={onDone} />
          <Recent broker={broker} />
        </>
      )}
    </>
  );
}

/** Paper is selected: say what that means and where its blotter is. */
function PaperNote({ broker }: { broker: BrokerState }) {
  const real = broker.accounts.filter((a) => a.id !== PAPER).length;
  return (
    <Note tone="muted">
      Every order gesture on the chart — <b>q/w/s</b>, the BUY/SELL dock,
      space+click, the long-press ticket — fills the paper blotter, and nothing
      here can reach a broker. Its trades are on the chart and its running total
      is behind the title, as always.
      {real > 0 && (
        <>
          {" "}
          Pick one of the {real} real account{real === 1 ? "" : "s"} above to
          send the same gestures to the exchange instead.
        </>
      )}
    </Note>
  );
}

/**
 * Labelling an account demo or live — what replaced `RITHMIC_ENV`.
 *
 * It has to be a person's declaration: Rithmic's account list carries an id, a
 * name, an FCM and a loss limit, and nothing at all about whether the account is
 * funded. The app cannot work it out, so it refuses to guess and asks once. The
 * label is then stored and shown as a badge everywhere the account appears,
 * which is the part an env var could never do.
 */
function TagAccount({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [e, setE] = useState<string | null>(null);
  const tag = async (kind: "demo" | "live") => {
    setBusy(kind);
    setE(null);
    try {
      await tagAccount(broker.account_id, kind);
      onDone();
    } catch (x) {
      setE(err(x));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 11, color: palette.muted, marginBottom: 8, lineHeight: 1.5 }}>
        <b>{broker.account_id}</b> has not been labelled. Nothing Rithmic sends
        says whether an account is funded, so this app will not guess — say which
        it is and it stays said. Nothing can be sent until you do.
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          disabled={busy != null}
          style={{ flex: 1, borderColor: palette.green, color: palette.green }}
          onClick={() => void tag("demo")}
        >
          {busy === "demo" ? "…" : "This is a DEMO account"}
        </button>
        <button
          type="button"
          disabled={busy != null}
          style={{ flex: 1, borderColor: palette.red, color: palette.red }}
          onClick={() => void tag("live")}
        >
          {busy === "live" ? "…" : "This is a LIVE account"}
        </button>
      </div>
      {e && <div style={{ fontSize: 11, color: palette.red, marginTop: 6 }}>⚠ {e}</div>}
    </div>
  );
}

/**
 * One-click trading, per account.
 *
 * The ATAS model: every order pops a confirm naming it in words, unless you have
 * decided this particular account does not need one. Off by default on both
 * demo and live, and **cleared server-side whenever an account is tagged live**
 * — the accident it guards is enabling it on practice and inheriting it on real
 * money, which no amount of care at the moment of clicking would catch.
 */
function OneClick({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  const on = broker.one_click;
  return (
    <div style={{ marginBottom: 8 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color: on ? palette.orange : palette.muted,
        }}
        title={
          on
            ? "Orders fire the moment you click or press a key — no confirmation. This is per account, and tagging an account live turns it off again."
            : "Every order pops a confirm naming side, size, price and account. Enter sends it, Esc cancels."
        }
      >
        <input
          type="checkbox"
          checked={on}
          disabled={busy}
          onChange={async (x) => {
            setBusy(true);
            setE(null);
            try {
              await setOneClick(broker.account_id, x.target.checked);
              onDone();
            } catch (v) {
              setE(err(v));
            } finally {
              setBusy(false);
            }
          }}
        />
        One-click trading{on ? " — no confirmation" : ""}
        {broker.kind === "live" && on && <b style={{ color: palette.red }}> · LIVE</b>}
      </label>
      {e && <div style={{ fontSize: 11, color: palette.red }}>⚠ {e}</div>}
    </div>
  );
}

/**
 * Which contract the orders go to — not which one is on screen.
 *
 * The tape cannot follow this: one Rithmic login is one socket and the
 * subscription was made at connect. So this is the deliberate version of the
 * hazard `Guards.max_risk_usd` was written against — "the chart is NQ but the
 * plan was written for MNQ" — turned into a choice, with the mismatch drawn
 * rather than left to be remembered. NQ and MNQ track the same index to within
 * a tick, which is what makes practising a micro plan on the mini's tape
 * honest; a *limit* price dragged off that tape is still an NQ price, and it is
 * sent as typed.
 *
 * The whole control disappears when there is nothing to choose between, which
 * is every session on a login without micro entitlement. A select with one
 * option is a question with one answer.
 */
function InstrumentSwitch({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  const perTick = broker.tick_size * broker.point_value;
  const away = broker.symbol !== broker.feed_symbol;

  if (broker.instruments.length < 2) return <span>{broker.symbol}</span>;

  return (
    <>
      <select
        value={broker.symbol}
        disabled={busy}
        title={
          "Which contract orders are sent to. The chart stays on " +
          `${broker.feed_symbol} either way — the tape is one subscription, made ` +
          "when the feed connected. Switching drops anything reviewed, and is " +
          "refused while anything is working or held."
        }
        onChange={async (x) => {
          setBusy(true);
          setE(null);
          try {
            await setBrokerInstrument(x.target.value);
            onDone();
          } catch (v) {
            setE(err(v));
          } finally {
            setBusy(false);
          }
        }}
        style={{ fontSize: 11, maxWidth: 120, ...(away ? { borderColor: palette.orange } : null) }}
      >
        {broker.instruments.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {/* The money, because that is the only reason to touch this control. */}
      <span title="Dollars per tick on the routed contract. Every bracket on this panel is measured in ticks, so this is what turns the geometry into risk.">
        ${perTick % 1 === 0 ? perTick.toFixed(0) : perTick.toFixed(2)}/tick
      </span>
      {away && (
        <span
          style={{ color: palette.orange }}
          title={
            `Orders go to ${broker.symbol}; the chart and the paper blotter are ` +
            `${broker.feed_symbol}. The two track within a tick, so the geometry ` +
            "carries over — but a limit price read off this chart is the other " +
            "contract's price, sent as typed."
          }
        >
          ⚠ chart is {broker.feed_symbol}
        </span>
      )}
      {busy && <span>switching…</span>}
      {e && <span style={{ color: palette.red }}>⚠ {e}</span>}
    </>
  );
}

/**
 * The account selector — paper and the real ones in one list.
 *
 * One selector rather than a mode switch beside an account picker, which is the
 * whole point of this design: there is always an active account, "which one"
 * has a single answer, and the thing that cannot trade sits first in the list.
 *
 * Switching **re-reads**, so it is not a display filter — it is a different
 * balance, and the panel refuses to send anything until the new account's
 * working orders and position have come back.
 */
function AccountSwitch({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  return (
    <>
      <select
        value={broker.account_id}
        disabled={busy}
        title={
          "Which account the chart's gestures go to. Switching re-reads and " +
          "drops anything reviewed: the working list belongs to one account and " +
          "so did the sentence you were shown."
        }
        onChange={async (x) => {
          setBusy(true);
          setE(null);
          try {
            await setBrokerAccount(x.target.value);
            onDone();
          } catch (v) {
            setE(err(v));
          } finally {
            setBusy(false);
          }
        }}
        style={{ fontSize: 11, maxWidth: 190 }}
      >
        {broker.accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.id === PAPER ? "📝 Paper" : a.label}
            {a.kind && a.kind !== PAPER ? `  [${a.kind}]` : ""}
            {!a.tagged ? "  [tag me]" : ""}
          </option>
        ))}
      </select>
      {busy && <span>switching…</span>}
      {e && <span style={{ color: palette.red }}>⚠ {e}</span>}
    </>
  );
}

function RefreshButton({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        disabled={busy}
        style={{ fontSize: 10, padding: "0 6px" }}
        // Available on any account. Reading the truth is never the dangerous
        // operation.
        title="Re-ask the broker what is working and what is held"
        onClick={async () => {
          setBusy(true);
          setE(null);
          try {
            await refreshBroker();
            onDone();
          } catch (x) {
            setE(err(x));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "…" : "re-read"}
      </button>
      {e && <span style={{ color: palette.red }}> ⚠ {e}</span>}
    </>
  );
}

/** What the broker says is held. Never derived from anything this page did. */
function Position({ broker }: { broker: BrokerState }) {
  const p = broker.position;
  const flat = !p || p.net === 0;
  return (
    <div
      style={{
        border: `1px solid ${palette.cardBorder}`,
        borderRadius: 4,
        padding: "6px 8px",
        marginBottom: 10,
        fontSize: 12,
      }}
    >
      <div style={{ fontSize: 10, color: palette.muted, letterSpacing: 0.4 }}>
        BROKER POSITION
      </div>
      {flat ? (
        <div style={{ color: palette.muted }}>
          flat
          {broker.reconciled_at == null && " — not yet read back"}
        </div>
      ) : (
        <div>
          <strong style={{ color: p!.net > 0 ? palette.green : palette.red }}>
            {p!.net > 0 ? "LONG" : "SHORT"} {Math.abs(p!.net)}
          </strong>
          {p!.avg_price != null && ` @ ${fmtPts(p!.avg_price)}`}
          {p!.open_pnl != null && (
            <span style={{ color: p!.open_pnl >= 0 ? palette.green : palette.red }}>
              {" · open "}
              {fmtUsd(p!.open_pnl)}
            </span>
          )}
          {p!.day_pnl != null && (
            <span style={{ color: palette.muted }}> · day {fmtUsd(p!.day_pnl)}</span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The discipline layer, and today against it.
 *
 * Drawn in every state including the one where the rules are switched off —
 * *especially* that one. A safety rail that is silently disabled is worse than
 * one that was never built, because it gets traded as though it were there.
 *
 * The levels are printed rather than named. "Daily stop" tells you a rule
 * exists; "−$500" is the thing you can hold a decision against, and these
 * numbers are fitted to this book (docs/research/lucidpro-operating-plan.md)
 * rather than borrowed, so the reader has to be able to see which ones.
 *
 * Two day figures, on purpose. `realized` is what the server paired out of the
 * fill stream and is what the rules are enforced on; the broker's `day_pnl` is
 * what the *account* did, which includes anything traded elsewhere against the
 * same login. A gap between them is shown rather than reconciled — it means one
 * of the two is missing trades, and quietly picking a winner would hide that.
 */
function Guard({ guard: g, onDone }: { guard: GuardState; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const lv = g.levels;
  const tone = !g.on
    ? palette.red
    : g.locked
      ? palette.red
      : g.slow
        ? palette.orange
        : palette.muted;
  const diverged = g.divergence != null && Math.abs(g.divergence) >= 1;

  return (
    <div
      style={{
        border: `1px solid ${g.on ? (g.locked || g.slow ? tone : palette.cardBorder) : palette.red}`,
        borderRadius: 4,
        padding: "6px 8px",
        marginBottom: 10,
        fontSize: 12,
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: g.on ? palette.muted : palette.red,
          letterSpacing: 0.4,
        }}
      >
        {g.on ? "DAY · GUARDED" : "DAY · GUARDRAILS OFF"}
      </div>

      {!g.on ? (
        <Note tone="red">
          <b>LIVE_GUARDRAILS is switched off.</b> The daily stop, the slow-down
          threshold, the minimum target and the stop-width clamp are all
          unenforced — nothing below is being applied. Remove{" "}
          <code>LIVE_GUARDRAILS=0</code> from <code>.env</code> and restart to
          put them back.
        </Note>
      ) : (
        <div style={{ marginTop: 2 }}>
          <strong
            style={{
              color:
                g.realized > 0 ? palette.green : g.realized < 0 ? tone : palette.muted,
            }}
          >
            {fmtUsd(g.realized)}
          </strong>
          <span style={{ color: palette.muted }}>
            {" "}
            · {g.trades} trade{g.trades === 1 ? "" : "s"}
          </span>
          {/* Where the number came from, once any of it came off disk. Not a
              warning — a rebuilt total is the correct one, and the state worth
              being alarmed by is the opposite: $0 on an afternoon that has
              already traded, which is what this whole read-back exists to
              stop. */}
          {g.restored > 0 && (
            <span
              style={{ color: palette.muted }}
              title={
                `${g.restored} of these came back from the journal rather than from a fill ` +
                "this server watched — the process restarted mid-session. The day's total " +
                "and its lock are rebuilt from the booked trades, so the daily stop still " +
                "measures the whole day. The one thing that cannot survive a restart is the " +
                "slow-down gap, which starts over."
              }
            >
              {" "}
              · {g.restored} rebuilt
            </span>
          )}
          {/* What the stop actually fires on. Only worth drawing while
              something is open — flat, it is the same number as realised, and
              two identical figures side by side read as a bug. */}
          {g.open_pnl != null && g.open_pnl !== 0 && (
            <span
              style={{ color: g.equity < 0 ? tone : palette.green }}
              title="Realised plus the open position. The daily stop is measured on this, not on realised — a position held at −$800 has already spent the drawdown whether or not it has been booked."
            >
              {" "}
              · equity <b>{fmtUsd(g.equity)}</b>
            </span>
          )}
          {diverged && (
            <span
              style={{ color: palette.orange }}
              title={
                "The broker's day P&L and the total these rules are enforced on " +
                "disagree. One of them is missing trades — most likely something " +
                "was traded on this account outside this app, or a fill arrived " +
                "before the process attached. The rules follow the local number."
              }
            >
              {" "}
              · broker {fmtUsd(g.broker_day_pnl ?? 0)} ⚠
            </span>
          )}
        </div>
      )}

      {g.on && g.locked && (
        <Note tone="red">
          <b>The day is over</b> — {g.locked}. It stays over even if the running
          total comes back: "one more to get back to level" is the trade this
          rule exists to refuse. Closing orders and Flatten still work.
          {g.auto_flattened && (
            <>
              {" "}
              <b>The open position was closed automatically.</b> Check the
              platform that it landed — a partial failure is in the order
              journal, not on this line.
            </>
          )}
        </Note>
      )}

      {g.on && !g.locked && g.slow && (
        <Note tone="orange">
          <b>Slow down.</b> Past {fmtUsd(-lv.slow_down_at)}, entries go no closer
          than {Math.round(lv.min_gap_s)}s apart. A bad start slowed down on
          costs $147/day; the same start sped up on costs $803. Volume is not the
          problem — speed is.
        </Note>
      )}

      {g.on && (
        <button
          type="button"
          onClick={() => setEditing((v) => !v)}
          title="The levels these rules run on. Set any one to 0 to disable that rule; the master switch is LIVE_GUARDRAILS in .env."
          style={{
            fontSize: 10,
            color: palette.muted,
            marginTop: 4,
            lineHeight: 1.6,
            padding: 0,
            border: "none",
            background: "none",
            textAlign: "left",
            cursor: "pointer",
          }}
        >
          stop {fmtUsd(-lv.daily_loss_stop)}
          {lv.daily_profit_lock > 0 && ` · lock ${fmtUsd(lv.daily_profit_lock)}`}
          {lv.slow_down_at > 0 && ` · slow ${fmtUsd(-lv.slow_down_at)}`}
          {" · target ≥"}
          {lv.min_target_ticks}tk · stop {lv.stop_ticks_min}–{lv.stop_ticks_max}tk
          {lv.max_risk_usd > 0 && ` · risk ≤${fmtUsd(lv.max_risk_usd)}`}
          {lv.require_bracket && " · bracket"}
          {lv.auto_flatten && " · auto-flat"} {editing ? "▴" : "▾"}
        </button>
      )}

      {g.on && editing && <Levels levels={lv} onDone={onDone} />}
    </div>
  );
}

/** The guard levels, editable. **Not** a way to turn the layer off.
 *
 *  That is `LIVE_GUARDRAILS` in `.env`, and the split is the design: the levels
 *  are a decision about what the rules are, which is a fine thing to tune from
 *  the chart, and switching them off is a decision to trade without rules, which
 *  should require leaving it. Setting one level to 0 disables that rule alone —
 *  visibly, since the summary line above stops printing it.
 *
 *  Takes effect on the next order: an order is checked against the rules as
 *  they stand when it is sent.
 */
function Levels({ levels, onDone }: { levels: GuardLevels; onDone: () => void }) {
  const [draft, setDraft] = useState<GuardLevels>(levels);
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);

  const num = (
    key: keyof GuardLevels,
    label: string,
    hint: string,
    step = 1,
  ) => (
    <label
      key={key}
      title={hint}
      style={{ fontSize: 10, color: palette.muted, display: "grid", gap: 2 }}
    >
      {label}
      <input
        type="number"
        min={0}
        step={step}
        value={String(draft[key])}
        onChange={(x) =>
          setDraft((d) => ({ ...d, [key]: Math.max(0, Number(x.target.value) || 0) }))
        }
        style={{ width: 74, fontSize: 11 }}
      />
    </label>
  );

  return (
    <div style={{ marginTop: 6, display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {num("daily_loss_stop", "Day stop $", "Realised dollars down at which the day is over. Latches — a later winner does not reopen it. 0 disables.")}
        {num("daily_profit_lock", "Profit lock $", "Realised dollars up at which the day is over. 0 in evaluation (no consistency rule to poison); 1000 once funded.")}
        {num("slow_down_at", "Slow at $", "Dollars down at which entries have to space out. 0 disables.")}
        {num("min_gap_s", "Min gap s", "The floor on the gap between entries, once past the slow-down level.")}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {num("min_target_ticks", "Target ≥", "Refuse a target tighter than this. Every target at or under 80 ticks is net-negative on this book.")}
        {num("stop_ticks_min", "Stop ≥", "Tighter than this gets noise-stopped.")}
        {num("stop_ticks_max", "Stop ≤", "Wider than this is fewer losses until the account is over. Take fewer contracts instead.")}
        {num("max_risk_usd", "Risk max $", "The most one entry may risk: stop x size x the contract's dollars-per-tick. Catches the contract being wrong, which a quantity ceiling cannot.", 25)}
        {num("commission_per_side", "Comm/side $", "Per side, per contract. The day's total is measured net of it. $3.50 on a mini, ~$0.50 on a micro.", 0.5)}
      </div>
      <label style={{ fontSize: 10, color: palette.muted, display: "flex", gap: 6 }}>
        <input
          type="checkbox"
          checked={draft.require_bracket}
          onChange={(x) =>
            setDraft((d) => ({ ...d, require_bracket: x.target.checked }))
          }
        />
        Every entry carries a stop and a target
      </label>
      <label
        style={{ fontSize: 10, color: palette.muted, display: "flex", gap: 6 }}
        title="At the daily stop, close what is open rather than only refusing the next entry. Measured on equity, so an unbooked loss counts. Off, the day still locks — it just stops acting."
      >
        <input
          type="checkbox"
          checked={draft.auto_flatten}
          onChange={(x) =>
            setDraft((d) => ({ ...d, auto_flatten: x.target.checked }))
          }
        />
        Close the position at the daily stop
      </label>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setE(null);
            try {
              await saveRoutingSettings({ guards: draft });
              onDone();
            } catch (x) {
              setE(err(x));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "…" : "Save levels"}
        </button>
        <span style={{ fontSize: 10, color: palette.muted }}>
          takes effect on the next order · 0 disables a rule
        </span>
      </div>
      {e && <div style={{ fontSize: 11, color: palette.red }}>⚠ {e}</div>}
    </div>
  );
}

/**
 * The order pad, and the review that has to happen between it and the exchange.
 *
 * Two states, and they are exclusive on purpose: while an order is staged the
 * pad is replaced by the sentence, so there is no arrangement of this panel in
 * which a second order can be built beside one waiting to be sent.
 */
function Ticket({
  status,
  broker,
  mark,
  tickSize,
  ticket,
  onTicket,
  onDone,
}: {
  status: RoutingStatus;
  broker: BrokerState;
  mark: number;
  tickSize: number;
  ticket: LiveTicket;
  onTicket: TicketEdit;
  onDone: () => void;
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [kind, setKind] = useState<OrderKind>("market");
  const [price, setPrice] = useState("");
  const gl = status.guards;
  // **Size and the bracket are the page's, not this pad's.** They used to be
  // six `useState`s right here, seeded off the guard levels — which read well
  // and was wrong: the chart's gestures carry their own copy, so setting the
  // stop here and then placing with space+click sent the *other* copy, and
  // neither surface showed the number that actually went out. One ticket, held
  // by the page (lib/simPrefs, DEFAULT_LIVE_TICKET), edited from both.
  //
  // The guard levels are still drawn beside the inputs below — as the limits
  // they are, rather than as a default that silently overwrote a choice.
  const { size: qty, stopTicks, targetTicks, trailTicks, beTicks, beLock } = ticket;
  const setQty = (v: number) => onTicket("size", v);
  const setStopTicks = (v: number) => onTicket("stopTicks", v);
  const setTargetTicks = (v: number) => onTicket("targetTicks", v);
  const setTrailTicks = (v: number) => onTicket("trailTicks", v);
  const setBeTicks = (v: number) => onTicket("beTicks", v);
  const setBeLock = (v: number) => onTicket("beLock", v);
  const [staged, setStaged] = useState<Staged | null>(null);
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  // Pre-fill a resting price from the mark the first time a resting type is
  // chosen. Only when empty — overwriting a price somebody typed because a
  // tick arrived would be the panel editing an order under them.
  useEffect(() => {
    if (kind !== "market" && !price && Number.isFinite(mark)) setPrice(String(mark));
  }, [kind, mark, price]);

  const stagedFor = useStagedCountdown(staged);
  useEffect(() => {
    // A review that has run out is dropped rather than left greyed: the server
    // has already forgotten the token, and an order whose price is a minute old
    // is a different order. Re-review it.
    if (staged && stagedFor <= 0) {
      setStaged(null);
      setE("that review expired before it was sent — the price has moved, review it again");
    }
  }, [staged, stagedFor]);

  if (staged) {
    return (
      <div
        style={{
          border: `1px solid ${palette.orange}`,
          borderRadius: 4,
          padding: "8px",
          marginBottom: 10,
        }}
      >
        <div style={{ fontSize: 10, letterSpacing: 0.4, color: palette.orange }}>
          CONFIRM — THIS SENDS A REAL ORDER
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.5, margin: "6px 0" }}>{staged.sentence}</p>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button type="button" disabled={busy} onClick={() => setStaged(null)}>
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            style={{ borderColor: palette.orange, color: palette.orange }}
            onClick={async () => {
              setBusy(true);
              setE(null);
              try {
                const r = await sendOrder(staged.token);
                setStaged(null);
                setSent(`sent · ${r.basket_id || r.tag}`);
                onDone();
              } catch (x) {
                setE(err(x));
                // The token is spent whatever happened, so the review is gone
                // either way. Clearing it stops a second click from posting a
                // token the server has already consumed and reading the 409 as
                // a new failure.
                setStaged(null);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Sending…" : "Send order"}
          </button>
          <span style={{ fontSize: 11, color: palette.muted }}>
            expires in {mmss(stagedFor)}
          </span>
        </div>
        {e && <div style={{ fontSize: 11, color: palette.red, marginTop: 6 }}>⚠ {e}</div>}
      </div>
    );
  }

  const bad = kind !== "market" && !(Number(price) > 0);

  return (
    <div
      style={{
        border: `1px solid ${palette.cardBorder}`,
        borderRadius: 4,
        padding: "8px",
        marginBottom: 10,
        display: "grid",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", gap: 6 }}>
        {(["buy", "sell"] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSide(s)}
            aria-pressed={side === s}
            style={{
              flex: 1,
              fontSize: 12,
              color: side === s ? (s === "buy" ? palette.green : palette.red) : palette.muted,
              borderColor: side === s ? (s === "buy" ? palette.green : palette.red) : undefined,
            }}
          >
            {s.toUpperCase()}
          </button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <label style={{ fontSize: 11, color: palette.muted, display: "grid", gap: 2 }}>
          Qty (max {status.max_qty})
          <input
            type="number"
            min={1}
            max={status.max_qty}
            value={qty}
            onChange={(x) => setQty(Math.max(1, Number(x.target.value) || 1))}
            style={{ width: 62 }}
          />
        </label>
        <label style={{ fontSize: 11, color: palette.muted, display: "grid", gap: 2 }}>
          Type
          <select
            value={kind}
            onChange={(x) => setKind(x.target.value as OrderKind)}
            style={{ width: 84 }}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        {kind !== "market" && (
          <label style={{ fontSize: 11, color: palette.muted, display: "grid", gap: 2 }}>
            Price
            <input
              value={price}
              onChange={(x) => setPrice(x.target.value)}
              style={{ width: 84 }}
            />
          </label>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <label style={{ fontSize: 11, color: palette.muted, display: "grid", gap: 2 }}>
          Stop (ticks)
          {status.guardrails && gl.stop_ticks_max > 0 && (
            <span style={{ fontSize: 9 }}>
              {gl.stop_ticks_min}–{gl.stop_ticks_max}
            </span>
          )}
          <input
            type="number"
            min={0}
            value={stopTicks}
            onChange={(x) => setStopTicks(Math.max(0, Number(x.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        <label style={{ fontSize: 11, color: palette.muted, display: "grid", gap: 2 }}>
          Target (ticks)
          {status.guardrails && gl.min_target_ticks > 0 && (
            <span style={{ fontSize: 9 }}>≥ {gl.min_target_ticks}</span>
          )}
          <input
            type="number"
            min={0}
            value={targetTicks}
            onChange={(x) => setTargetTicks(Math.max(0, Number(x.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        {/* One number, because Rithmic's trail has one free variable. The ride
            distance is the stop above — measured on MNQU6, a 50-tick stop put
            the first rung 50 ticks under the high — so all that is left to
            choose is how far in profit it wakes up. Disabled without a stop
            rather than hidden: the reason is worth reading once. */}
        <label
          style={{
            fontSize: 11,
            color: trailTicks ? palette.orange : palette.muted,
            display: "grid",
            gap: 2,
            opacity: stopTicks ? 1 : 0.5,
          }}
          title={
            stopTicks
              ? `Rithmic ratchets the stop up behind the high once the trade is this ` +
                `far in profit, riding ${stopTicks} ticks back. It moves the stop ` +
                `itself — nothing here has to stay open for it to work, and it ` +
                `survives a reload. 0 is off.`
              : "Needs a stop: the trail rides at the stop's own distance behind the high, so there is nothing to measure from without one."
          }
        >
          Trail after (t)
          <input
            type="number"
            min={0}
            disabled={!stopTicks}
            value={trailTicks}
            onChange={(x) => setTrailTicks(Math.max(0, Number(x.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        {/* Fires once and stops, where the trail keeps going — a separate
            mechanism, not a mode of the one beside it. */}
        <label
          style={{
            fontSize: 11,
            color: beTicks && stopTicks ? palette.orange : palette.muted,
            display: "grid",
            gap: 2,
            opacity: stopTicks ? 1 : 0.5,
          }}
          title={
            stopTicks
              ? "Once the trade is this far in profit, Rithmic jumps the stop to lock the amount beside it in. Fires once. 0 is off."
              : "Needs a stop: there is no leg to jump without one."
          }
        >
          Breakeven after (t)
          <input
            type="number"
            min={0}
            disabled={!stopTicks}
            value={beTicks}
            onChange={(x) => setBeTicks(Math.max(0, Number(x.target.value) || 0))}
            style={{ width: 72 }}
          />
        </label>
        {beTicks > 0 && stopTicks > 0 && (
          <label
            style={{ fontSize: 11, color: palette.orange, display: "grid", gap: 2 }}
            title={
              "How much profit that jump locks in, in your favour on either " +
              "side. At least 1 tick — Rithmic cannot be told 'exactly at the " +
              "fill', since a zero is a protobuf default that never reaches it."
            }
          >
            …locking (t)
            <input
              type="number"
              min={1}
              value={beLock}
              onChange={(x) => setBeLock(Math.max(1, Number(x.target.value) || 1))}
              style={{ width: 72 }}
            />
          </label>
        )}
        <span style={{ fontSize: 10, color: palette.muted, alignSelf: "end", paddingBottom: 4 }}>
          {stopTicks ? `${(stopTicks * tickSize).toFixed(2)} pts` : "no stop"} ·{" "}
          {targetTicks ? `${(targetTicks * tickSize).toFixed(2)} pts` : "no target"}
          {trailTicks > 0 && stopTicks > 0 && (
            <>
              {" "}
              · <b style={{ color: palette.orange }}>trails</b>
            </>
          )}
        </span>
      </div>
      <button
        type="button"
        disabled={busy || bad}
        style={broker.one_click ? { borderColor: palette.orange, color: palette.orange } : undefined}
        onClick={async () => {
          setBusy(true);
          setE(null);
          setSent(null);
          const draft = {
            side,
            qty,
            type: kind,
            price: kind === "market" ? null : Number(price),
            stop_ticks: stopTicks,
            target_ticks: targetTicks,
            // Belt and braces against the server's own refusals: a trail with
            // no stop has nothing to ride at, a lock with no trigger never
            // fires, and a zero lock never reaches Rithmic at all. Sending any
            // of them would be asking for a 422 the inputs already prevent.
            trail_trigger_ticks: stopTicks ? trailTicks : 0,
            be_trigger_ticks: stopTicks ? beTicks : 0,
            be_ticks: stopTicks && beTicks ? Math.max(1, beLock) : 0,
          };
          try {
            // The pad obeys the same one-click setting the chart gestures do —
            // it would be strange for the panel to insist on a review the
            // keyboard is allowed to skip, and stranger still to have two
            // answers to "does this account confirm".
            if (broker.one_click) {
              const r = await sendOrderNow(draft);
              setSent(`sent · ${r.basket_id || r.tag}`);
              onDone();
            } else {
              setStaged({ ...(await previewOrder(draft)), at: Date.now() });
            }
          } catch (x) {
            setE(err(x));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "…" : broker.one_click ? `Send ${side.toUpperCase()} now` : "Review order"}
      </button>
      {e && <div style={{ fontSize: 11, color: palette.red }}>⚠ {e}</div>}
      {sent && <div style={{ fontSize: 11, color: palette.green }}>{sent}</div>}
    </div>
  );
}

/** A staged review, with the instant it was staged at.
 *
 *  The timestamp is the client's, and it only drives the countdown — the server
 *  holds the real deadline and refuses on its own clock, so a drifting browser
 *  fails safe (a refusal on send) rather than the other way. */
type Staged = OrderPreview & { at: number };

/** Seconds left on a staged review, ticked locally. This is what the deadline
 *  looks like, never what enforces it — the server holds its own clock. */
function useStagedCountdown(staged: Staged | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!staged) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [staged]);
  if (!staged) return 0;
  // `now` can lag `staged.at` on the first render after staging, which reads as
  // *more* time than there is. Harmless in that direction and the reason the
  // expiry watcher below cannot fire early.
  return staged.expires_in_s - (now - staged.at) / 1000;
}

function Working({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [e, setE] = useState<string | null>(null);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: palette.muted, letterSpacing: 0.4 }}>
        WORKING AT THE BROKER
      </div>
      {broker.reconciled_at == null ? (
        // Not the same as an empty list, and drawn differently on purpose.
        <div style={{ fontSize: 11, color: palette.orange }}>not read back</div>
      ) : broker.working.length === 0 ? (
        <div style={{ fontSize: 11, color: palette.muted }}>nothing working</div>
      ) : (
        broker.working.map((o) => (
          <div
            key={o.basket_id}
            style={{
              display: "flex",
              gap: 6,
              alignItems: "baseline",
              fontSize: 11,
              borderTop: `1px solid ${palette.cardBorder}`,
              padding: "4px 0",
            }}
          >
            <OrderLine o={o} />
            <button
              type="button"
              style={{ marginLeft: "auto", fontSize: 10, padding: "0 6px" }}
              disabled={busy === o.basket_id}
              title="Cancel this order"
              onClick={async () => {
                setBusy(o.basket_id);
                setE(null);
                try {
                  await cancelBrokerOrder(o.basket_id);
                  onDone();
                } catch (x) {
                  setE(err(x));
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === o.basket_id ? "…" : "cancel"}
            </button>
          </div>
        ))
      )}
      {e && <div style={{ fontSize: 11, color: palette.red }}>⚠ {e}</div>}
    </div>
  );
}

function OrderLine({ o }: { o: BrokerOrder }) {
  // `||`, not `??`: the price field that does not apply to an order's kind
  // arrives as Rithmic's 0.0 rather than absent, and a stop rendered "@ 0.00" is
  // this row lying about where it sits. See `restingPx` in lib/brokerViews.
  const px = o.price || o.trigger_price || null;
  // Same class of thing on the size: some notifications leave `quantity` at 0
  // and describe the order only by what has filled and what has not.
  const qty = o.qty || o.filled + o.unfilled;
  return (
    <span>
      <b style={{ color: o.side === "buy" ? palette.green : palette.red }}>
        {o.side.toUpperCase()}
      </b>{" "}
      {qty} {o.type}
      {px != null && ` @ ${fmtPts(px)}`}
      {o.filled > 0 && (
        <span style={{ color: palette.muted }}>
          {" "}
          · {o.filled}/{qty} filled
        </span>
      )}
      {o.status && <span style={{ color: palette.muted }}> · {o.status}</span>}
      {o.text && <span style={{ color: palette.orange }}> · {o.text}</span>}
    </span>
  );
}

/**
 * The kill switch.
 *
 * Enabled whenever there is a connection and gated on nothing else, because the
 * situation you most want it in is the one where the page was just reloaded or
 * something is misbehaving. It cancels first and exits second: exiting under a working
 * bracket can leave the bracket to open a fresh position the other way when it
 * triggers. A partial failure says which half did not land rather than reporting
 * the half that did.
 */
function Flatten({ broker, onDone }: { broker: BrokerState; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const nothing = broker.working.length === 0 && (broker.position?.net ?? 0) === 0;
  return (
    <div style={{ marginBottom: 10 }}>
      <button
        type="button"
        disabled={busy || !broker.attached}
        style={{
          width: "100%",
          borderColor: palette.red,
          color: palette.red,
          opacity: nothing ? 0.6 : 1,
        }}
        title="Cancel everything working and exit the position. Gated on nothing but the connection."
        onClick={async () => {
          setBusy(true);
          setE(null);
          setOk(false);
          try {
            await flattenAll();
            setOk(true);
            onDone();
          } catch (x) {
            setE(err(x));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Flattening…" : "FLATTEN ALL"}
      </button>
      {ok && <div style={{ fontSize: 11, color: palette.muted }}>sent — re-read to confirm</div>}
      {e && (
        <div style={{ fontSize: 11, color: palette.red }}>
          ⚠ {e} — check the platform directly
        </div>
      )}
    </div>
  );
}

function Recent({ broker }: { broker: BrokerState }) {
  if (broker.recent.length === 0) return null;
  return (
    <details style={{ fontSize: 11, color: palette.muted }}>
      <summary>{broker.recent.length} finished</summary>
      {broker.recent.map((o, i) => (
        <div key={`${o.basket_id}-${i}`} style={{ padding: "2px 0" }}>
          <OrderLine o={o} />
        </div>
      ))}
    </details>
  );
}
