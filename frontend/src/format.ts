// Plain formatting helpers shared by every page: no markup, no styles.
export const words = (s: string) => s.replaceAll("_", " ");
/** What to do with a file a resume box can't take: most people's resume is a Word file, and "wrong file type" helps nobody. */
export const resumeFileRefusal = (file: File) =>
  /\.(docx?|pages|odt|rtf|txt)$/i.test(file.name)
    ? `AXIOM needs a PDF of your resume. Open ${file.name} and choose File, then Save As or Export, then PDF, and add that file.`
    : `${file.name} isn't a PDF. Add your resume as a PDF.`;
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
export function seconds(total: number) {
  const s = Math.max(0, Math.round(total));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

const PLATFORMS: Record<string, string> = {
  adp: "ADP",
  ashby: "Ashby",
  custom: "Company site",
  dayforce: "Dayforce",
  eightfold: "Eightfold",
  general: "Company site",
  greenhouse: "Greenhouse",
  icims: "iCIMS",
  jazzhr: "JazzHR",
  lever: "Lever",
  oracle: "Oracle",
  paycom: "Paycom",
  paylocity: "Paylocity",
  smartrecruiters: "SmartRecruiters",
  successfactors: "SAP SuccessFactors",
  taleo: "Taleo",
  ukg: "UKG",
  workable: "Workable",
  workday: "Workday",
};
/** A platform's own name: "icims" reads as iCIMS. */
export function platformName(ats?: string) {
  const id = (ats || "").toLowerCase();
  return PLATFORMS[id] || id.charAt(0).toUpperCase() + id.slice(1).replaceAll("_", " ") || "Unknown";
}

// A readable role for a job saved as a bare link: the last wordy path segment ("Exton-Associate%2C-Data-Science" ->
// "Exton Associate, Data Science"), never a 300-character query string in the table (owner 2026-10-28: "cluttered").
export function urlRole(url?: string) {
  try {
    const u = new URL(url || "");
    const generic = /^(?:jobs?|careers?|details?|apply|applications?|en|us|en-us|postings?|positions?|openings?|view|search|requisitions?|reqs?|vacanc(?:y|ies)|careersite|ux|ats|home|jobdetail)$/i;
    // A shared board's first segment is the employer, not the role.
    const board = /greenhouse\.io$|lever\.co$|ashbyhq\.com$/.test(u.hostname) ? u.pathname.split("/").filter(Boolean)[0]?.toLowerCase() : "";
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
      .find((s) => /[a-z]{3,}/i.test(s) && !generic.test(s) && s.toLowerCase() !== board && !/^[0-9a-f-]{16,}$/i.test(s));
    const title = (segment || u.searchParams.get("title") || "")
      .replace(/[-_+]+/g, " ")
      .replace(/\b(?=\w*\d)[0-9a-z]{8,}\b/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    return title;
  } catch {
    return "";
  }
}

const ROLE_UNREAD = "Role not read yet";

/**
 * What a list shows for a job: the employer, the role, and, only when either was not read from the posting, the posting's own host as
 * a muted hint. A link saved before ranking has neither a company nor a title; the address stands in for them and says so.
 */
export function jobWords(j: { company?: string; title?: string; url?: string }) {
  const company = employerOf(j.company, j.url);
  const read = j.title || urlRole(j.url);
  return { company, url: j.url, role: read || ROLE_UNREAD, unread: !read, hint: !j.company || !read ? (linkHost(j.url).split(" · ").pop() ?? "") : "" };
}

/** Who a job is with, for a list: its company, or for a job saved as a bare link, the employer its address names ("Blossom Health"). */
export function employerOf(company?: string, url?: string) {
  if (company) return company;
  const name = linkHost(url).split(" · ")[0] ?? "";
  if (!name) return "Employer";
  // A plain host ("careers.example.com") names its employer by the label before the suffix ("Example"); the host itself stays
  // available as the hint (jobWords).
  const label = name.includes(".") ? hostLabel(name) : name;
  return label.replace(/[-_]+/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

// "careers.example.com" -> "example", "jobs.example.co.uk" -> "example": the label left of the registrable suffix.
function hostLabel(host: string) {
  const parts = host.split(".");
  const second = /^(?:co|com|org|net|gov|edu|ac)$/.test(parts[parts.length - 2] ?? "") && (parts[parts.length - 1]?.length ?? 0) === 2;
  return parts[Math.max(0, parts.length - (second ? 3 : 2))] || host;
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

// What a stored reason says to a person. The engine writes its own bookkeeping into them: who approved a job ("Owner approved: ",
// "Applicant approved: Owner 10-29 night goal: ..."), why it was let past the fit score ("Admitted by title (admit_by_title setting):
// ...") and eligibility checks as "Explicit hiring restriction | Eligibility check: citizenship: "quote"". A person reads the
// sentence and, for a restriction, which rule it was.
const RESTRICTIONS: Record<string, string> = {
  citizenship: "The posting requires U.S. citizenship.",
  security_clearance: "The posting requires a security clearance.",
  visa_status: "The posting does not accept your work authorization.",
};
export function plainReason(text?: string | null) {
  let s = (text || "").trim();
  if (!s) return "";
  const check = s.match(/Eligibility check:\s*(\w+)/);
  if (/^Explicit hiring restriction/i.test(s) && check) return RESTRICTIONS[check[1]!] || "The posting has a hiring restriction you do not meet.";
  // The engine's notes about its own browser name source files and internal steps (vocabulary.py says the same).
  if (/^(Browser control failed|Browser observation stopped)/i.test(s)) return "The browser stopped responding on this page.";
  if (/^(Open the ApplyPilot Chrome|The local Chrome debugging|Chrome worker tab)/i.test(s)) return "AXIOM's browser wasn't ready, so this application didn't start.";
  s = s.replace(/^Owner approved:\s*/i, "").replace(/^Applicant approved:[^:]*:\s*/i, "");
  s = s.replace(/^Admitted by title \(admit_by_title setting\):\s*/i, "");
  // The engine's own words for its own trouble: jobpilot/vocabulary.py _ENGINE says the same.
  if (/\.py:\d+/.test(s)) return "AXIOM got stuck on this site's pages.";
  if (/^Worker interrupted/i.test(s)) return "This attempt was interrupted before anything was sent.";
  if (/^Worker error\b/i.test(s)) return "AXIOM hit a problem of its own.";
  if (/debugging endpoint|automation connection|Chromium (?:missing|did not|exited)/i.test(s)) return "AXIOM couldn't use its browser.";
  if (/^An explicit hiring restriction conflicts with confirmed applicant fact/i.test(s)) return "The posting has a hiring restriction you do not meet.";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The score of a listing, or null when it has none worth showing: a ruled-out job is stored with a placeholder 0, not a fit. */
export function shownFit(j: { score?: number | null; reason?: string | null }) {
  if (j.score == null) return null;
  return j.score === 0 && /hiring restriction/i.test(j.reason || "") ? null : j.score;
}

/** When a listing was last put in front of us. A date inferred from a feed is the day it was found, not the employer's date. */
export function listedWhen(j: { posted_at?: string; date_basis?: string }, now: number) {
  const basis = j.date_basis || "";
  // "added": a job with no listing, dated by when it was saved. "inferred": a feed gave no date, so it is the day it was found.
  const found = /inferred/i.test(basis) || basis === "added";
  const note = /inferred/i.test(basis) ? ". The employer gave no posting date." : basis === "added" ? ". It has no posting date." : "";
  return { text: (found ? "seen " : "") + ago(j.posted_at, now), title: `${found ? "Found" : basis || "Posted"} ${stamp(j.posted_at)}${note}` };
}
