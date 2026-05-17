// Tiny number formatters shared across the wizard and (later) the review UI.

export function formatSeconds(sec: number): string {
  if (!Number.isFinite(sec)) return "—";
  const total = Math.max(0, sec);
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  if (m === 0) return `${s.toFixed(2)}s`;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

export function formatMs(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  return `${Math.round(ms)} ms`;
}

export function formatPercent(num: number, denom: number): string {
  if (!denom) return "0%";
  return `${Math.round((100 * num) / denom)}%`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
