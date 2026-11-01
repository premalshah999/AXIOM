import { useEffect, useState } from "react";
import { api } from "../api";
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
    cost?: { actual_usd: number | null };
    pillars_missing?: string[];
  };
};

const path = (id: string) => `/v2/resumes/aggressive/${encodeURIComponent(id)}`;

/** The server invalidates approval on each project change. Refresh the parent
 * view after mutations; Update project PDF creates a new file without AI.
 * This component never approves an application. */
export function AggressiveDrafts({ workspaceId, jobId, needsUpdate, disabled, onChanged }: { workspaceId: string; jobId?: string; needsUpdate?: boolean; disabled?: boolean; onChanged?: () => void }) {
  const [data, setData] = useState<DraftWorkspace | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    setData(null);
    setError("");
    api<DraftWorkspace>(path(workspaceId), "GET", undefined, abort.signal)
      .then(setData)
      .catch((e: Error) => { if (e.name !== "AbortError") setError(e.message); });
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
  return <section className="ag-drafts" aria-label="Aggressive project drafts" aria-busy={busy}>
    <div className="ag-heading">
      <div><h2>Choose your projects</h2><p>Drafts are included in this preview. Keep them, or switch to projects from your master resume.</p></div>
      <a className="ax-btn" target="_blank" rel="noreferrer" href={`/api${path(data.id)}/preview.pdf?revision=${data.revision}`}>Preview PDF</a>
    </div>
    <p className="ag-status" role="status">{unapproved
      ? `${unapproved} selected project${unapproved === 1 ? " needs" : "s need"} your approval before applications.`
      : "Project choices saved. Review and approve the final PDF before applying."}</p>
    {error && <p className="ag-error" role="alert">{error}</p>}
    {needsUpdate && <div className="ag-heading"><p>Project choices changed. Update the PDF, then review it before applying. This uses no AI.</p><Button disabled={busy || disabled} onClick={updatePdf}>Update project PDF</Button></div>}
    {data.report?.overflow_message && <p className="ag-error">{data.report.overflow_message}</p>}
    <details className="ag-brief"><summary>What this job needs</summary>
      <p>{data.brief.product}</p><ol>{data.brief.proof_points.map(p => <li key={p}>{p}</li>)}</ol>
      <p>{data.brief.seniority}</p><p>{data.brief.real_stack.join(", ")}</p>
    </details>
    <div className="ag-heading"><h3>Generated drafts</h3><Button disabled={busy || disabled} onClick={() => choose([], [])}>Use only my original projects</Button></div>
    <div className="ag-projects">{data.drafts.map(d => <article key={d.id} className="ag-project">
      <label className="ag-choice"><input type="checkbox" checked={data.selected_drafts.includes(d.id)} disabled={busy || disabled}
        onChange={e => choose(e.target.checked ? [...data.selected_drafts, d.id] : data.selected_drafts.filter(id => id !== d.id), data.removed_projects)} />
        <span>{d.title}</span></label>
      <p className="ag-meta">{d.confirmed ? "Approved project" : "Draft · Not built yet"} · {d.archetype}</p>
      <ul>{d.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
      <details><summary>How to build it</summary><ol>{d.build_plan.map((s, i) => <li key={i}>{s}</li>)}</ol></details>
      {!d.confirmed && <div className="ag-approve"><p>Approve this exact project content for your resume.</p>
        <Button disabled={busy || disabled} onClick={() => change(`/drafts/${encodeURIComponent(d.id)}/approve`, "POST", { approved: true, revision: data.revision })}>Approve project</Button></div>}
    </article>)}</div>
    <h3>Your original projects</h3>
    {originals.length ? originals.map(p => <article key={p.id} className="ag-project">
      <label className="ag-choice"><input type="checkbox" disabled={busy || disabled} checked={!data.removed_projects.includes(p.id)}
        onChange={e => choose(data.selected_drafts, e.target.checked ? data.removed_projects.filter(id => id !== p.id) : [...data.removed_projects, p.id])} />
        <span>{p.heading}</span></label>
      <ul>{p.bullets.filter(b => b.include).map(b => <li key={b.id}>{b.text}</li>)}</ul>
    </article>) : <p>Your master resume has no projects. You can continue with its existing sections.</p>}
    {data.report?.critique_text && <details className="ag-critique"><summary>Independent recruiter critique</summary><pre>{data.report.critique_text}</pre></details>}
    {data.report?.pillars_missing?.length ? <p className="ag-meta">Requirements without a match: {data.report.pillars_missing.join(", ")}. Coverage is advisory.</p> : null}
    {data.report?.cost?.actual_usd != null && <p className="ag-meta">Model cost: ${data.report.cost.actual_usd.toFixed(5)}</p>}
  </section>;
}
