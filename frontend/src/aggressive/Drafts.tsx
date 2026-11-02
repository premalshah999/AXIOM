import { useEffect, useState } from "react";
import { plainError } from "../format";
import { api } from "../api";
import { PdfPages, usePdf } from "../pdfpages";
import { Button } from "../ui/index";
import "./drafts.css";

type Project = {
  id: string;
  title: string;
  descriptor: string;
  archetype: string;
  proof_point: number;
  bullets: string[];
  build_plan: string[];
  confirmed: boolean;
  replaces?: string | null;
};
type Original = { id: string; heading: string; include: boolean; bullets: { id: string; text: string; include: boolean }[] };
export type DraftWorkspace = {
  id: string;
  revision: number;
  selected_drafts: string[];
  removed_projects: string[];
  drafts: Project[];
  source_doc: { sections: { kind: string; include: boolean; entries: Original[] }[] };
  brief: { product: string; proof_points: string[]; real_stack: string[]; seniority: string };
  report?: {
    critique_text?: string;
    overflow_message?: string;
    drafts_left_out?: { title: string; problems: string[] }[];
    cost?: { actual_usd: number | null };
    pillars_missing?: string[];
  };
};

const path = (id: string) => `/v2/resumes/aggressive/${encodeURIComponent(id)}`;

const INSPIRED_WARNING = "The generated resume is an inspired version. Please verify every detail on this resume before using it for applications. We are constantly trying to help our users get interviews, and this could help you get an idea of what an ideal dream candidate would look like, by our standards.";

/** The server invalidates approval on each project change. Refresh the parent
 * view after mutations; Update project PDF creates a new file without AI.
 * This component never approves an application. */
export function AggressiveDrafts({ workspaceId, jobId, needsUpdate, disabled, onChanged }: { workspaceId: string; jobId?: string; needsUpdate?: boolean; disabled?: boolean; onChanged?: () => void }) {
  const [data, setData] = useState<DraftWorkspace | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  // In place, with any trouble said here: in a new window the app has no words for a refusal, only the raw answer.
  const preview = usePdf(previewing && data ? `/api${path(data.id)}/preview.pdf?revision=${data.revision}` : null);
  useEffect(() => {
    const abort = new AbortController();
    setData(null);
    setError("");
    api<DraftWorkspace>(path(workspaceId), "GET", undefined, abort.signal)
      .then(setData)
      .catch((e: Error) => { if (e.name !== "AbortError") setError(plainError(e.message)); });
    return () => abort.abort();
  }, [workspaceId]);

  async function change(endpoint: string, method: string, body: unknown, optimistic?: Partial<DraftWorkspace>) {
    if (!data || busy || disabled) return;
    const previous = data;
    setBusy(true);
    setError("");
    if (optimistic) setData({ ...data, ...optimistic });
    try {
      const updated = await api<DraftWorkspace>(path(data.id) + endpoint, method, body);
      setData(updated);
      onChanged?.();
    } catch (e) {
      setData(previous);
      setError((e as Error).message);
      // A stale revision can happen in another open review tab. Refresh the
      // saved choices so the next action uses the current revision.
      try { setData(await api<DraftWorkspace>(path(previous.id))); } catch { /* Keep the original error. */ }
    } finally {
      setBusy(false);
    }
  }
  function choose(selected: string[], removed: string[]) {
    return change("/choices", "PUT", { selected_drafts: selected, removed_projects: removed, revision: data!.revision }, { selected_drafts: selected, removed_projects: removed });
  }
  /** A draft and the project it replaces share one place on the page: choosing either one gives way to the other. */
  function chooseDraft(d: Project, keep: boolean) {
    const selected = keep ? [...data!.selected_drafts, d.id] : data!.selected_drafts.filter(id => id !== d.id);
    const others = data!.removed_projects.filter(id => id !== d.replaces);
    return choose(selected, keep && d.replaces ? [...others, d.replaces] : others);
  }
  function chooseOriginal(id: string, keep: boolean) {
    const others = data!.removed_projects.filter(x => x !== id);
    const replacing = data!.drafts.filter(d => d.replaces === id).map(d => d.id);
    return keep ? choose(data!.selected_drafts.filter(x => !replacing.includes(x)), others) : choose(data!.selected_drafts, [...others, id]);
  }
  async function updatePdf() {
    if (!data || !jobId || busy || disabled) return;
    setBusy(true);
    setError("");
    try {
      await api(`${path(data.id)}/refresh`, "POST", { job_id: jobId, revision: data.revision });
      onChanged?.();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (!data) return <div className="ag-drafts" aria-live="polite">{error ? <p role="alert">{error}</p> : <p>Loading project drafts…</p>}</div>;
  const originals = data.source_doc.sections.filter(s => s.include && s.kind === "projects").flatMap(s => s.entries.filter(e => e.include));
  const unapproved = data.drafts.filter(d => data.selected_drafts.includes(d.id) && !d.confirmed).length;
  const replaced = (d: Project) => originals.find(p => p.id === d.replaces)?.heading;
  return <section className="ag-drafts" aria-label="Aggressive project drafts" aria-busy={busy}>
    <p className="ag-warning" role="note">{INSPIRED_WARNING}</p>
    <p className="ag-meta" role="note">Figures in [target: ...] brackets are inspired values, not results. Replace each with what you really measure.</p>
    <div className="ag-heading">
      <div><h2>Choose your projects</h2><p>Each draft takes the place of one of your projects, with the same number of bullets and the same length. Keep them, or switch back to your own.</p></div>
      <Button aria-expanded={previewing} onClick={() => setPreviewing(!previewing)}>{previewing ? "Hide preview" : "Preview PDF"}</Button>
    </div>
    {previewing && <div className="ag-preview"><PdfPages data={preview.data} error={preview.error} fit="width" label="Preview of these project choices" /></div>}
    <p className="ag-status" role="status">{unapproved
      ? `${unapproved} selected project${unapproved === 1 ? " needs" : "s need"} your approval before applications.`
      : "Project choices saved. Review and approve the final PDF before applying."}</p>
    {error && <p className="ag-error" role="alert">{error}</p>}
    {needsUpdate && <div className="ag-heading"><p>Project choices changed. Update the PDF, then review it before applying. This uses no AI.</p><Button disabled={busy || disabled} onClick={updatePdf}>Update project PDF</Button></div>}
    {data.report?.overflow_message && <p className="ag-error">{data.report.overflow_message}</p>}
    {data.report?.drafts_left_out?.length ? <p className="ag-meta">{data.report.drafts_left_out.length} project draft{data.report.drafts_left_out.length === 1 ? " was" : "s were"} left out because {data.report.drafts_left_out.length === 1 ? "it" : "they"} didn't pass the checks, so your own project stays in that place.</p> : null}
    <details className="ag-brief"><summary>What this job needs</summary>
      <p>{data.brief.product}</p><ol>{data.brief.proof_points.map(p => <li key={p}>{p}</li>)}</ol>
      <p>{data.brief.seniority}</p><p>{data.brief.real_stack.join(", ")}</p>
    </details>
    <div className="ag-heading"><h3>Generated drafts</h3><Button disabled={busy || disabled || (!data.selected_drafts.length && !data.removed_projects.length)} onClick={() => choose([], [])}>Use only my original projects</Button></div>
    <div className="ag-projects">{data.drafts.map(d => <article key={d.id} className="ag-project">
      <label className="ag-choice"><input type="checkbox" checked={data.selected_drafts.includes(d.id)} disabled={busy || disabled}
        onChange={e => chooseDraft(d, e.target.checked)} />
        <span>{d.title}</span></label>
      <p className="ag-meta">{d.confirmed ? "Approved project" : "Draft · Not built yet"} · {d.archetype}{replaced(d) ? ` · replaces ${replaced(d)}` : ""}</p>
      <ul>{d.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <details><summary>How to build it</summary><ol>{d.build_plan.map((s, i) => <li key={i}>{s}</li>)}</ol></details>
      {!d.confirmed && <div className="ag-approve"><p>Approve only once you've verified every detail of this project is accurate.</p>
        <Button disabled={busy || disabled} onClick={() => change(`/drafts/${encodeURIComponent(d.id)}/approve`, "POST", { approved: true, revision: data.revision })}>Approve project</Button></div>}
    </article>)}</div>
    <h3>Your original projects</h3>
    {originals.length ? originals.map(p => <article key={p.id} className="ag-project">
      <label className="ag-choice"><input type="checkbox" disabled={busy || disabled} checked={!data.removed_projects.includes(p.id)}
        onChange={e => chooseOriginal(p.id, e.target.checked)} />
        <span>{p.heading}</span></label>
      <ul>{p.bullets.filter(b => b.include).map(b => <li key={b.id}>{b.text}</li>)}</ul>
    </article>) : <p>Your resume has no projects of its own. You can continue with its other sections.</p>}
    {data.report?.critique_text && <details className="ag-critique"><summary>Independent recruiter critique</summary><pre>{data.report.critique_text}</pre></details>}
    {data.report?.pillars_missing?.length ? <p className="ag-meta">Requirements without a match: {data.report.pillars_missing.join(", ")}. Coverage is advisory.</p> : null}
    {data.report?.cost?.actual_usd != null && <p className="ag-meta">Model cost: ${data.report.cost.actual_usd.toFixed(5)}</p>}
  </section>;
}
