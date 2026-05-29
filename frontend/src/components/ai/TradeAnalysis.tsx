import { useState } from "react";
import { useTradeAi, useGenTradeAi, type TradeAnalysis as TA } from "../../hooks/useAi";
import { useMeta } from "../../hooks/useMeta";
import type { FilterScope } from "../../lib/queryKeys";
import { KpiGrid } from "../KpiGrid";
import { ModelPicker } from "./ModelPicker";
import { AiBullets } from "./AiBullets";
import type { Tone } from "../../theme";

const GRADE_TONE: Record<string, Tone> = { A: "pos", B: "pos", C: "neutral", D: "neg", F: "neg" };

function Analysis({ data }: { data: TA }) {
  if (data.error) return <div className="notice warn">AI error: {data.error}</div>;
  const grade = (data.grade ?? "").trim().toUpperCase().slice(0, 1);
  return (
    <div>
      <KpiGrid
        cards={[
          { label: "Verdict", value: data.verdict ?? "—", hero: true },
          ...(grade
            ? [{ label: "Grade", value: grade, tone: GRADE_TONE[grade] ?? "neutral" }]
            : []),
        ]}
        template={grade ? "3fr 1fr" : "1fr"}
      />
      <AiBullets title="What went well" items={data.went_well} tone="pos" />
      <AiBullets title="What went wrong" items={data.went_wrong} tone="neg" />
      <AiBullets title="Suggestion" items={data.suggestion} />
    </div>
  );
}

export function TradeAnalysis({
  tradeKey,
  scope,
  hasExcursion,
}: {
  tradeKey: string;
  scope: FilterScope;
  hasExcursion: boolean;
}) {
  const { data: meta } = useMeta();
  const { data } = useTradeAi(tradeKey);
  const gen = useGenTradeAi(tradeKey, scope);
  const [view, setView] = useState<string | null>(null);

  if (!meta?.ai_available)
    return (
      <div className="notice">
        Set LLM_MODEL / LLM_MODELS (and provider keys) in .env to enable AI analysis.
      </div>
    );
  if (!hasExcursion)
    return (
      <div className="notice">
        AI critique needs Databento excursion data (MAE/MFE). Unavailable for this trade.
      </div>
    );

  const analyses = data?.analyses ?? {};
  const names = Object.keys(analyses);
  const chosen = view && names.includes(view) ? view : names[0];

  return (
    <div className="panel">
      <div className="section-title">AI analysis</div>
      <ModelPicker models={meta.models} pending={gen.isPending} onGenerate={(m) => gen.mutate(m)} />
      {gen.data?.error && <div className="notice warn">AI error: {gen.data.error}</div>}
      {names.length === 0 ? (
        <div className="muted">No analyses yet — pick a model and click Analyze.</div>
      ) : (
        <>
          {names.length > 1 && (
            <div className="radio-group" style={{ marginBottom: 8 }}>
              {names.map((n) => (
                <button key={n} className={n === chosen ? "active" : ""} onClick={() => setView(n)}>
                  {n}
                </button>
              ))}
            </div>
          )}
          <div className="section-cap">
            {chosen} · saved {analyses[chosen!].created_at}
          </div>
          <Analysis data={analyses[chosen!].analysis} />
        </>
      )}
    </div>
  );
}
