export function LevelsToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="radio-group" style={{ marginBottom: 10 }}>
      <button className={value ? "active" : ""} onClick={() => onChange(!value)}>
        Levels
      </button>
    </div>
  );
}
