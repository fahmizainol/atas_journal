// The visual strategy description shown in place of the prose `description`: a
// lead line, a Visual/Definition toggle, a rule rail (Setup/Side/Entry/Stop/
// Target chips), and the annotated teaching diagrams. "Definition" flips back to
// the original prose so nothing is lost — it's demoted, not deleted. Driven
// entirely by a StrategyExplainer spec (lib/strategyExplainers.ts); a strategy
// without a spec never renders this and keeps its plain description.
import { useState } from "react";
import type { StrategyExplainer as ExplainerSpec, ExFigure } from "../../lib/strategyExplainers";
import { StrategyExplainerChart } from "./StrategyExplainerChart";

function Figure({ fig }: { fig: ExFigure }) {
  return (
    <figure className="se-fig">
      <div className="se-fig-head">
        <span className="se-fig-num">{fig.num}</span>
        <span className="se-fig-title">{fig.title}</span>
      </div>
      {fig.layout === "split" ? (
        <div className="se-split">
          {(fig.panels ?? []).map((p, k) => (
            <div className="se-sub" key={k}>
              <div className="se-sub-cap">
                <span className={`se-verdict ${p.verdict}`}>{p.verdictLabel}</span>
                {p.note}
              </div>
              <StrategyExplainerChart spec={p.chart} ariaLabel={p.ariaLabel} />
            </div>
          ))}
        </div>
      ) : (
        <div className="se-card-chart">
          {fig.chart && <StrategyExplainerChart spec={fig.chart} ariaLabel={fig.ariaLabel} />}
        </div>
      )}
      <figcaption className="se-fig-cap">{fig.caption}</figcaption>
    </figure>
  );
}

export function StrategyExplainer({ spec, description }: { spec: ExplainerSpec; description: string }) {
  const [view, setView] = useState<"visual" | "text">("visual");
  return (
    <div className="se">
      <p className="se-subtitle">{spec.subtitle}</p>

      <div className="se-toggle" role="tablist" aria-label="Description view">
        <button
          type="button"
          role="tab"
          aria-selected={view === "visual"}
          onClick={() => setView("visual")}
        >
          Visual
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "text"}
          onClick={() => setView("text")}
        >
          Definition
        </button>
      </div>

      {view === "visual" ? (
        <>
          <div className="se-rail">
            {spec.chips.map((chip) => (
              <div className="se-rule" key={chip.key}>
                <div className="k">
                  <span className="dot" style={{ background: chip.dot }} />
                  {chip.key}
                </div>
                <div className="v">
                  {chip.main}
                  {chip.em && <em> — {chip.em}</em>}
                </div>
              </div>
            ))}
          </div>
          <div className="se-figs">
            {spec.figures.map((fig) => (
              <Figure fig={fig} key={fig.num} />
            ))}
          </div>
        </>
      ) : (
        <div className="se-def">
          <p>{description}</p>
        </div>
      )}
    </div>
  );
}
