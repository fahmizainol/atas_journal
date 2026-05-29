import { palette } from "../../theme";

type Tone = "pos" | "neg" | "neutral";

const COLOR: Record<Tone, string> = {
  pos: palette.green,
  neg: palette.red,
  neutral: palette.text,
};

// Mirrors app._ai_bullets: a small captioned list, color-coded by tone.
export function AiBullets({
  title,
  items,
  tone = "neutral",
}: {
  title: string;
  items?: string[] | string;
  tone?: Tone;
}) {
  if (!items || (Array.isArray(items) && items.length === 0)) return null;
  const list = Array.isArray(items) ? items : [items];
  return (
    <div style={{ marginTop: 8 }}>
      <div className="section-cap">{title}</div>
      <ul style={{ marginTop: 2, color: COLOR[tone] }}>
        {list.map((i, idx) => (
          <li key={idx}>{i}</li>
        ))}
      </ul>
    </div>
  );
}
