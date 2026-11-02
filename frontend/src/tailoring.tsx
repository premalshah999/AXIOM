// Apply, or Apply with a tailored resume. TailorChoice is the small control that goes wherever a job is queued; TailorView is the screen where a tailored resume
// is read, edited and approved, for any mode the server has registered (jobpilot/features/resumes/modes.py). The score and the highlights
// are worked out here on each keystroke from the patterns the server sends, with no request and no model, and the server gets the same number on save (ats.py).
import { ArrowLeft, RotateCcw } from "lucide-react";
import { isDeveloperText, plainError } from "./format";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, usePoll } from "./hooks";
import { PdfPages, usePdf } from "./pdfpages";
import { SwitchRow } from "./setup-kit";
import { Button, Loading, Panel, Pill } from "./ui/index";
import { AggressiveDrafts } from "./aggressive";
import { rowFetchesItself } from "./tailorbatch";
import "./styles/tailoring.css";

// ---------- What the server sends ----------
type Bullet = { id: string; text: string; unit_id?: string | null; source_text?: string; include: boolean };
type Entry = { id: string; heading: string; subheading: string; dates: string; location: string; bullets: Bullet[]; include: boolean };
type Section = { id: string; title: string; kind: string; entries: Entry[]; text: string; include: boolean };
type Doc = { name: string; contact: string[]; sections: Section[] };
type Keyword = { term: string; tier: "required" | "duties" | "preferred"; weight: number; pattern: string; status: string; in_evidence: boolean };
type Score = { score: number | null; keywords: Keyword[]; method: string; disclaimer: string };
type Problem = { code: string; where: string; message: string };
export type Mode = { id: string; label: string; note: string; available: boolean };
export type TailorState = { mode: string; state: "preparing" | "review" | "approved" | "failed" };
type View = {
  job: { id: string; company: string; title: string; location: string; url: string; description: string; status: string };
  resume_name: string | null;
  modes: Mode[];
  approval_required: boolean;
  choice: (TailorState & { error: { code?: string; detail?: string }; artifact_id: string | null; approved_artifact_id: string | null; approved_at: string | null; approved_how: string | null }) | null;
  stale: boolean;
  approval_block: { code: string; message: string } | null;
  project_changes_pending: boolean;
  artifact: { id: string; pdf_sha: string; model_calls: number; cost: number; finished_at: string; policy_version: string } | null;
  source_doc: Doc | null;
  doc: Doc | null;
  ats: Score;
  ats_source: number | null;
  ats_pdf: number | null;
  rules: { ok: boolean; problems: Problem[]; warnings: Problem[]; typography?: { ok: boolean; sizes: Record<string, number[]> } | null; pages?: number | null } | null;
  rule_problems: Problem[];
  parse: { ok: boolean; items: { label: string; ok: boolean; detail: string }[] } | null;
  report: { workspace_id?: string; changed_bullets?: number; added_skills?: string[]; refused?: { id: string; reason: string }[]; overflow?: boolean; summary_origin?: string };
  preview_rules: View["rules"];
  drafts: { id: string; title: string; bullets: string[]; confirmed: boolean }[];
  pages: number | null;
  cost: { usd: number; calls: number; prompt_tokens: number; completion_tokens: number } | null;
  estimates: Record<string, number>;
  removed_projects: string[];
};

const path = (jobId: string) => `/v2/resumes/tailor/job/${encodeURIComponent(jobId)}`;
const cents = (usd: number | null | undefined) => {
  if (usd == null) return "";
  const c = usd * 100;
  if (c < 0.01) return "under 0.01 cents";
  return `${c < 1 ? c.toFixed(2) : c.toFixed(1)} cents`;
};

// ---------- The same sums as ats.py ----------
function compile(pattern: string, flags = "i") {
  try {
    return new RegExp(pattern, flags);
  } catch {
    return null;
  }
}

/** The resume as a parser reads it: rules.doc_plain, here so each keystroke needs no request. */
export function docPlain(doc: Doc) {
  const parts: string[] = [doc.name, ...doc.contact];
  for (const s of doc.sections) {
    if (!s.include) continue;
    parts.push(s.title, s.text);
    for (const e of s.entries) {
      if (!e.include) continue;
      parts.push(e.heading, e.subheading, e.dates, e.location);
      for (const b of e.bullets) if (b.include && b.text.trim()) parts.push(b.text);
    }
  }
  return parts.filter(Boolean).join("\n");
}

export function liveScore(keywords: Keyword[], text: string) {
  let found = 0;
  let total = 0;
  const tiers = { required: { found: 0, total: 0 }, duties: { found: 0, total: 0 }, preferred: { found: 0, total: 0 } };
  const rows = keywords.map((k) => {
    const hit = !!compile(k.pattern)?.test(text);
    total += k.weight;
    if (hit) found += k.weight;
    tiers[k.tier].total += 1;
    if (hit) tiers[k.tier].found += 1;
    return { ...k, status: hit ? "matched" : k.in_evidence ? "available" : "needed" };
  });
  return { score: total ? Math.round((100 * found) / total) : null, tiers, rows };
}

const MARK: Record<string, string> = { matched: "tl-hit", available: "tl-have", needed: "tl-need" };

/** Text with each keyword marked. Where two overlap, the longer one wins. */
function Marked({ text, keywords }: { text: string; keywords: { pattern: string; status: string }[] }) {
  const parts = useMemo(() => {
    const spans: { from: number; to: number; status: string }[] = [];
    for (const k of keywords) {
      const rx = compile(k.pattern, "gi");
      if (!rx) continue;
      for (const m of text.matchAll(rx)) if (m[0]) spans.push({ from: m.index, to: m.index + m[0].length, status: k.status });
    }
    spans.sort((a, b) => a.from - b.from || b.to - a.to);
    const out: ReactNode[] = [];
    let at = 0;
    spans.forEach((s, i) => {
      if (s.from < at) return;
      if (s.from > at) out.push(text.slice(at, s.from));
      out.push(
        <mark key={i} className={MARK[s.status]}>
          {text.slice(s.from, s.to)}
        </mark>,
      );
      at = s.to;
    });
    out.push(text.slice(at));
    return out;
  }, [text, keywords]);
  return <>{parts}</>;
}

/** A plain text box with marks drawn behind its words, so what you type is highlighted as you type it. */
function MarkedArea({ value, onChange, keywords, label, invalid, onBlur }: { value: string; onChange: (v: string) => void; keywords: Keyword[]; label: string; invalid?: boolean; onBlur?: () => void }) {
  return (
    <div className={"tl-area" + (invalid ? " is-invalid" : "")}>
      <div className="tl-area-marks" aria-hidden="true">
        <Marked text={value} keywords={keywords} />
        {"\n"}
      </div>
      <textarea aria-label={label} aria-invalid={invalid || undefined} value={value} rows={1} spellCheck onBlur={onBlur} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

// ---------- The difference from the source ----------
function words(s: string) {
  return s.split(/(\s+)/).filter(Boolean);
}

/** A word diff, longest common subsequence; bullets are short. */
function Diff({ from, to }: { from: string; to: string }) {
  const a = words(from);
  const b = words(to);
  const dp = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
  const out: ReactNode[] = [];
  let i = 0;
  let j = 0;
  let k = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push(a[i]);
      i++;
      j++;
    } else if (i < a.length && (j >= b.length || dp[i + 1]![j]! >= dp[i]![j + 1]!)) {
      out.push(<del key={k++}>{a[i]}</del>);
      i++;
    } else {
      out.push(<ins key={k++}>{b[j]}</ins>);
      j++;
    }
  }
  return <span className="tl-diff">{out}</span>;
}

// ---------- The small control, for wherever a job is queued ----------
/**
 * "Apply" or "Apply with tailored resume" and a mode. Off by default. On a list, pass `known` and `modes` from one GET /v2/resumes/tailor/choices?job_ids=... so
 * that each row doesn't ask for itself; alone, it reads for itself. `onChange` hears the state, so a row can react.
 * `listed`: the list owns the state (it fetches once for the page and polls the rows that are preparing), so this row never asks and shows what it is given.
 */
export function TailorChoice({ jobId, known, modes, compact, listed, onChange }: { jobId: string; known?: TailorState | null; modes?: Mode[]; compact?: boolean; listed?: boolean; onChange?: (s: TailorState | null) => void }) {
  const { go } = useWorkspace();
  const [own, setOwn] = useState<{ choice: TailorState | null; modes: Mode[]; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const choice = listed ? known ?? null : known !== undefined && !own ? known : own?.choice ?? null;
  const list = !listed && own?.modes?.length ? own.modes : modes;
  const load = useRef(async () => {
    const r = await api<{ choices: Record<string, TailorState>; modes: Mode[] }>(`/v2/resumes/tailor/choices?job_ids=${encodeURIComponent(jobId)}`);
    setOwn((was) => ({ ...was, choice: r.choices[jobId] ?? null, modes: r.modes }));
    onChange?.(r.choices[jobId] ?? null);
  });
  const preparing = choice?.state === "preparing";
  useEffect(() => {
    if (rowFetchesItself(!!listed, known, modes)) void load.current().catch(() => undefined);
  }, [known, modes, listed]);
  useEffect(() => {
    if (!preparing || listed) return;
    const t = setInterval(() => void load.current().catch(() => undefined), 2500);
    return () => clearInterval(t);
  }, [preparing, listed]);
  const pick = async (mode: string) => {
    setBusy(true);
    try {
      const r = await api<{ choice: TailorState | null }>(path(jobId), "PUT", { mode });
      const now = r.choice ? { mode: r.choice.mode, state: r.choice.state } : null;
      if (!listed) setOwn((was) => ({ modes: was?.modes ?? list ?? [], choice: now }));
      onChange?.(now);
    } catch (e) {
      setOwn((was) => ({ modes: was?.modes ?? list ?? [], choice, error: e instanceof Error ? plainError(e.message) : "That didn't save." }));
    } finally {
      setBusy(false);
    }
  };
  const open = () => go({ view: "tailor", tailor: jobId, job: undefined, run: undefined });
  return (
    <div className={"tl-choice" + (compact ? " is-compact" : "")}>
      <label className="tl-choice-pick">
        {!compact && <span className="ax-label">How to apply</span>}
        <select
          className="ax-control ax-input"
          aria-label={compact ? "How to apply" : undefined}
          value={choice?.mode || "off"}
          disabled={busy || !list}
          onChange={(e) => void pick(e.target.value)}
        >
          <option value="off">Apply</option>
          {(list || []).map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>
              {`Apply with tailored resume: ${m.label}${m.available ? "" : " (coming soon)"}`}
            </option>
          ))}
        </select>
      </label>
      {choice && (
        <span className="tl-choice-state">
          {choice.state === "preparing" && <Pill tone="info" dot>Tailoring</Pill>}
          {choice.state === "review" && <Pill tone="warn" dot>Needs your review</Pill>}
          {choice.state === "approved" && <Pill tone="ok" dot>Ready to send</Pill>}
          {choice.state === "failed" && <Pill tone="bad" dot>Couldn't tailor</Pill>}
          <a
            className="ax-link"
            href={routeUrl({ view: "tailor", tailor: jobId })}
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey) return;
              e.preventDefault();
              open();
            }}
          >
            {choice.state === "review" ? "Review" : "Open"}
          </a>
        </span>
      )}
      {own?.error && <p className="ax-error" role="alert">{own.error}</p>}
    </div>
  );
}

/** Choosing before a job exists, as when adding a link: the same options as TailorChoice, held by the caller. */
export function HowToApply({ value, onChange, disabled }: { value: string; onChange: (mode: string) => void; disabled?: boolean }) {
  const modes = usePoll<{ modes: Mode[] }>((signal) => api("/v2/resumes/tailor/modes", "GET", undefined, signal), 120000);
  return (
    <label className="tl-choice-pick">
      <span className="ax-label">How to apply</span>
      <select className="ax-control ax-input" value={value} disabled={disabled || !modes.data} onChange={(e) => onChange(e.target.value)}>
        <option value="off">Apply</option>
        {(modes.data?.modes || []).map((m) => (
          <option key={m.id} value={m.id} disabled={!m.available}>
            {`Apply with tailored resume: ${m.label}${m.available ? "" : " (coming soon)"}`}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The Settings switch. On by default. */
export function TailorApprovalRow() {
  const settings = usePoll<{ approval: boolean }>((signal) => api("/v2/resumes/tailor/settings", "GET", undefined, signal), 60000);
  const { pending, run } = useAction();
  const on = settings.data?.approval ?? true;
  return (
    <SwitchRow
      label="Review tailored resumes before they're sent"
      note="On: a tailored resume waits for your approval. Off: it goes out as soon as it's made and has passed the resume checks."
      checked={on}
      disabled={pending !== null || !settings.data}
      onChange={() =>
        run(
          "approval",
          async () => {
            await api("/v2/resumes/tailor/settings", "PUT", { approval: !on });
            await settings.refresh();
          },
          on ? "Tailored resumes will go out without waiting for you." : "Tailored resumes will wait for your approval.",
        )
      }
    />
  );
}

// ---------- The screen ----------
function useBack() {
  const { go } = useWorkspace();
  return () => go({ view: "queue" });
}

export function TailorView() {
  const { route } = useWorkspace();
  const jobId = route.tailor;
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const refresh = useRef(async () => undefined as void);
  refresh.current = async () => {
    if (!jobId) return;
    try {
      setView(await api<View>(path(jobId)));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? plainError(e.message) : "This job couldn't be opened.");
    }
  };
  useEffect(() => {
    setView(null);
    void refresh.current();
  }, [jobId]);
  const preparing = view?.choice?.state === "preparing";
  useEffect(() => {
    if (!preparing) return;
    const t = setInterval(() => void refresh.current(), 2000);
    return () => clearInterval(t);
  }, [preparing]);
  const back = useBack();
  if (!jobId) return <div className="ax-page"><p className="ax-note">Open a job and choose Apply with tailored resume.</p></div>;
  if (error && !view) return <div className="ax-page"><p className="ax-error" role="alert">{error}</p></div>;
  if (!view) return <div className="ax-page"><Loading /></div>;
  return (
    <div className="ax-page tl-page">
      <header className="tl-head">
        <div className="tl-head-main">
          <button type="button" className="ax-link tl-back" onClick={back}>
            <ArrowLeft size={14} aria-hidden="true" /> Queue
          </button>
          <h2>
            {view.job.title || "Untitled role"}
            {view.job.company ? <span> at {view.job.company}</span> : null}
          </h2>
        </div>
        <ModePicker view={view} onChanged={() => void refresh.current()} />
      </header>
      {view.choice?.state === "preparing" ? (
        <p className="tl-working" role="status">
          Tailoring your resume{view.estimates[view.choice.mode] != null ? `. About ${cents(view.estimates[view.choice.mode])} in AI costs.` : "."}
        </p>
      ) : view.choice?.state === "failed" ? (
        <Failed view={view} onChanged={() => void refresh.current()} />
      ) : view.doc && view.source_doc && view.artifact ? (
        <Review key={view.artifact.id} view={view} onView={setView} onChanged={() => void refresh.current()} />
      ) : (
        <NotChosen view={view} />
      )}
    </div>
  );
}

function ModePicker({ view, onChanged }: { view: View; onChanged: () => void }) {
  const { pending, run } = useAction();
  const mode = view.choice?.mode || "off";
  const note = view.modes.find((m) => m.id === mode)?.note;
  return (
    <div className="tl-mode">
      <label>
        <span className="ax-label">How to apply</span>
        <select
          className="ax-control ax-input"
          value={mode}
          disabled={pending !== null}
          onChange={(e) =>
            run("mode", async () => {
              await api(path(view.job.id), "PUT", { mode: e.target.value });
              onChanged();
            })
          }
        >
          <option value="off">Apply with {view.resume_name ? `"${view.resume_name}"` : "my uploaded resume"}</option>
          {view.modes.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>
              {`Apply with tailored resume: ${m.label}${m.available ? "" : " (coming soon)"}`}
            </option>
          ))}
        </select>
      </label>
      <p className="ax-hint">
        {note || "Your uploaded resume, unchanged."}
        {mode !== "off" && view.estimates[mode] != null ? ` About ${cents(view.estimates[mode])} in AI costs.` : ""}
      </p>
    </div>
  );
}

function Failed({ view, onChanged }: { view: View; onChanged: () => void }) {
  const { pending, run } = useAction();
  const detail = view.rule_problems[0]?.message || (view.choice?.error?.detail && !isDeveloperText(view.choice.error.detail) ? view.choice.error.detail : "") || "Something went wrong.";
  return (
    <div className="tl-failed" role="alert">
      <p>
        <strong>The tailored resume couldn't be made.</strong> {detail} This job won't apply until you try again or switch it to plain Apply.
      </p>
      <Button
        variant="primary"
        disabled={pending !== null}
        onClick={() =>
          run("retry", async () => {
            await api(`${path(view.job.id)}/retry`, "POST");
            onChanged();
          })
        }
      >
        Try again
      </Button>
    </div>
  );
}

function ScoreBar({ score, source, keywords, tiers, children }: { score: number | null; source: number | null; keywords: Keyword[]; tiers: ReturnType<typeof liveScore>["tiers"]; children?: ReactNode }) {
  const parts = [
    tiers.required.total ? `Required ${tiers.required.found} of ${tiers.required.total}` : "",
    tiers.duties.total ? `Responsibilities ${tiers.duties.found} of ${tiers.duties.total}` : "",
    tiers.preferred.total ? `Preferred ${tiers.preferred.found} of ${tiers.preferred.total}` : "",
  ].filter(Boolean);
  return (
    <section className="tl-score" aria-label="ATS keyword match">
      <div className="tl-score-main">
        <output className="tl-score-number" data-testid="ats-score" aria-live="polite">
          {score == null ? "No terms found" : `${score}%`}
        </output>
        <div>
          <strong>ATS keyword match</strong>
          <small>
            {source != null && score != null && source !== score ? `${source}% on your uploaded resume. ` : ""}
            {parts.join(" · ")}
          </small>
        </div>
      </div>
      {children}
      {keywords.length === 0 && <p className="ax-note">This posting's text has no clear requirements to match against.</p>}
    </section>
  );
}

function Legend() {
  return (
    <p className="tl-legend" aria-label="What the highlights mean">
      <mark className="tl-hit">On your resume</mark> <mark className="tl-have">In your history, not on this resume</mark> <mark className="tl-need">You'd need this</mark>
    </p>
  );
}

/** What the posting asks for, sorted by what can be done about it. A term nothing supports is named honestly and never offered as an insert. */
function KeywordLists({ rows, onAdd }: { rows: Keyword[]; onAdd?: (term: string) => void }) {
  const have = rows.filter((k) => k.status === "available");
  const need = rows.filter((k) => k.status === "needed");
  const on = rows.filter((k) => k.status === "matched");
  return (
    <div className="tl-lists">
      {need.length > 0 && (
        <div>
          <h3>You'd need this ({need.length})</h3>
          <p className="ax-note">Nothing you've given AXIOM says you have these, so they stay off your resume. If you do have one, add it to your Profile.</p>
          <ul className="tl-terms">{need.map((k) => <li key={k.term} className="tl-need">{k.term}</li>)}</ul>
        </div>
      )}
      {have.length > 0 && (
        <div>
          <h3>In your history, not on this resume ({have.length})</h3>
          <ul className="tl-terms">
            {have.map((k) => (
              <li key={k.term} className="tl-have">
                {k.term}
                {onAdd && (
                  <button type="button" className="ax-link" onClick={() => onAdd(k.term)} aria-label={`Add ${k.term} to Skills`}>
                    Add
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {on.length > 0 && (
        <div>
          <h3>On your resume ({on.length})</h3>
          <ul className="tl-terms">{on.map((k) => <li key={k.term} className="tl-hit">{k.term}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function Posting({ text, rows }: { text: string; rows: Keyword[] }) {
  return (
    <section className="tl-posting" aria-label="The posting">
      <h2>The posting</h2>
      <Legend />
      <div className="tl-posting-text">
        <Marked text={text} keywords={rows} />
      </div>
    </section>
  );
}

function NotChosen({ view }: { view: View }) {
  const live = useMemo(() => liveScore(view.ats.keywords, ""), [view.ats.keywords]);
  const rows = view.ats.keywords;
  return (
    <div className="tl-columns">
      <div className="tl-col">
        <ScoreBar score={view.ats.score} source={null} keywords={rows} tiers={scoreTiers(rows, live.tiers)}>
          <p className="ax-note">{view.ats.disclaimer}</p>
        </ScoreBar>
        <KeywordLists rows={rows} />
      </div>
      <Posting text={view.job.description} rows={rows} />
    </div>
  );
}

function scoreTiers(rows: Keyword[], empty: ReturnType<typeof liveScore>["tiers"]) {
  const tiers = { required: { ...empty.required, found: 0, total: 0 }, duties: { found: 0, total: 0 }, preferred: { found: 0, total: 0 } };
  for (const k of rows) {
    tiers[k.tier].total += 1;
    if (k.status === "matched") tiers[k.tier].found += 1;
  }
  return tiers;
}

/** A rewrite's allowed characters: the source's length, down to 10% shorter (rules.bullet_window). */
function windowOf(source: string) {
  const n = source.trim().length;
  return { low: Math.ceil(n * 0.9), high: n };
}

function BulletEdit({ bullet, source, keywords, problem, onChange }: { bullet: Bullet; source: string | undefined; keywords: Keyword[]; problem?: string; onChange: (text: string) => void }) {
  const original = source ?? bullet.text;
  const { low, high } = windowOf(original);
  const n = bullet.text.trim().length;
  const changed = bullet.text !== original;
  const off = changed && (n > high || n < low);
  return (
    <li className="tl-bullet">
      <MarkedArea value={bullet.text} onChange={onChange} keywords={keywords} label="Bullet" invalid={off || !!problem} />
      <div className="tl-bullet-foot">
        <span className={"tl-count" + (off ? " is-off" : "")} data-testid={`count-${bullet.id}`}>
          {n} characters
          {off ? (n > high ? `, at most ${high}` : `, at least ${low}`) : changed ? ` of ${high}` : ""}
        </span>
        {changed && (
          <button type="button" className="ax-link" onClick={() => onChange(original)}>
            Use my original
          </button>
        )}
      </div>
      {changed && (
        <p className="tl-was">
          <Diff from={original} to={bullet.text} />
        </p>
      )}
      {problem && <p className="ax-error">{problem}</p>}
    </li>
  );
}

function Review({ view, onView, onChanged }: { view: View; onView: (v: View) => void; onChanged: () => void }) {
  const { go } = useWorkspace();
  const [doc, setDoc] = useState<Doc>(() => structuredClone(view.doc!));
  const [removed, setRemoved] = useState<string[]>(view.removed_projects);
  const [dirty, setDirty] = useState(false);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [message, setMessage] = useState("");
  const { pending, run } = useAction();
  const source = view.source_doc!;
  const sourceBullets = useMemo(() => {
    const out = new Map<string, string>();
    for (const s of source.sections) for (const e of s.entries) for (const b of e.bullets) out.set(b.id, b.text);
    return out;
  }, [source]);
  const live = useMemo(() => liveScore(view.ats.keywords, docPlain(doc)), [doc, view.ats.keywords]);
  const onResume = useMemo(() => live.rows.filter((k) => k.status === "matched"), [live.rows]);
  const problemAt = (id: string) => problems.find((p) => p.where === id)?.message;
  const change = (fn: (d: Doc) => void) => {
    setDoc((was) => {
      const next = structuredClone(was);
      fn(next);
      return next;
    });
    setDirty(true);
    setMessage("");
  };
  const setBullet = (sec: number, ent: number, bul: number, text: string) =>
    change((d) => {
      d.sections[sec]!.entries[ent]!.bullets[bul]!.text = text;
    });
  const setText = (sec: number, text: string) =>
    change((d) => {
      d.sections[sec]!.text = text;
    });
  const addSkill = (term: string) =>
    change((d) => {
      let s = d.sections.find((x) => x.kind === "skills" && x.include);
      if (!s) {
        s = { id: "skills", title: "Skills", kind: "skills", entries: [], text: "", include: true };
        d.sections.push(s);
      }
      const trimmed = s.text.trimEnd();
      s.text = trimmed ? `${trimmed}, ${term}` : term;
    });
  const toggleProject = (sec: number, ent: number, id: string, keep: boolean) => {
    change((d) => {
      d.sections[sec]!.entries[ent]!.include = keep;
    });
    setRemoved((was) => (keep ? was.filter((x) => x !== id) : [...was, id]));
  };
  const save = async () => {
    try {
      const fresh = await api<View>(`${path(view.job.id)}/save`, "POST", { doc, removed_projects: removed });
      setProblems([]);
      return fresh;
    } catch (e) {
      const body = e instanceof ApiError ? (e.data as { problems?: Problem[] } | undefined) : undefined;
      setProblems(body?.problems ?? []);
      setMessage(e instanceof Error ? plainError(e.message) : "That couldn't be saved.");
      return null;
    }
  };
  const saveOnly = () =>
    run("save", async () => {
      const fresh = await save();
      if (fresh) onView(fresh);
    });
  const approve = () =>
    run("approve", async () => {
      let artifact = view.artifact!.id;
      if (dirty) {
        const fresh = await save();
        if (!fresh) return;
        artifact = fresh.artifact!.id;
        onView(fresh);
      }
      try {
        const done = await api<{ message: string }>(`${path(view.job.id)}/approve`, "POST", { artifact_id: artifact, apply: true });
        setMessage(done.message);
        onChanged();
      } catch (e) {
        setMessage(e instanceof Error ? plainError(e.message) : "That couldn't be approved.");
      }
    });
  const approved = view.choice?.state === "approved" && view.choice.approved_artifact_id === view.artifact!.id;
  const overflow = (view.pages ?? 1) > 1;
  const summary = doc.sections.findIndex((s) => s.kind === "text" && /^(professional\s+)?(summary|profile|objective)\b/i.test(s.title));
  const newSummary = summary >= 0 && !source.sections.some((s) => s.id === doc.sections[summary]!.id);
  const pdf = usePdf(`/api/v2/resumes/artifacts/${view.artifact!.id}/pdf`);
  const sized = view.rules?.typography?.sizes;
  return (
    <>
      <div className="tl-bar">
        <ScoreBar score={live.score} source={view.ats_source} keywords={live.rows} tiers={live.tiers} />
        <div className="tl-actions">
          <Button variant="primary" disabled={pending !== null || !view.rules?.ok || !!view.approval_block || view.stale} onClick={approve}>
            {approved && !dirty ? "Approved" : view.approval_required ? "Approve and apply" : "Save and apply"}
          </Button>
          {dirty && (
            <Button disabled={pending !== null} onClick={saveOnly}>
              Save edits
            </Button>
          )}
          <Button
            icon={<RotateCcw size={14} aria-hidden="true" />}
            disabled={pending !== null}
            onClick={() =>
              run("again", async () => {
                await api(`${path(view.job.id)}/retry`, "POST");
                onChanged();
              })
            }
          >
            Tailor again
          </Button>
        </div>
      </div>
      <p className="tl-disclaimer">{view.ats.disclaimer}</p>
      {view.approval_block && <p className="ax-note" role="status">{view.approval_block.message}</p>}
      {view.report.workspace_id && <>
        {dirty && <p className="ax-note">Save your resume edits before changing projects.</p>}
        <AggressiveDrafts workspaceId={view.report.workspace_id} jobId={view.job.id} needsUpdate={view.project_changes_pending}
          disabled={dirty || pending !== null || ["queued", "running", "submitting", "waiting_browser", "confirmed", "submission_unknown", "already_applied"].includes(view.job.status)} onChanged={onChanged} />
      </>}
      {message && (
        <p className={problems.length ? "ax-error tl-message" : "tl-message"} role="status">
          {message}
        </p>
      )}
      {approved && !dirty && (
        <p className="tl-approved">
          {view.choice?.approved_how === "setting_off" ? "Approval is off in Settings, so this version goes out as it is." : "You approved this version."} The PDF below is exactly what's sent.{" "}
          <a href={routeUrl({ view: "settings", tab: "applying" })} className="ax-link" onClick={(e) => { e.preventDefault(); go({ view: "settings", tab: "applying" }); }}>Settings</a>
        </p>
      )}
      {view.stale && <p className="ax-error tl-message" role="alert">Your profile or this posting changed after this was tailored, so it won't be sent. Tailor it again.</p>}
      {overflow && (
        <p className="ax-error tl-message" role="alert">
          This resume runs to {view.pages} pages at the set type sizes, even with tight spacing. Nothing was dropped to fit. Shorten something if you want one page.
        </p>
      )}
      <div className="tl-columns">
        <div className="tl-col tl-resume">
          <h2>Your tailored resume</h2>
          <Legend />
          {doc.sections.map((s, si) => {
            if (!s.include) return null;
            const isSummary = si === summary;
            if (s.kind === "skills" || s.kind === "text") {
              const editable = s.kind === "skills" || isSummary;
              const was = source.sections.find((x) => x.id === s.id)?.text;
              return (
                <section key={s.id} className="tl-sec">
                  <h3>
                    {s.title}
                    {isSummary && newSummary && <Pill>New</Pill>}
                  </h3>
                  {editable ? (
                    <MarkedArea value={s.text} keywords={onResume} label={s.title} invalid={!!problemAt(s.id)} onChange={(t) => setText(si, t)} />
                  ) : (
                    <p className="tl-fixed"><Marked text={s.text} keywords={onResume} /></p>
                  )}
                  {problemAt(s.id) && <p className="ax-error">{problemAt(s.id)}</p>}
                  {s.kind === "skills" && was !== undefined && was !== s.text && (
                    <p className="tl-was"><Diff from={was} to={s.text} /></p>
                  )}
                  {s.kind === "skills" && <p className="ax-hint">Skills can be added, never removed. An added skill has to be in your history.</p>}
                </section>
              );
            }
            return (
              <section key={s.id} className="tl-sec">
                <h3>{s.title}</h3>
                {s.entries.map((e, ei) => {
                  const removable = !view.report.workspace_id && s.kind === "projects" && source.sections.some((x) => x.entries.some((y) => y.id === e.id));
                  const generated = !!view.report.workspace_id && !source.sections.some((x) => x.entries.some((y) => y.id === e.id));
                  return (
                    <article key={e.id} className={"tl-entry" + (e.include ? "" : " is-off")}>
                      <header>
                        <span><strong>{e.heading}</strong>{e.subheading ? `, ${e.subheading}` : ""}</span>
                        {e.dates && <em>{e.dates}</em>}
                      </header>
                      {problemAt(e.id) && <p className="ax-error">{problemAt(e.id)}</p>}
                      {s.kind === "projects" && removable && (
                        <label className="ax-check tl-keep">
                          <input type="checkbox" checked={e.include} onChange={(x) => toggleProject(si, ei, e.id, x.target.checked)} />
                          <span>Keep on this resume</span>
                        </label>
                      )}
                      {e.include && s.kind !== "education" && (
                        <ul className="tl-bullets">
                          {e.bullets.map((b, bi) =>
                            b.include ? (
                              generated ? <li key={b.id} className="tl-fixed"><Marked text={b.text} keywords={onResume} /></li> : <BulletEdit key={b.id} bullet={b} source={sourceBullets.get(b.id)} keywords={onResume} problem={problemAt(b.id)} onChange={(t) => setBullet(si, ei, bi, t)} />
                            ) : null,
                          )}
                        </ul>
                      )}
                    </article>
                  );
                })}
              </section>
            );
          })}
        </div>
        <aside className="tl-col tl-side">
          <KeywordLists rows={live.rows} onAdd={addSkill} />
          <Posting text={view.job.description} rows={live.rows} />
          {!view.report.workspace_id && view.drafts.some((d) => !d.confirmed) && (
            <section className="tl-drafts">
              <h2>Not built yet</h2>
              <p className="ax-note">These drafts need your approval before applications.</p>
              <ul>{view.drafts.filter((d) => !d.confirmed).map((d) => <li key={d.id}>{d.title}</li>)}</ul>
            </section>
          )}
          <Checks view={view} sized={sized} />
          <details className="ax-fold tl-pdf">
            <summary>The PDF that's sent</summary>
            <PdfPages data={pdf.data} error={pdf.error} fit="width" label="Tailored resume" />
          </details>
          {view.cost && (
            <p className="ax-hint">
              This version cost {cents(view.cost.usd)} in AI ({view.cost.calls} {view.cost.calls === 1 ? "call" : "calls"}).
            </p>
          )}
        </aside>
      </div>
    </>
  );
}

function Checks({ view, sized }: { view: View; sized?: Record<string, number[]> }) {
  const rules = view.preview_rules || view.rules;
  const type = sized && `Name ${sized.name?.[0] ?? "?"} pt, headings ${sized.section?.[0] ?? "?"}, sub-headings ${sized.sub?.[0] ?? "?"}, bullets ${sized.bullet?.[0] ?? "?"}, read from the PDF`;
  return (
    <section className="tl-checks" aria-label="Checks">
      <h2>Checks</h2>
      <ul>
        <li className={rules?.ok ? "is-ok" : "is-bad"}>
          {rules?.ok ? "Bullets within length and count; nothing removed; only your own skills" : rules?.problems?.[0]?.message || "Not checked"}
        </li>
        {type && <li className={rules?.typography?.ok ? "is-ok" : "is-bad"}>{type}</li>}
        {(view.parse?.items || []).map((i) => (
          <li key={i.label} className={i.ok ? "is-ok" : "is-bad"}>
            {i.label}
            {i.detail ? `: ${i.detail}` : ""}
          </li>
        ))}
        {(rules?.warnings || []).filter((w) => w.code !== "overflow").map((w) => (
          <li key={w.where + w.code} className="is-warn">{w.message}</li>
        ))}
      </ul>
      {(view.report.refused?.length ?? 0) > 0 && (
        <p className="ax-note">{view.report.refused!.length} suggested {view.report.refused!.length === 1 ? "change was" : "changes were"} refused by these checks and your own words kept.</p>
      )}
    </section>
  );
}
