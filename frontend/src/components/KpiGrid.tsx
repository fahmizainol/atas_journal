import { KpiCard, type Card } from "./KpiCard";

// Mirrors ui.render_cards: a CSS-grid row of cards with an explicit
// grid-template-columns string (e.g. "1.5fr 1fr 1fr 1fr").
export function KpiGrid({
  cards,
  template,
  className,
}: {
  cards: Card[];
  template: string;
  className?: string;
}) {
  return (
    <div
      className={`kpi-grid${className ? ` ${className}` : ""}`}
      style={{ gridTemplateColumns: template }}
    >
      {cards.map((c, i) => (
        <KpiCard key={i} {...c} />
      ))}
    </div>
  );
}
