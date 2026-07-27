// Small formatting helpers shared across the Security Center pages.
// Kept separate from lib/api.ts / lib/security-api.ts, which are
// fetch-layer modules with no rendering concerns.

export function formatUnixSeconds(value: number | undefined): string {
  if (!value) {
    return "n/a";
  }
  return new Date(value * 1000).toLocaleString();
}

export function formatTimestamp(value: string | undefined): string {
  if (!value) {
    return "n/a";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function formatLagSeconds(value: number | undefined): string {
  if (value === undefined) {
    return "n/a";
  }
  if (value < 60) {
    return `${Math.round(value)}s ago`;
  }
  if (value < 3_600) {
    return `${Math.round(value / 60)}m ago`;
  }
  return `${(value / 3_600).toFixed(1)}h ago`;
}

export function formatBoolean(value: boolean): string {
  return value ? "yes" : "no";
}
