import { useState } from "react";
import { useFilters } from "../../hooks/useFilters";
import { useMeta } from "../../hooks/useMeta";
import { usePeriodAi, useGenPeriodAi, type PeriodReview as PR } from "../../hooks/useAi";
import { KpiGrid } from "../KpiGrid";
import { ModelPicker } from "./ModelPicker";
import { AiBullets } from "./AiBullets";

function Review({ data }: { data: PR }) {
  if (data.error) return <div className="notice warn">AI error: {data.error}</div>;
  return (
    <div>
      {data.summary && (
        <KpiGrid cards={[{ label: "Summary", value: data.summary, hero: true }]} template="1fr" />
      )}
      <AiBullets title="Strengths" items={data.strengths} tone="pos" />
      <AiBullets title="Leaks" items={data.leaks} tone="neg" />
      <AiBullets title="Recommendations" items={data.recommendations} />
    </div>
  );
}

export function PeriodReview() {
  const { scope } = useFilters();
  const { data: meta } = useMeta();
  const { data } = usePeriodAi(scope);
  const gen = useGenPeriodAi(scope);
  const [view, setView] = useState<string | null>(null);

  if (!meta?.ai_available)
    return (
      <div className="notice">
        Set LLM_MODEL / LLM_MODELS (and provider keys) in .env to enable AI review.
      </div>
    );

  const reviews = data?.reviews ?? {};
  const names = Object.keys(reviews);
  const chosen = view && names.includes(view) ? view : names[0];

  return (
    <div className="panel">
      <ModelPicker
        models={meta.models}
        pending={gen.isPending}
        onGenerate={(m) => gen.mutate(m)}
        label="Generate"
      />
      {gen.data?.error && <div className="notice warn">AI error: {gen.data.error}</div>}
      {names.length === 0 ? (
        <div className="muted">No reviews yet — pick a model and click Generate.</div>
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
          {reviews[chosen!].stale && (
            <div className="notice warn">
              New trades were imported into this scope since this review — regenerate to refresh it.
            </div>
          )}
          <div className="section-cap">
            {chosen} · saved {reviews[chosen!].created_at} · {reviews[chosen!].trade_count} trades
          </div>
          <Review data={reviews[chosen!].review} />
        </>
      )}
    </div>
  );
}
