import React, { useEffect, useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { dropDraft, useAction, useDraft, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import type {
  AccountRow,
  AccountSummary,
  AnswerRules,
  DemographicRule,
  EducationEntry,
  EducationStep,
  Profile,
  ReviewedAnswer,
  WorkEntry,
} from "./types";
import { ago, Confirm, Empty, ErrorLine, Field, KeyValues, Loading, plural } from "./ui";

// ---------- Profile ----------
type ProfileDraft = { value: Profile; base: string; dirty: boolean };
// Learned answers are server-owned; they never travel back in a profile save.
const comparable = (p: Profile) => {
  const { reviewed_answers: _r, approved_answers: _a, ...rest } = p as Profile & Record<string, unknown>;
  return JSON.stringify(rest);
};
const list = (v: string) =>
  v
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const EMPTY_WORK: WorkEntry = { company: "", title: "", location: "", start: "", end: "", current: false, description: "" };
const EMPTY_EDU: EducationEntry = { school: "", degree: "", field_of_study: "", start: "", end: "", gpa: "" };
const SECTIONS = [
  ["identity", "Identity"],
  ["experience", "Experience"],
  ["facts", "Facts"],
  ["policies", "Policies"],
  ["rules", "When options don't match"],
  ["preferences", "Preferences"],
] as const;

export function ProfileSettings() {
  const { snapshot, refresh, route } = useWorkspace();
  const server = snapshot.profile;
  const [draft, setDraft] = useDraft<ProfileDraft | null>("profile", null);
  const [section, setSection] = useDraft<string>("profile-section", "identity");
  // Links such as ?view=setup&tab=profile&filter=preferences open one section directly.
  useEffect(() => {
    if (route.filter && SECTIONS.some(([id]) => id === route.filter)) setSection(route.filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.filter]);
  const [factsText, setFactsText] = useDraft<string | null>("profile-facts", null);
  const { pending, run } = useAction();

  // Follow the server until the first edit; afterwards keep the draft and
  // flag concurrent changes instead of silently replacing either side.
  useEffect(() => {
    if (!draft || (!draft.dirty && draft.base !== comparable(server))) {
      setDraft({ value: server, base: comparable(server), dirty: false });
      setFactsText(JSON.stringify(server.facts || {}, null, 2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server]);
  if (!draft) return <Loading />;
  const p = draft.value;
  const facts = factsText ?? JSON.stringify(p.facts || {}, null, 2);
  let factsError = "";
  let parsedFacts: Record<string, unknown> = {};
  try {
    const v = JSON.parse(facts || "{}");
    if (!v || Array.isArray(v) || typeof v !== "object") factsError = "Facts must be a JSON object { … }.";
    else parsedFacts = v;
  } catch (e) {
    factsError = "Not valid JSON: " + (e as Error).message;
  }
  const changedElsewhere = draft.dirty && draft.base !== comparable(server);
  // Functional updates: several fields can change in one event without losing any.
  const set = <K extends keyof Profile>(key: K, value: Profile[K]) =>
    setDraft((d) => d && { ...d, value: { ...d.value, [key]: value }, dirty: true });
  const discard = () => {
    setDraft({ value: server, base: comparable(server), dirty: false });
    setFactsText(JSON.stringify(server.facts || {}, null, 2));
  };
  const save = () =>
    run(
      "save",
      async () => {
        await api("/profile", "PUT", {
          ...p,
          facts: parsedFacts,
          reviewed_answers: [],
          work: p.work.filter((w) => Object.values(w).some((v) => v !== "" && v !== false)),
          education: p.education.filter((e) => Object.values(e).some(Boolean)),
          evidence: p.evidence.filter((e) => e.text.trim()),
        });
        dropDraft("profile");
        await refresh();
        setDraft(null);
      },
      "Profile saved. New applications use it immediately.",
    );

  return (
    <form
      className="profile"
      onSubmit={(e) => {
        e.preventDefault();
        if (!factsError) save();
      }}
    >
      <nav className="tabs sub" aria-label="Profile sections">
        {SECTIONS.map(([id, label]) => (
          <a
            key={id}
            href={"#" + id}
            aria-current={section === id ? "page" : undefined}
            onClick={(e) => {
              e.preventDefault();
              setSection(id);
            }}
          >
            {label}
            {id === "facts" && factsError && <span className="tab-flag" aria-label="has an error">!</span>}
          </a>
        ))}
      </nav>
      {changedElsewhere && (
        <div className="inline-warn" role="status">
          Your profile changed elsewhere (for example from Telegram) after you
          started editing. Saving replaces the fields shown here.{" "}
          <button type="button" className="link" onClick={discard}>
            Discard my edits and reload
          </button>
        </div>
      )}
      {section === "identity" && <Identity p={p} set={set} />}
      {section === "experience" && <Experience p={p} set={set} />}
      {section === "facts" && (
        <section className="panel">
          <header className="panel-head">
            <h2>Confirmed facts</h2>
            <p className="muted">
              Explicit facts answer screening questions first: authorization,
              sponsorship, age, relocation and similar. Give each a clear
              time and country scope. Missing facts are asked once, never guessed.
            </p>
          </header>
          <Field label="Facts (JSON)" hint={factsError ? undefined : `${Object.keys(parsedFacts).length} facts`}>
            <textarea
              className="code"
              spellCheck={false}
              rows={16}
              value={facts}
              aria-invalid={!!factsError || undefined}
              onChange={(e) => {
                setFactsText(e.target.value);
                setDraft((d) => d && { ...d, dirty: true });
              }}
            />
          </Field>
          {factsError && (
            <p className="error-text" role="alert">
              {factsError}
            </p>
          )}
          <details className="fold">
            <summary>Example keys (the setup guide asks for most of these)</summary>
            <pre>{`{
  "work_authorization_us": true,
  "requires_future_sponsorship_us": false,
  "over_18": true,
  "willing_to_relocate": true,
  "has_security_clearance": false
}`}</pre>
          </details>
          <Field label="Approved consent statements" hint="One exact checkbox label per line. List only commitments you accept.">
            <textarea
              rows={4}
              value={p.approved_consents.join("\n")}
              onChange={(e) => set("approved_consents", e.target.value.split("\n").filter((x) => x.trim()))}
            />
          </Field>
          <div className="form-actions start">
            <button
              type="button"
              className="btn sm"
              disabled={pending !== null || draft.dirty}
              title={draft.dirty ? "Save or discard your edits first" : undefined}
              onClick={() =>
                run(
                  "organize",
                  async () => {
                    const r = await api<{ saved?: number }>("/profile/organize", "POST");
                    dropDraft("profile");
                    await refresh();
                    setDraft(null);
                    return r;
                  },
                  (r) => (r?.saved != null ? `Organized and saved ${plural(r.saved, "fact")}.` : "Saved facts organized."),
                )
              }
            >
              {pending === "organize" ? "Organizing…" : "Organize saved facts with AI"}
            </button>
            <span className="muted sm">
              {draft.dirty ? "Save or discard your edits first. " : ""}Only restructures facts you already saved; nothing is invented.
            </span>
          </div>
        </section>
      )}
      {section === "policies" && <Policies p={p} set={set} />}
      {section === "rules" && <AnswerRulesEditor p={p} set={set} />}
      {section === "preferences" && <Preferences p={p} set={set} />}
      {draft.dirty && (
      <div className="save-bar dirty">
        <span className="muted sm">
          {draft.dirty
            ? factsError
              ? "Fix the facts JSON to save."
              : "Unsaved changes are kept while you move around."
            : "All changes saved."}
        </span>
        {draft.dirty && (
          <button type="button" className="btn quiet" onClick={discard}>
            Discard
          </button>
        )}
        <button className="btn primary" disabled={!draft.dirty || !!factsError || pending !== null}>
          {pending === "save" ? "Saving…" : "Save profile"}
        </button>
      </div>
      )}
    </form>
  );
}

type Setter = <K extends keyof Profile>(key: K, value: Profile[K]) => void;

function Text({
  p,
  set,
  k,
  label,
  type = "text",
  hint,
  autoComplete,
}: {
  p: Profile;
  set: Setter;
  k: keyof Profile;
  label: string;
  type?: string;
  hint?: string;
  autoComplete?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type={type}
        autoComplete={autoComplete || "off"}
        value={String(p[k] ?? "")}
        onChange={(e) => set(k, e.target.value as never)}
      />
    </Field>
  );
}

function Identity({ p, set }: { p: Profile; set: Setter }) {
  const addr = p.address;
  const setAddr = (k: keyof Profile["address"], v: string) => set("address", { ...addr, [k]: v });
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Name and contact</h2>
          <p className="muted">Filled directly into forms. First and last names are used exactly as written.</p>
        </header>
        <div className="form-grid">
          <Text p={p} set={set} k="name" label="Full legal name" />
          <Text p={p} set={set} k="preferred_name" label="Preferred name" />
          <Text p={p} set={set} k="first_name" label="Legal first name" />
          <Text p={p} set={set} k="middle_name" label="Middle name" />
          <Text p={p} set={set} k="last_name" label="Legal last name" />
          <Text p={p} set={set} k="email" label="Email" type="email" />
          <Text p={p} set={set} k="phone" label="Phone" type="tel" />
          <Text p={p} set={set} k="phone_country_code" label="Phone country code" hint="“+1” or the country name an employer lists." />
          <Text p={p} set={set} k="phone_type" label="Phone type" />
          <Text p={p} set={set} k="location" label="Current location" hint="City, state" />
          <Text p={p} set={set} k="linkedin" label="LinkedIn URL" type="url" />
          <Text p={p} set={set} k="github" label="GitHub URL" type="url" />
          <Text p={p} set={set} k="website" label="Website or portfolio" type="url" />
        </div>
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Address</h2>
        </header>
        <div className="form-grid">
          {(
            [
              ["line1", "Address line 1", true],
              ["line2", "Address line 2", true],
              ["city", "City"],
              ["state", "State or province"],
              ["postal_code", "Postal code"],
              ["county", "County"],
              ["country", "Country"],
            ] as [keyof Profile["address"], string, boolean?][]
          ).map(([k, label, wide]) => (
            <Field key={k} label={label} wide={wide}>
              <input value={addr[k] || ""} onChange={(e) => setAddr(k, e.target.value)} />
            </Field>
          ))}
        </div>
      </section>
    </>
  );
}

function Experience({ p, set }: { p: Profile; set: Setter }) {
  const { pending, run } = useAction();
  const [proposal, setProposal] = useState<{
    work: WorkEntry[];
    education: EducationEntry[];
    skills: string[];
    contact: Partial<Profile>;
  } | null>(null);
  const hasWork = (w: WorkEntry) =>
    p.work.some((x) => x.company.trim().toLowerCase() === w.company.trim().toLowerCase() && x.title.trim().toLowerCase() === w.title.trim().toLowerCase());
  const hasEdu = (e: EducationEntry) =>
    p.education.some((x) => x.school.trim().toLowerCase() === e.school.trim().toLowerCase() && x.degree.trim().toLowerCase() === e.degree.trim().toLowerCase());
  const updateWork = (i: number, patch: Partial<WorkEntry>) =>
    set("work", p.work.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  const updateEdu = (i: number, patch: Partial<EducationEntry>) =>
    set("education", p.education.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const newWork = proposal?.work.filter((w) => !hasWork(w)) || [];
  const newEdu = proposal?.education.filter((e) => !hasEdu(e)) || [];
  return (
    <>
      <section className="panel">
        <header className="panel-head row">
          <div>
            <h2>From your resume</h2>
            <p className="muted">Proposes roles and degrees found in your uploaded resume. Nothing is replaced; you choose what to add.</p>
          </div>
          <button
            type="button"
            className="btn sm"
            disabled={pending !== null}
            onClick={() =>
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
              })
            }
          >
            {pending === "extract" ? "Reading resume…" : "Extract from resume"}
          </button>
        </header>
        {proposal && (
          <div className="proposal">
            <p>
              Found {plural(proposal.work.length, "role")} and {plural(proposal.education.length, "degree")};{" "}
              {newWork.length + newEdu.length ? `${newWork.length + newEdu.length} not yet in your profile.` : "all are already in your profile."}
            </p>
            {[...newWork.map((w) => ({ kind: "work" as const, w })), ...newEdu.map((e) => ({ kind: "edu" as const, e }))].map((item, i) => (
              <div className="proposal-item" key={i}>
                <span>
                  {item.kind === "work" ? (
                    <>
                      <strong>{item.w.title}</strong> · {item.w.company} · {item.w.start}–{item.w.current ? "present" : item.w.end}
                    </>
                  ) : (
                    <>
                      <strong>{item.e.degree}</strong> {item.e.field_of_study && `in ${item.e.field_of_study}`} · {item.e.school} · {item.e.end}
                    </>
                  )}
                </span>
                <button
                  type="button"
                  className="btn sm"
                  onClick={() =>
                    item.kind === "work" ? set("work", [...p.work, item.w]) : set("education", [...p.education, item.e])
                  }
                >
                  Add
                </button>
              </div>
            ))}
            <div className="form-actions start">
              {newWork.length + newEdu.length > 1 && (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    set("work", [...p.work, ...newWork]);
                    set("education", [...p.education, ...newEdu]);
                  }}
                >
                  Add all new
                </button>
              )}
              {Object.keys(proposal.contact).length > 0 && (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    for (const [k, v] of Object.entries(proposal.contact)) set(k as keyof Profile, v as never);
                  }}
                >
                  Fill {plural(Object.keys(proposal.contact).length, "empty contact field")}
                </button>
              )}
              {proposal.skills.length > 0 && !p.skills.length && (
                <button type="button" className="btn sm" onClick={() => set("skills", proposal.skills)}>
                  Use {plural(proposal.skills.length, "skill")}
                </button>
              )}
              <button type="button" className="btn sm quiet" onClick={() => setProposal(null)}>
                Done
              </button>
            </div>
          </div>
        )}
      </section>
      <section className="panel">
        <header className="panel-head row">
          <div>
            <h2>Work history</h2>
            <p className="muted">Dates as YYYY-MM, or YYYY when the month isn't known.</p>
          </div>
          <button type="button" className="btn sm" onClick={() => set("work", [...p.work, { ...EMPTY_WORK }])}>
            <Plus size={14} aria-hidden="true" /> Add role
          </button>
        </header>
        {p.work.length === 0 && <p className="muted sm">No roles yet.</p>}
        {p.work.map((w, i) => (
          <fieldset className="entry" key={i}>
            <legend>{w.title || w.company ? `${w.title || "Role"} · ${w.company || "Company"}` : `Role ${i + 1}`}</legend>
            <div className="form-grid">
              <Field label="Company">
                <input value={w.company} onChange={(e) => updateWork(i, { company: e.target.value })} />
              </Field>
              <Field label="Title">
                <input value={w.title} onChange={(e) => updateWork(i, { title: e.target.value })} />
              </Field>
              <Field label="Location">
                <input value={w.location} onChange={(e) => updateWork(i, { location: e.target.value })} />
              </Field>
              <Field label="Start">
                <input value={w.start} placeholder="YYYY-MM" onChange={(e) => updateWork(i, { start: e.target.value })} />
              </Field>
              <Field label="End">
                <input value={w.end} placeholder={w.current ? "Present" : "YYYY-MM"} disabled={w.current} onChange={(e) => updateWork(i, { end: e.target.value })} />
              </Field>
              <label className="check">
                <input type="checkbox" checked={w.current} onChange={(e) => updateWork(i, { current: e.target.checked, end: e.target.checked ? "" : w.end })} />
                <span>Current role</span>
              </label>
              <Field label="What you did" wide>
                <textarea rows={3} value={w.description} onChange={(e) => updateWork(i, { description: e.target.value })} />
              </Field>
            </div>
            <Confirm label="Remove role" confirm="Remove" onConfirm={() => set("work", p.work.filter((_, j) => j !== i))} />
          </fieldset>
        ))}
      </section>
      <section className="panel">
        <header className="panel-head row">
          <div>
            <h2>Education</h2>
            <p className="muted">List every degree; each is answered separately on forms.</p>
          </div>
          <button type="button" className="btn sm" onClick={() => set("education", [...p.education, { ...EMPTY_EDU }])}>
            <Plus size={14} aria-hidden="true" /> Add degree
          </button>
        </header>
        {p.education.length === 0 && <p className="muted sm">No degrees yet.</p>}
        {p.education.map((x, i) => (
          <fieldset className="entry" key={i}>
            <legend>{x.degree || x.school ? `${x.degree || "Degree"} · ${x.school || "School"}` : `Degree ${i + 1}`}</legend>
            <div className="form-grid">
              <Field label="School" wide>
                <input value={x.school} onChange={(e) => updateEdu(i, { school: e.target.value })} />
              </Field>
              <Field label="Degree">
                <input value={x.degree} onChange={(e) => updateEdu(i, { degree: e.target.value })} />
              </Field>
              <Field label="Field of study">
                <input value={x.field_of_study} onChange={(e) => updateEdu(i, { field_of_study: e.target.value })} />
              </Field>
              <Field label="Start">
                <input value={x.start} placeholder="YYYY-MM" onChange={(e) => updateEdu(i, { start: e.target.value })} />
              </Field>
              <Field label="End or expected" hint="A future date means the degree is in progress.">
                <input value={x.end} placeholder="YYYY-MM" onChange={(e) => updateEdu(i, { end: e.target.value })} />
              </Field>
              <Field label="GPA">
                <input value={x.gpa} onChange={(e) => updateEdu(i, { gpa: e.target.value })} />
              </Field>
            </div>
            <Confirm label="Remove degree" confirm="Remove" onConfirm={() => set("education", p.education.filter((_, j) => j !== i))} />
          </fieldset>
        ))}
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Skills and languages</h2>
        </header>
        <div className="form-grid">
          <Field label="Skills" hint="Comma separated" wide>
            <input value={p.skills.join(", ")} onChange={(e) => set("skills", list(e.target.value))} />
          </Field>
          <Field label="Languages" hint="Comma separated" wide>
            <input value={(p.languages || []).join(", ")} onChange={(e) => set("languages", list(e.target.value))} />
          </Field>
        </div>
      </section>
      <section className="panel">
        <header className="panel-head row">
          <div>
            <h2>Stories and evidence</h2>
            <p className="muted">
              Projects, achievements and examples used to write supported narrative answers. Nothing beyond these notes is claimed.
            </p>
          </div>
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              const used = new Set(p.evidence.map((e) => e.id));
              let n = p.evidence.length + 1;
              while (used.has("note-" + n)) n++;
              set("evidence", [...p.evidence, { id: "note-" + n, text: "" }]);
            }}
          >
            <Plus size={14} aria-hidden="true" /> Add note
          </button>
        </header>
        {p.evidence.length === 0 && <p className="muted sm">No notes yet.</p>}
        {p.evidence.map((note, i) => (
          <div className="note-row" key={note.id}>
            <Field label={`Note ${i + 1}`} hint={<span className="mono">{note.id}</span>} wide>
              <textarea
                rows={3}
                maxLength={8000}
                value={note.text}
                onChange={(e) => set("evidence", p.evidence.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
              />
            </Field>
            <Confirm label="Remove" confirm="Remove note" onConfirm={() => set("evidence", p.evidence.filter((_, j) => j !== i))} />
          </div>
        ))}
      </section>
    </>
  );
}

function Check({
  checked,
  onChange,
  title,
  children,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <label className="check wide">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <strong>{title}</strong>
        {children && <small>{children}</small>}
      </span>
    </label>
  );
}

function Policies({ p, set }: { p: Profile; set: Setter }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>What ApplyPilot may do for you</h2>
        <p className="muted">These decide which steps run unattended.</p>
      </header>
      <div className="form-grid">
        <Check checked={p.autonomous} onChange={(v) => set("autonomous", v)} title="Apply autonomously">
          Applications missing a required personal fact wait for your answer while other jobs continue.
        </Check>
        <Check checked={p.allow_account_creation} onChange={(v) => set("allow_account_creation", v)} title="Create and reuse employer accounts">
          Needed for Workday, iCIMS and other portals that require an account.
        </Check>
        <Check checked={p.allow_application_consents} onChange={(v) => set("allow_application_consents", v)} title="Accept privacy notices and certify accuracy">
          Certifies that saved information is accurate where a form requires it.
        </Check>
        <Check checked={p.accept_all_application_terms} onChange={(v) => set("accept_all_application_terms", v)} title="Accept application terms and conditions" />
        <Check
          checked={!!p.accept_required_marketing}
          onChange={(v) => set("accept_required_marketing", v)}
          title="Accept required marketing consent"
        >
          Only when a form refuses to continue without it.
        </Check>
        <Check checked={p.decline_demographics} onChange={(v) => set("decline_demographics", v)} title="Decline voluntary demographic questions">
          Chooses “prefer not to say” unless you confirmed a specific answer.
        </Check>
      </div>
    </section>
  );
}

const EDUCATION_STEPS: [EducationStep, string, string][] = [
  ["closest", "Closest match", "Your fallback major (below) and the same-level degree first, then the most similar option."],
  ["pick3", "Assistant chooses from the 3 closest", "Only options close to your value are offered to the assistant; unrelated ones never are."],
  ["other", "“Other” or “Not listed”", "When the list offers one."],
  ["ask", "Ask me", "The application waits for your answer while other jobs continue."],
];
const IDENTITY_TOPICS: [string, string][] = [
  ["gender", "Gender"],
  ["race", "Race or ethnicity"],
  ["hispanic", "Hispanic or Latino"],
  ["veteran", "Veteran status"],
  ["disability", "Disability"],
  ["other", "Other identity questions"],
];

function rulesWithDefaults(p: Profile): AnswerRules {
  const saved = p.answer_rules || {};
  const demographics: Record<string, DemographicRule> = {};
  for (const [key] of IDENTITY_TOPICS) {
    demographics[key] = {
      decline_first: p.decline_demographics,
      answer: "",
      when_missing: "ask",
      ...(saved.demographics?.[key] || {}),
    };
  }
  return {
    education: saved.education || ["closest", "pick3", "other", "ask"],
    major_fallback: saved.major_fallback ?? "",
    source_order: saved.source_order ?? [],
    source_any_channel: saved.source_any_channel ?? false,
    salary_stance: saved.salary_stance ?? "",
    travel_willingness: saved.travel_willingness ?? "",
    employment_type_sought: saved.employment_type_sought ?? "",
    join_talent_communities: saved.join_talent_communities ?? false,
    demographics,
    guest_apply_first: saved.guest_apply_first ?? true,
    open_agreement_links: saved.open_agreement_links ?? true,
  };
}

function AnswerRulesEditor({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const update = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  const topic = (key: string, next: Partial<DemographicRule>) =>
    update({ demographics: { ...rules.demographics, [key]: { ...rules.demographics[key], ...next } } });
  // Enabled steps keep their saved order; disabled ones follow in their usual place.
  const ordered = [
    ...rules.education,
    ...EDUCATION_STEPS.map(([s]) => s).filter((s) => !rules.education.includes(s)),
  ];
  const move = (step: EducationStep, by: number) => {
    const list = [...rules.education];
    const i = list.indexOf(step);
    const j = i + by;
    if (i < 0 || j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    update({ education: list });
  };
  const toggle = (step: EducationStep, on: boolean) =>
    update({
      education: on
        ? ordered.filter((s) => s === step || rules.education.includes(s))
        : rules.education.filter((s) => s !== step),
    });
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Degree, major or school not listed</h2>
          <p className="muted">What to choose when the form’s list doesn’t include your exact value. Enabled steps run in this order.</p>
        </header>
        <ol className="rule-steps">
          {ordered.map((step) => {
            const [, title, note] = EDUCATION_STEPS.find(([s]) => s === step)!;
            const on = rules.education.includes(step);
            const i = rules.education.indexOf(step);
            return (
              <li key={step} className={on ? "" : "off"}>
                <label className="check">
                  <input type="checkbox" checked={on} onChange={(e) => toggle(step, e.target.checked)} />
                  <span>
                    <strong>{title}</strong>
                    <small>{note}</small>
                  </span>
                </label>
                {on && (
                  <span className="rule-order">
                    <button className="btn ghost sm" aria-label={`Move ${title} earlier`} disabled={i === 0} onClick={() => move(step, -1)}>
                      ↑
                    </button>
                    <button
                      className="btn ghost sm"
                      aria-label={`Move ${title} later`}
                      disabled={i === rules.education.length - 1}
                      onClick={() => move(step, 1)}
                    >
                      ↓
                    </button>
                  </span>
                )}
              </li>
            );
          })}
        </ol>
        <div className="form-grid">
          <Field
            label="Fallback major"
            hint="Chosen when a form’s list lacks your major, for example Computer Science. Leave empty to choose none."
          >
            <input value={rules.major_fallback} maxLength={100} onChange={(e) => update({ major_fallback: e.target.value })} />
          </Field>
          <Field
            label="“How did you hear about us” order"
            hint="Used when your own source isn’t offered. Comma separated, best first: LinkedIn, company website, Indeed, job board… Empty asks you."
            wide
          >
            <input value={rules.source_order.join(", ")} onChange={(e) => update({ source_order: list(e.target.value) })} />
          </Field>
          <Check checked={rules.source_any_channel} onChange={(v) => update({ source_any_channel: v })} title="Then any other plain channel">
            Accepts an offered channel that claims no referral or insider relationship.
          </Check>
        </div>
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Self-identification questions</h2>
          <p className="muted">
            Each question runs: “prefer not to say” when on and offered → your answer → if your answer isn’t listed, the
            fallback you choose. Nothing is guessed from other facts.
          </p>
        </header>
        <div className="table-wrap">
          <table className="table rules-table">
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Prefer not to say when offered</th>
                <th scope="col">Your answer</th>
                <th scope="col">If your answer isn’t listed</th>
              </tr>
            </thead>
            <tbody>
              {IDENTITY_TOPICS.map(([key, label]) => {
                const r = rules.demographics[key];
                return (
                  <tr key={key}>
                    <th scope="row">{label}</th>
                    <td data-label="Prefer not to say">
                      <input
                        type="checkbox"
                        aria-label={`Prefer not to say for ${label}`}
                        checked={r.decline_first}
                        onChange={(e) => topic(key, { decline_first: e.target.checked })}
                      />
                    </td>
                    <td data-label="Your answer">
                      <input
                        aria-label={`Your answer for ${label}`}
                        placeholder="From Knowledge"
                        value={r.answer}
                        onChange={(e) => topic(key, { answer: e.target.value })}
                      />
                    </td>
                    <td data-label="If not listed">
                      <select
                        aria-label={`If your ${label.toLowerCase()} answer isn’t listed`}
                        value={r.when_missing}
                        onChange={(e) => topic(key, { when_missing: e.target.value as DemographicRule["when_missing"] })}
                      >
                        <option value="closest">Closest option</option>
                        <option value="other">“Other”</option>
                        <option value="ask">Ask me</option>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Buttons and steps</h2>
        </header>
        <div className="form-grid">
          <Check checked={rules.guest_apply_first} onChange={(v) => update({ guest_apply_first: v })} title="Apply as a guest when the site allows it">
            Skips sign-in and account creation when an employer offers “Apply without an account”.
          </Check>
          <Check checked={rules.join_talent_communities} onChange={(v) => update({ join_talent_communities: v })} title="Join talent communities">
            Opts in when a form offers to keep your data on file for other roles. Off declines.
          </Check>
          <Check checked={rules.open_agreement_links} onChange={(v) => update({ open_agreement_links: v })} title="Open linked documents before accepting an agreement">
            Some sites (Dayforce) accept an agreement only after its privacy and terms links were opened.
          </Check>
        </div>
        <p className="muted sm rules-note">Account creation, consents and terms are under Policies. CAPTCHA solving is under System › Helpers.</p>
      </section>
    </>
  );
}

function Preferences({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const rule = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Targets and answers about the role</h2>
      </header>
      <div className="form-grid">
        <Field
          label="Target roles"
          hint="Comma separated. Usual alternate titles count too: Software Development Engineer also finds Software Engineer and SDE."
          wide
        >
          <input value={p.target_roles.join(", ")} onChange={(e) => set("target_roles", list(e.target.value))} />
        </Field>
        <Field label="Apply first to" hint="Order of ready jobs in a batch">
          <select
            value={p.job_priority ?? "new_grad_first"}
            onChange={(e) => set("job_priority", e.target.value as Profile["job_priority"])}
          >
            <option value="new_grad_first">New grad roles, then best fit</option>
            <option value="best_fit_first">Best fit, any level</option>
          </select>
        </Field>
        <Field label="Target locations" hint="Comma separated" wide>
          <input value={p.target_locations.join(", ")} onChange={(e) => set("target_locations", list(e.target.value))} />
        </Field>
        <Field label="Exclude titles containing" hint="Comma separated keywords" wide>
          <input value={p.excluded_keywords.join(", ")} onChange={(e) => set("excluded_keywords", list(e.target.value))} />
        </Field>
        <Field label="Minimum fit for ranking" hint="0–100">
          <input type="number" min={0} max={100} value={p.minimum_fit} onChange={(e) => set("minimum_fit", Number(e.target.value))} />
        </Field>
        <Field label="Salary expectation" hint="Leave blank to skip optional salary questions.">
          <input value={p.salary_expectation} onChange={(e) => set("salary_expectation", e.target.value)} />
        </Field>
        <Field label="Stance on desired pay" hint="Said when pay is asked and no salary is saved, for example Negotiable. Blank says nothing.">
          <input value={rules.salary_stance} maxLength={60} onChange={(e) => rule({ salary_stance: e.target.value })} />
        </Field>
        <Field label="Willingness to travel" hint="For example “open to travel”. Blank claims nothing.">
          <input value={rules.travel_willingness} maxLength={100} onChange={(e) => rule({ travel_willingness: e.target.value })} />
        </Field>
        <Field label="Employment sought" hint="For example “full-time early-career employment”. Blank claims nothing.">
          <input value={rules.employment_type_sought} maxLength={100} onChange={(e) => rule({ employment_type_sought: e.target.value })} />
        </Field>
        <Check checked={!!p.include_internships} onChange={(v) => set("include_internships", v)} title="Include internships and co-ops">
          Off applies to full-time roles only.
        </Check>
        <Field label="Availability or notice">
          <input value={p.availability} onChange={(e) => set("availability", e.target.value)} />
        </Field>
        <Field label="How you heard about the job" hint="Answer to “How did you hear about us?”">
          <input value={p.application_source} onChange={(e) => set("application_source", e.target.value)} />
        </Field>
      </div>
    </section>
  );
}

// ---------- Answers (knowledge base) ----------
export function LearnedAnswers() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const entries = usePoll<{ entries: ReviewedAnswer[] }>(
    (signal) => api("/knowledge?q=" + encodeURIComponent(term), "GET", undefined, signal),
    120000,
    [term],
  );
  const { pending, run } = useAction();
  const [question, setQuestion] = useDraft("kb-question", "");
  const [answer, setAnswer] = useDraft("kb-answer", "");
  const [scope, setScope] = useDraft<ReviewedAnswer["scope"]>("kb-scope", "personal");
  const [employer, setEmployer] = useDraft("kb-employer", "");
  const rows = entries.data?.entries || [];
  const paging = usePagination(rows, term, 25);
  return (
    <>
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "add",
            async () => {
              await api("/knowledge", "POST", {
                question: question.trim(),
                answer: answer.trim(),
                options: [],
                scope,
                employer: scope === "employer" ? employer.trim() : "",
              });
              setQuestion("");
              setAnswer("");
              await entries.refresh();
            },
            "Answer saved.",
          );
        }}
      >
        <header className="panel-head">
          <h2>Add an answer</h2>
          <p className="muted">
            Answers you give in Needs you, on Telegram or here. Reusable answers apply at every employer;
            employer answers only where you gave them.
          </p>
        </header>
        <div className="form-grid">
          <Field label="Question" wide>
            <input required minLength={2} maxLength={2000} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Do you have experience with Kafka?" />
          </Field>
          <Field label="Answer" wide>
            <textarea required rows={2} maxLength={8000} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          </Field>
          <Field label="Applies to">
            <select value={scope} onChange={(e) => setScope(e.target.value as ReviewedAnswer["scope"])}>
              <option value="personal">Every employer</option>
              <option value="employer">One employer</option>
            </select>
          </Field>
          {scope === "employer" && (
            <Field label="Employer">
              <input required value={employer} onChange={(e) => setEmployer(e.target.value)} />
            </Field>
          )}
        </div>
        <div className="form-actions">
          <button className="btn primary" disabled={pending !== null}>
            {pending === "add" ? "Saving…" : "Save answer"}
          </button>
        </div>
      </form>
      <section className="panel">
        <header className="panel-head row">
          <h2>Saved answers</h2>
          <label className="search">
            <Search size={15} aria-hidden="true" />
            <input type="search" aria-label="Search saved answers" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </header>
        <ErrorLine error={entries.error} retry={entries.refresh} />
        {!entries.data && !entries.error ? (
          <Loading />
        ) : rows.length ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Question</th>
                    <th scope="col">Answer</th>
                    <th scope="col">Applies to</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {paging.items.map((e) => (
                    <tr key={e.id}>
                      <td className="wrap">{e.question}</td>
                      <td className="wrap">{e.answer}</td>
                      <td data-label="Applies to">
                        {e.scope === "personal" ? "Every employer" : e.employer || "One employer"}
                        <small className="muted"> · {e.layer === "narrative" ? "narrative" : "fact"}</small>
                      </td>
                      <td className="cell-actions">
                        <Confirm
                          label="Forget"
                          confirm="Forget answer"
                          pending={pending === e.id}
                          onConfirm={() =>
                            run(
                              e.id,
                              async () => {
                                await api("/knowledge/" + encodeURIComponent(e.id), "DELETE");
                                await entries.refresh();
                              },
                              "Answer forgotten. It won't be reused.",
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination paging={paging} label="Answer pages" />
          </>
        ) : (
          <Empty title={term ? "No saved answers match" : "No saved answers yet"}>
            {term ? null : "Answers you give to genuine questions are saved here and reused."}
          </Empty>
        )}
      </section>
    </>
  );
}

// ---------- Accounts ----------
const ACCOUNT_STATES: Record<string, string> = {
  created_locally: "Created, not signed in yet",
  signing_in: "Signing in",
  authenticated: "Signed in",
  verification_pending: "Waiting for email verification",
  reset_requested: "Password reset requested",
  password_reset: "Password reset",
  exists: "Already registered",
  locked: "Locked",
};
const accountHost = (a: AccountRow) =>
  a.origin.replace(/^https?:\/\//, "") || a.id.replace(/^employer_account:/, "");

export function AccountSettings() {
  const { snapshot } = useWorkspace();
  const summary = usePoll<AccountSummary>((signal) => api("/accounts", "GET", undefined, signal), 60000);
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const accounts = useMemo(
    () => (summary.data?.accounts || []).filter((a) => !q || accountHost(a).toLowerCase().includes(q)),
    [summary.data, q],
  );
  const paging = usePagination(accounts, q, 25);
  const cfg = snapshot.config;
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>How sign-in works</h2>
          <p className="muted">
            Employer accounts are created or reused with one login. Passwords stay encrypted on the
            server and are never shown here. Your mailbox handles verification codes and resets.
          </p>
        </header>
        <KeyValues
          items={[
            ["Login email", cfg.account_email || "Profile email"],
            ["Shared password", cfg.account ? "Set on the server" : "Generated and encrypted"],
            ["Gmail IMAP", cfg.imap ? "Configured" : <span className="warn-text">Not configured</span>],
            ["Captcha service", cfg.twocaptcha ? "Configured" : "Not configured"],
            ["Account creation", snapshot.profile.allow_account_creation ? "Allowed" : <span className="warn-text">Off (Profile › Policies)</span>],
          ]}
        />
        {!!summary.data?.password_problems.length && (
          <p className="inline-warn">Some portals may reject the shared password: {summary.data.password_problems.join("; ")}.</p>
        )}
        <div className="form-actions start">
          <button
            className="btn sm"
            disabled={!cfg.imap || pending !== null}
            onClick={() => run("imap", () => api("/inbox/check", "POST"), "Gmail IMAP sign-in works.")}
          >
            {pending === "imap" ? "Testing…" : "Test Gmail connection"}
          </button>
          {!cfg.imap && <span className="muted sm">Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD on the server, then restart.</span>}
        </div>
      </section>
      <section className="panel">
        <header className="panel-head row">
          <div>
            <h2>Employer accounts</h2>
            <p className="muted">{summary.data ? plural(summary.data.accounts.length, "account") : "…"}</p>
          </div>
          <label className="search">
            <Search size={15} aria-hidden="true" />
            <input type="search" aria-label="Search employer accounts" placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
        </header>
        <ErrorLine error={summary.error} retry={summary.refresh} />
        {!summary.data && !summary.error ? (
          <Loading />
        ) : accounts.length ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Employer site</th>
                    <th scope="col">State</th>
                    <th scope="col">Updated</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {paging.items.map((a) => (
                    <tr key={a.id}>
                      <td className="mono-ish">{accountHost(a)}</td>
                      <td data-label="State">
                        {ACCOUNT_STATES[a.state] || a.state.replaceAll("_", " ") || "Unknown"}
                        {a.reset_requested_at ? <small className="muted"> · reset {ago(a.reset_requested_at * 1000)}</small> : null}
                      </td>
                      <td data-label="Updated">{ago(a.updated_at)}</td>
                      <td className="cell-actions">
                        <Confirm
                          label="Forget"
                          confirm="Forget record"
                          note="The next application signs in again or creates the account."
                          pending={pending === a.id}
                          onConfirm={() =>
                            run(
                              a.id,
                              async () => {
                                await api("/accounts/" + encodeURIComponent(a.id), "DELETE");
                                await summary.refresh();
                              },
                              "Account record forgotten.",
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination paging={paging} label="Account pages" />
          </>
        ) : (
          <Empty title={q ? "No accounts match" : "No employer accounts yet"}>
            {q ? null : "They appear after the first application that needs one."}
          </Empty>
        )}
      </section>
      <SessionImport />
    </>
  );
}

function SessionImport() {
  const { pending, run } = useAction();
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState(0);
  return (
    <details className="panel fold">
      <summary>
        <strong>Import an employer session</strong>
        <span className="muted sm"> · only for SMS or passkey sign-ins</span>
      </summary>
      <p className="muted sm">
        Run <code>jobpilot login https://employer.example</code> on your own machine, or import a Playwright
        storage-state JSON. Only cookies for that exact employer host are accepted; the file stays in the
        private data directory.
      </p>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!file) return;
          const f = new FormData();
          f.append("url", url);
          f.append("file", file);
          run(
            "session",
            async () => {
              const r = await api<{ host: string }>("/sessions", "POST", f);
              setFile(null);
              setKey((k) => k + 1);
              return r;
            },
            (r) => r && `Session saved for ${r.host}.`,
          );
        }}
      >
        <Field label="Employer URL">
          <input type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field label="Storage-state JSON">
          <input key={key} type="file" accept="application/json" required onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </Field>
        <button className="btn" disabled={!file || pending !== null}>
          {pending === "session" ? "Importing…" : "Import session"}
        </button>
      </form>
    </details>
  );
}
