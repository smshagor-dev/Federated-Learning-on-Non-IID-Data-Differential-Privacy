export function MetricCard({
  label,
  value,
  caption,
}: {
  label: string;
  value: string;
  caption: string;
}) {
  return (
    <article className="card alt">
      <div className="eyebrow">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="muted">{caption}</div>
    </article>
  );
}
