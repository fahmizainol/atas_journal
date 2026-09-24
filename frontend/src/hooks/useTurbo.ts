// Hold Ctrl to fast-forward the tape.
//
// The speed ladder is a setting you land on once (`[`/`]`, or the transport's
// <select>), and it is the speed you want to *watch* at. Skipping the dead
// twenty minutes between one setup and the next is a different gesture: you
// want it while you want it and then you want your speed back. A ladder step
// can't do that — you would have to walk up and then remember to walk down —
// so this is a hold, not a mode, and there is nothing to leave switched on.
//
// Ctrl alone, deliberately unmodified and never preventDefault'ed: the browser's
// own Ctrl chords have to keep working, and this reads the modifier rather than
// claiming the key.
//
// The multiplier is not clamped to the ladder's top. 300× is the fastest you can
// read; this is the speed at which you are explicitly not reading.

import { useEffect, useRef, useState, type MutableRefObject } from "react";

/** What Ctrl is worth. */
export const TURBO_MULT = 10;

export interface Turbo {
  /** 10 while Ctrl is held, 1 otherwise. A ref because the frame loop reads it
   *  sixty times a second — a state read would need the loop re-created to see
   *  a change, and the loop is a `useCallback` the rAF chain holds by identity. */
  readonly mult: MutableRefObject<number>;
  /** The same fact, for the transport to show. One re-render per press, on
   *  pages that already re-render ten times a second while playing. */
  readonly on: boolean;
}

/**
 * Ctrl-held state, for a replay transport to multiply its speed by.
 *
 * Inert on a live tape without asking: `liveSource.clockFor` ignores the speed
 * argument entirely, so a multiplied speed is still the last print.
 */
export function useTurbo(): Turbo {
  const mult = useRef(1);
  const [on, setOn] = useState(false);

  useEffect(() => {
    const set = (held: boolean) => {
      if ((mult.current > 1) === held) return;
      mult.current = held ? TURBO_MULT : 1;
      setOn(held);
    };
    // Every key event carries the modifier state, so any key resyncs — including
    // Ctrl's own: `ctrlKey` is true on its keydown and false on its keyup.
    const onKey = (e: KeyboardEvent) => set(e.ctrlKey);
    // Ctrl+Tab, Ctrl+click on a link, alt-tab: the keyup lands in a window that
    // isn't this one and never arrives, which would leave the tape stuck at 10×
    // with no key to press to get it back.
    const off = () => set(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", off);
    document.addEventListener("visibilitychange", off);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", off);
      document.removeEventListener("visibilitychange", off);
    };
  }, []);

  return { mult, on };
}
