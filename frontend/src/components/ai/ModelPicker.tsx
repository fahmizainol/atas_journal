import { useState } from "react";

// Model dropdown + generate button (shared by per-trade and period reviews).
export function ModelPicker({
  models,
  pending,
  onGenerate,
  label = "Analyze",
}: {
  models: string[];
  pending: boolean;
  onGenerate: (model: string) => void;
  label?: string;
}) {
  const [model, setModel] = useState(models[0] ?? "");
  if (models.length === 0) return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-end", margin: "8px 0" }}>
      <div className="field" style={{ flex: 1, maxWidth: 360 }}>
        <label>Model</label>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      <button className="btn-accent" disabled={pending} onClick={() => onGenerate(model)}>
        {pending ? "Analyzing…" : label}
      </button>
    </div>
  );
}
