const TFS = ["1m", "5m", "15m"] as const;

export function TimeframeRadio({ value, onChange }: { value: string; onChange: (tf: string) => void }) {
  return (
    <div className="radio-group" style={{ marginBottom: 10 }}>
      {TFS.map((tf) => (
        <button key={tf} className={value === tf ? "active" : ""} onClick={() => onChange(tf)}>
          {tf}
        </button>
      ))}
    </div>
  );
}
