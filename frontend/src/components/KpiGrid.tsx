import { KpiCard, type Card } from "./KpiCard";

// Mirrors ui.render_cards: a CSS-grid row of cards with an explicit
// grid-template-columns string (e.g. "1.5fr 1fr 1fr 1fr").
export function KpiGrid({ cards, template }: { cards: Card[]; template: string }) {
  return (
    <div className="kpi-grid" style={{ gridTemplateColumns: template }}>
      {cards.map((c, i) => (
        <KpiCard key={i} {...c} />
      ))}
    </div>
  );
}
