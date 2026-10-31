// Profile › Work and education: roles, degrees and notes as cards that open in a drawer to edit, then skills and languages.
// Everything reads and writes the page's one draft (profile.tsx); nothing here saves on its own.
import { Briefcase, ChevronRight, FileText, GraduationCap, Plus, ScanText } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "./api";
import { plural } from "./format";
import { useAction } from "./hooks";
import { finish, Group, SectionHead, span, Tags } from "./profile-fields";
import type { Setter } from "./profile";
import type { EducationEntry, Profile, WorkEntry } from "./types";
import { Button, Check, ConfirmButton, Drawer, Empty, Input, Monogram, Panel, Textarea } from "./ui/index";

export const EMPTY_WORK: WorkEntry = { company: "", title: "", location: "", start: "", end: "", current: false, description: "" };
export const EMPTY_EDU: EducationEntry = { school: "", degree: "", field_of_study: "", start: "", end: "", gpa: "" };

type Editing = { kind: "work" | "edu" | "note"; i: number; fresh: boolean } | null;
type Proposal = { work: WorkEntry[]; education: EducationEntry[]; skills: string[]; contact: Partial<Profile> };

const blankWork = (w: WorkEntry) => !w.company.trim() && !w.title.trim() && !w.description.trim();
const blankEdu = (e: EducationEntry) => !e.school.trim() && !e.degree.trim();
const sameText = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** One role, degree or note as a card: whole card opens it. */
function EntryCard({
  lead,
  title,
  sub,
  when,
  body,
  label,
  onOpen,
}: {
  lead: ReactNode;
  title: string;
  sub?: string;
  when?: string;
  body?: string;
  label: string;
  onOpen: () => void;
}) {
  return (
    <li>
      <button type="button" className="pf-card" aria-label={label} onClick={onOpen}>
        {lead}
        <span className="pf-card-main">
          <span className="pf-card-title">
            <strong title={title}>{title}</strong>
            {when && <span className="pf-card-when">{when}</span>}
          </span>
          {sub && <span className="pf-card-sub">{sub}</span>}
          {body && <span className="pf-card-body">{body}</span>}
        </span>
        <ChevronRight size={16} aria-hidden="true" className="pf-card-go" />
      </button>
    </li>
  );
}

export function WorkSection({ p, set }: { p: Profile; set: Setter }) {
  const { pending, run } = useAction();
  const [editing, setEditing] = useState<Editing>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);

  const hasWork = (w: WorkEntry) => p.work.some((x) => sameText(x.company, w.company) && sameText(x.title, w.title));
  const hasEdu = (e: EducationEntry) => p.education.some((x) => sameText(x.school, e.school) && sameText(x.degree, e.degree));
  const newWork = proposal?.work.filter((w) => !hasWork(w)) || [];
  const newEdu = proposal?.education.filter((e) => !hasEdu(e)) || [];
  const updateWork = (i: number, patch: Partial<WorkEntry>) => set("work", p.work.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  const updateEdu = (i: number, patch: Partial<EducationEntry>) =>
    set("education", p.education.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const updateNote = (i: number, text: string) => set("evidence", p.evidence.map((x, j) => (j === i ? { ...x, text } : x)));

  const extract = () =>
    run("extract", async () => {
      const r = await api<{
        work: WorkEntry[];
        education: EducationEntry[];
        skills: string[];
        first_name: string;
        last_name: string;
        phone: string;
        location: string;
        linkedin: string;
        github: string;
        website: string;
      }>("/profile/extract", "POST");
      const contact: Partial<Profile> = {};
      for (const k of ["first_name", "last_name", "phone", "location", "linkedin", "github", "website"] as const)
        if (r[k] && !p[k]) contact[k] = r[k];
      setProposal({ work: r.work || [], education: r.education || [], skills: r.skills || [], contact });
    });

  // A card that was just added and left empty is taken back, so the list never holds a blank one.
  const close = () => {
    if (editing?.fresh) {
      if (editing.kind === "work" && p.work[editing.i] && blankWork(p.work[editing.i]!)) set("work", p.work.filter((_, j) => j !== editing.i));
      if (editing.kind === "edu" && p.education[editing.i] && blankEdu(p.education[editing.i]!)) set("education", p.education.filter((_, j) => j !== editing.i));
      if (editing.kind === "note" && p.evidence[editing.i] && !p.evidence[editing.i]!.text.trim()) set("evidence", p.evidence.filter((_, j) => j !== editing.i));
    }
    setEditing(null);
  };
  const addWork = () => {
    set("work", [...p.work, { ...EMPTY_WORK }]);
    setEditing({ kind: "work", i: p.work.length, fresh: true });
  };
  const addEdu = () => {
    set("education", [...p.education, { ...EMPTY_EDU }]);
    setEditing({ kind: "edu", i: p.education.length, fresh: true });
  };
  const addNote = () => {
    const used = new Set(p.evidence.map((e) => e.id));
    let n = p.evidence.length + 1;
    while (used.has("note-" + n)) n++;
    set("evidence", [...p.evidence, { id: "note-" + n, text: "" }]);
    setEditing({ kind: "note", i: p.evidence.length, fresh: true });
  };

  const w = editing?.kind === "work" ? p.work[editing.i] : undefined;
  const e = editing?.kind === "edu" ? p.education[editing.i] : undefined;
  const note = editing?.kind === "note" ? p.evidence[editing.i] : undefined;
  const proposed = newWork.length + newEdu.length;

  return (
    <div className="pf-stack">
      <section className="pf-block">
        <SectionHead
          title="Work history"
          line={p.work.length ? plural(p.work.length, "role") + ". Applications that ask for your experience use these." : undefined}
          actions={
            <>
              <Button size="sm" icon={<ScanText size={14} aria-hidden="true" />} loading={pending === "extract"} disabled={pending !== null} onClick={extract}>
                Read from resume
              </Button>
              <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addWork}>
                Add role
              </Button>
            </>
          }
        />
        {proposal && (
          <div className="pf-proposal">
            <p>
              Found {plural(proposal.work.length, "role")} and {plural(proposal.education.length, "degree")} in your resume;{" "}
              {proposed ? `${proposed} not yet in your profile.` : "all are already in your profile."}
            </p>
            {proposed > 0 && (
              <ul className="ax-rows">
                {[...newWork.map((x) => ({ kind: "work" as const, w: x })), ...newEdu.map((x) => ({ kind: "edu" as const, e: x }))].map((item, i) => (
                  <li className="ax-row" key={i}>
                    <span className="ax-row-main">
                      {item.kind === "work" ? (
                        <>
                          <strong>{item.w.title}</strong>
                          <small>
                            {item.w.company} · {span(item.w.start, item.w.end, item.w.current)}
                          </small>
                        </>
                      ) : (
                        <>
                          <strong>
                            {item.e.degree}
                            {item.e.field_of_study && ` in ${item.e.field_of_study}`}
                          </strong>
                          <small>
                            {item.e.school} · {finish(item.e.end)}
                          </small>
                        </>
                      )}
                    </span>
                    <Button
                      size="sm"
                      onClick={() => (item.kind === "work" ? set("work", [...p.work, item.w]) : set("education", [...p.education, item.e]))}
                    >
                      Add
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <div className="pf-proposal-actions">
              {proposed > 1 && (
                <Button
                  size="sm"
                  onClick={() => {
                    set("work", [...p.work, ...newWork]);
                    set("education", [...p.education, ...newEdu]);
                  }}
                >
                  Add all {proposed}
                </Button>
              )}
              {Object.keys(proposal.contact).length > 0 && (
                <Button
                  size="sm"
                  onClick={() => {
                    for (const [k, v] of Object.entries(proposal.contact)) set(k as keyof Profile, v as never);
                  }}
                >
                  Fill {plural(Object.keys(proposal.contact).length, "empty contact field")}
                </Button>
              )}
              {proposal.skills.length > 0 && !p.skills.length && (
                <Button size="sm" onClick={() => set("skills", proposal.skills)}>
                  Use {plural(proposal.skills.length, "skill")}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setProposal(null)}>
                Done
              </Button>
            </div>
          </div>
        )}
        {p.work.length === 0 ? (
          <Panel>
            <Empty
              compact
              icon={Briefcase}
              title="No roles yet"
              hint="Applications ask for your employers, titles and dates. Add them here, or read them from your resume."
              action={
                <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addWork}>
                  Add role
                </Button>
              }
            />
          </Panel>
        ) : (
          <ul className="pf-cards">
            {p.work.map((x, i) => (
              <EntryCard
                key={i}
                lead={<Monogram name={x.company} size="md" />}
                title={x.title || "Untitled role"}
                sub={[x.company, x.location].filter(Boolean).join(" · ")}
                when={span(x.start, x.end, x.current)}
                body={x.description}
                label={`Edit ${x.title || "role"} at ${x.company || "employer"}`}
                onOpen={() => setEditing({ kind: "work", i, fresh: false })}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="pf-block">
        <SectionHead
          title="Education"
          line={p.education.length ? plural(p.education.length, "degree") + "." : undefined}
          actions={
            <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addEdu}>
              Add degree
            </Button>
          }
        />
        {p.education.length === 0 ? (
          <Panel>
            <Empty
              compact
              icon={GraduationCap}
              title="No degrees yet"
              hint="Schools, degrees and graduation dates answer most education questions on their own."
              action={
                <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addEdu}>
                  Add degree
                </Button>
              }
            />
          </Panel>
        ) : (
          <ul className="pf-cards">
            {p.education.map((x, i) => (
              <EntryCard
                key={i}
                lead={<Monogram name={x.school} size="md" />}
                title={(x.degree || "Degree") + (x.field_of_study ? ` in ${x.field_of_study}` : "")}
                sub={[x.school, x.gpa && `GPA ${x.gpa}`].filter(Boolean).join(" · ")}
                when={finish(x.end)}
                label={`Edit ${x.degree || "degree"} at ${x.school || "school"}`}
                onOpen={() => setEditing({ kind: "edu", i, fresh: false })}
              />
            ))}
          </ul>
        )}
      </section>

      <Panel flush className="pf-panel">
        <Group title="Skills and languages" hint="Used to match jobs and to answer skills questions.">
          <Tags label="Skills" className="pf-s6" value={p.skills} onChange={(v) => set("skills", v)} placeholder="Python, SQL…" />
          <Tags label="Languages" className="pf-s6" value={p.languages} onChange={(v) => set("languages", v)} placeholder="English…" />
        </Group>
      </Panel>

      <section className="pf-block">
        <SectionHead
          title="Notes and stories"
          line="Projects, achievements and anything else true about you. AXIOM draws on these to write answers it can support, and never beyond them."
          actions={
            <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addNote}>
              Add note
            </Button>
          }
        />
        {p.evidence.length === 0 ? (
          <Panel>
            <Empty
              compact
              icon={FileText}
              title="No notes yet"
              hint="A paragraph about a project or result is enough. Questions like “Tell us about a time…” are answered from these."
              action={
                <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={addNote}>
                  Add note
                </Button>
              }
            />
          </Panel>
        ) : (
          <ul className="pf-cards">
            {p.evidence.map((n, i) => (
              <EntryCard
                key={n.id}
                lead={
                  <span className="pf-note-icon" aria-hidden="true">
                    <FileText size={16} />
                  </span>
                }
                title={`Note ${i + 1}`}
                when={n.text.trim() ? `${n.text.length.toLocaleString()} characters` : undefined}
                body={n.text.replace(/\s+/g, " ").trim() || "Empty"}
                label={`Edit note ${i + 1}`}
                onOpen={() => setEditing({ kind: "note", i, fresh: false })}
              />
            ))}
          </ul>
        )}
      </section>

      <Drawer
        open={!!w}
        onClose={close}
        width={560}
        title={w?.title || "New role"}
        sub={w?.company}
        lead={<Monogram name={w?.company} size="lg" />}
        footer={
          <>
            <span className="pf-foot-start">
              <ConfirmButton
                label="Remove role"
                confirm="Remove"
                onConfirm={() => {
                  set("work", p.work.filter((_, j) => j !== editing!.i));
                  setEditing(null);
                }}
              />
            </span>
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          </>
        }
      >
        {w && editing && (
          <div className="pf-fields pf-form">
            <Input className="pf-s6" label="Title" data-autofocus value={w.title} onChange={(ev) => updateWork(editing.i, { title: ev.target.value })} />
            <Input className="pf-s3" label="Company" value={w.company} onChange={(ev) => updateWork(editing.i, { company: ev.target.value })} />
            <Input className="pf-s3" label="Location" value={w.location} onChange={(ev) => updateWork(editing.i, { location: ev.target.value })} />
            <Input className="pf-s3" label="Start" placeholder="YYYY-MM" value={w.start} onChange={(ev) => updateWork(editing.i, { start: ev.target.value })} />
            <Input
              className="pf-s3"
              label="End"
              placeholder={w.current ? "Present" : "YYYY-MM"}
              disabled={w.current}
              value={w.end}
              onChange={(ev) => updateWork(editing.i, { end: ev.target.value })}
            />
            <div className="pf-s6">
              <Check label="I work here now" checked={w.current} onChange={(on) => updateWork(editing.i, { current: on, end: on ? "" : w.end })} />
            </div>
            <Textarea
              className="pf-s6"
              label="What you did"
              hint="Plain sentences work. Applications that ask for duties or achievements use this."
              rows={9}
              value={w.description}
              onChange={(ev) => updateWork(editing.i, { description: ev.target.value })}
            />
          </div>
        )}
      </Drawer>

      <Drawer
        open={!!e}
        onClose={close}
        width={560}
        title={e?.degree || "New degree"}
        sub={e?.school}
        lead={<Monogram name={e?.school} size="lg" />}
        footer={
          <>
            <span className="pf-foot-start">
              <ConfirmButton
                label="Remove degree"
                confirm="Remove"
                onConfirm={() => {
                  set("education", p.education.filter((_, j) => j !== editing!.i));
                  setEditing(null);
                }}
              />
            </span>
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          </>
        }
      >
        {e && editing && (
          <div className="pf-fields pf-form">
            <Input className="pf-s6" label="School" data-autofocus value={e.school} onChange={(ev) => updateEdu(editing.i, { school: ev.target.value })} />
            <Input className="pf-s3" label="Degree" value={e.degree} onChange={(ev) => updateEdu(editing.i, { degree: ev.target.value })} />
            <Input className="pf-s3" label="Field of study" value={e.field_of_study} onChange={(ev) => updateEdu(editing.i, { field_of_study: ev.target.value })} />
            <Input className="pf-s2" label="Start" placeholder="YYYY-MM" value={e.start} onChange={(ev) => updateEdu(editing.i, { start: ev.target.value })} />
            <Input
              className="pf-s2"
              label="End or expected"
              placeholder="YYYY-MM"
              value={e.end}
              onChange={(ev) => updateEdu(editing.i, { end: ev.target.value })}
            />
            <Input className="pf-s2" label="GPA" value={e.gpa} onChange={(ev) => updateEdu(editing.i, { gpa: ev.target.value })} />
            <p className="ax-hint pf-s6">A date still ahead means the degree is in progress.</p>
          </div>
        )}
      </Drawer>

      <Drawer
        open={!!note}
        onClose={close}
        width={600}
        title={note ? `Note ${editing!.i + 1}` : "Note"}
        lead={
          <span className="pf-note-icon" aria-hidden="true">
            <FileText size={16} />
          </span>
        }
        footer={
          <>
            <span className="pf-foot-start">
              <ConfirmButton
                label="Remove note"
                confirm="Remove"
                onConfirm={() => {
                  set("evidence", p.evidence.filter((_, j) => j !== editing!.i));
                  setEditing(null);
                }}
              />
            </span>
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          </>
        }
      >
        {note && editing && (
          <Textarea
            label="Note"
            data-autofocus
            hint="Up to 8,000 characters."
            rows={16}
            maxLength={8000}
            value={note.text}
            onChange={(ev) => updateNote(editing.i, ev.target.value)}
          />
        )}
      </Drawer>
    </div>
  );
}
