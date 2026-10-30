// Plain formatting helpers shared by every page: no markup, no styles.
export const words = (s: string) => s.replaceAll("_", " ");
export const shortId = (id: string) => id.slice(0, 8);
export const plural = (n: number, one: string, many = one + "s") =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function ago(value?: string | number | null, now = Date.now()) {
  if (!value) return "—";
  const t = typeof value === "number" ? value : new Date(value).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return day(value);
}
export function day(value?: string | number | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year:
      new Date(value).getFullYear() === new Date().getFullYear()
        ? undefined
        : "numeric",
  });
}
export function stamp(value?: string | number | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
export function clock(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}
export function elapsed(from?: string | null, now = Date.now()) {
  if (!from) return "";
  return seconds((now - new Date(from).getTime()) / 1000);
}
export function seconds(total: number) {
  const s = Math.max(0, Math.round(total));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
export const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** "examplecorp · boards.greenhouse.io" for a pasted link whose title isn't known until ranking. */
// A readable role for a job saved as a bare link: the last wordy path segment ("Exton-Associate%2C-Data-Science" ->
// "Exton Associate, Data Science"), never a 300-character query string in the table (owner 2026-10-28: "cluttered").
export function urlRole(url?: string) {
  try {
    const u = new URL(url || "");
    const generic = /^(?:jobs?|careers?|details?|apply|en|us|en-us|posting|positions?|openings?|view|search)$/i;
    const segment = u.pathname
      .split("/")
      .filter(Boolean)
      .map((s) => {
        try {
          return decodeURIComponent(decodeURIComponent(s));
        } catch {
          return s;
        }
      })
      .reverse()
      .find((s) => /[a-z]{3,}/i.test(s) && !generic.test(s) && !/^[0-9a-f-]{16,}$/i.test(s));
    const title = (segment || u.searchParams.get("title") || "")
      .replace(/[-_+]+/g, " ")
      .replace(/\b(?=\w*\d)[0-9a-z]{8,}\b/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    return title || "Untitled role";
  } catch {
    return "Untitled role";
  }
}

export function linkHost(url?: string) {
  try {
    const u = new URL(url || "");
    const first = u.pathname.split("/").filter(Boolean)[0] || "";
    const host = u.hostname.replace(/^www\./, "");
    const tenant = host.split(".")[0];
    // Shared ATS hosts name the employer in the path or the subdomain.
    if (/greenhouse\.io$|lever\.co$|ashbyhq\.com$/.test(host) && first)
      return `${first} · ${host}`;
    if (/myworkdayjobs\.com$|icims\.com$|oraclecloud\.com$/.test(host))
      return `${tenant} · ${host.split(".").slice(1).join(".")}`;
    return host;
  } catch {
    return "";
  }
}
