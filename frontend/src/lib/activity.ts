// How much time this app actually gets, measured in minutes of use.
//
// The tape already answers "how much market have I sat through" exactly —
// every attempt in data/replays stores its own clock, and summing them is a
// query, not a measurement. What none of it records is wall-clock: how long a
// sitting took, and how much of the week goes to reading statistics rather than
// trading a session. That has to be captured as it happens, and only from the
// day it is switched on; the stored attempts cannot be mined for it, because
// their `updated_at` is a save stamp that keeps moving when a status is patched
// days later.
//
// A HEARTBEAT, NOT PAGEVIEWS. PostHog derives session duration from the gap
// between a session's first and last event, which works for a site you click
// through and fails badly here: sit on /charts/replay for an hour without
// navigating and pageview-only capture records one event, so the hour reads as
// zero. Emitting a fixed-interval beat instead makes the arithmetic trivial in
// the other direction — one event is one minute, so a count IS a duration, and
// a breakdown by `section` is the same count per part of the app. No
// sessionisation, no gap-capping, nothing to get wrong in a dashboard query.
//
// WHAT LEAVES THE MACHINE. Nothing else in this frontend calls a third-party
// host, and that property is worth keeping deliberate rather than losing by
// accident. Autocapture is off because it records the text of whatever you
// click, which in this app is P&L, account balances and trade dates; session
// recording is off because it would ship the DOM those live in. What goes out
// is the beat, the workspace and the tab path — no numbers, no dates, no
// identifiers. With no key set, posthog-js is never even imported.
//
// Events are anonymous on purpose. The total is a count, so it adds up across
// devices with no identity to wire — the phone on the tailnet and the desktop
// contribute to the same number. The one cost is that using both at once
// double-counts those minutes, which is the honest tradeoff for having no
// person profile at all.

import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import type { PostHog } from "posthog-js";
import { tabForPath } from "./workspaces";

// Unset (the default, and what a fresh clone gets) disables the whole module.
const KEY = import.meta.env.VITE_POSTHOG_KEY;
const HOST = import.meta.env.VITE_POSTHOG_HOST || "https://us.i.posthog.com";

/** One beat, one minute. Changing this silently rescales every historical
 *  number, since the stored events carry no duration of their own — treat it as
 *  fixed, and if it ever has to move, move it on a date you write down. */
const PING_MS = 60_000;

/** How long after your last keystroke or mouse move the app stops counting.
 *  A visible tab is not the same as a tab being used: /charts/live sits open on
 *  a second monitor for the whole session, and without any bound it would bill
 *  the entire day as time spent.
 *
 *  Thirty minutes is set for watching rather than clicking — a session spent
 *  reading the tape without touching anything is time spent, and a five-minute
 *  bound scored most of it as zero. The cost is the other direction: walking
 *  away from a visible tab now bills up to half an hour before it stops. That
 *  is the deliberate trade — this errs toward counting attention it cannot see
 *  rather than discarding it. */
const IDLE_MS = 30 * 60_000;

const INPUT_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "scroll"] as const;

// Loaded once, on the first beat, so posthog-js stays out of the entry bundle
// and off the critical path of a page that draws a chart.
let client: Promise<PostHog | null> | null = null;

/** When the last beat went out, module-scoped so it outlives any one mount.
 *
 *  "One event is one minute" is the arithmetic the whole dashboard rests on, and
 *  until this existed nothing enforced it — the hook beats on mount, and React
 *  mounts effects twice under StrictMode, so every page load in dev recorded two
 *  minutes 50ms apart. That is not a test-only artifact here: the tailnet URL
 *  proxies to the dev server, so dev IS the runtime. Rate-limiting at the source
 *  makes the invariant true for any double-mount, present or future, rather than
 *  leaving it as a property of how the effect happens to be written. */
let lastBeatAt = 0;

function load(): Promise<PostHog | null> {
  if (!KEY) return Promise.resolve(null);
  const key = KEY;
  client ??= import("posthog-js").then(
    ({ default: posthog }) => {
      posthog.init(key, {
        api_host: HOST,
        // See the header: this is the whole privacy posture, in five options.
        autocapture: false,
        disable_session_recording: true,
        capture_pageview: false,
        capture_pageleave: false,
        person_profiles: "never",
        // The four above are client-side answers to client-side questions. This
        // one answers the server: posthog asks the project what else to switch
        // on and then fetches that code from its CDN — surveys, dead-click
        // capture and exception autocapture all arrived unasked-for on the first
        // real connection, because they are on by default in a new project.
        // Turning them off in the dashboard would work until someone turned one
        // back on; refusing to load remote code at all is the version that stays
        // true. Remote config still resolves, over JSON instead of a script tag.
        disable_external_dependency_loading: true,
      });
      return posthog;
    },
    (err) => {
      // Forget the failure. `??=` caches whatever this returns for the life of
      // the page, and a cached rejection is permanent in a way the failure
      // itself is not: one blocked request or one bad chunk and every later beat
      // would await the same dead promise, so the tab would go silent until it
      // was reloaded — indistinguishable, from the dashboard, from not having
      // used the app. Clearing it costs a retry a minute later.
      client = null;
      console.warn("usage heartbeat: posthog-js failed to load", err);
      return null;
    },
  );
  return client;
}

/** Beat once a minute while the app is visible and being used.
 *
 *  Mounted once, in the shell — every route renders inside it, including the
 *  chart pages that draw no other chrome. */
export function useActivityPing(): void {
  const { pathname } = useLocation();
  // The beat reads the route through a ref so navigating never restarts the
  // interval: a 60s timer reset on every click would beat only for people who
  // sit still, which is the opposite of what it is measuring.
  const path = useRef(pathname);
  useEffect(() => {
    path.current = pathname;
  }, [pathname]);

  useEffect(() => {
    if (!KEY) return;

    // Seeded at mount rather than zero — you clicked something to get here, and
    // seeding it idle would discard the whole first window of every visit.
    let lastInput = Date.now();
    const touch = () => {
      lastInput = Date.now();
    };
    for (const e of INPUT_EVENTS) window.addEventListener(e, touch, { passive: true });

    const beat = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastInput > IDLE_MS) return;
      // Half an interval of slack: wide enough to swallow a double-mount, and
      // never wide enough to swallow a real beat, since setInterval fires at or
      // after its delay and so can only ever be late.
      if (now - lastBeatAt < PING_MS / 2) return;
      lastBeatAt = now;
      const here = tabForPath(path.current);
      void load().then((ph) =>
        ph?.capture("heartbeat", {
          workspace: here?.ws.id ?? "other",
          section: here?.tab.to ?? path.current,
        }),
      );
    };
    beat();
    const timer = window.setInterval(beat, PING_MS);

    return () => {
      window.clearInterval(timer);
      for (const e of INPUT_EVENTS) window.removeEventListener(e, touch);
    };
  }, []);
}
