import { useEffect, useState } from "react";
import { useTradingProfile, useSaveProfile } from "../hooks/useSettings";
import { PeriodReview } from "../components/ai/PeriodReview";

function ProfileEditor() {
  const { data } = useTradingProfile();
  const save = useSaveProfile();
  const [text, setText] = useState("");
  useEffect(() => {
    if (data) setText(data.profile);
  }, [data]);

  return (
    <details className="panel" open={!data?.profile}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        My trading rules / style (used to ground every AI analysis)
      </summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(text);
        }}
        style={{ marginTop: 10 }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          style={{ width: "100%" }}
          placeholder="e.g. NQ scalper, max 2 contracts, cut losers fast, no trading after 2 losing trades in a day."
        />
        <div style={{ marginTop: 8 }}>
          <button className="btn-accent" type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save profile"}
          </button>
          {save.isSuccess && <span className="pos" style={{ marginLeft: 10 }}>Profile saved.</span>}
        </div>
      </form>
    </details>
  );
}

export function AiReview() {
  return (
    <div>
      <div className="section-title">AI period review</div>
      <div className="section-cap">
        Reviews the currently-filtered trades — adjust the filter bar above for a weekly / monthly /
        per-instrument review.
      </div>
      <ProfileEditor />
      <PeriodReview />
    </div>
  );
}
