import React, { useEffect, useId, useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { dropDraft, useAction, useDraft, useNotify, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import type {
  AccountRow,
  AccountSummary,
  AnswerRules,
  EmployerAccountStatus,
  DemographicRule,
  EducationEntry,
  EducationStep,
  Profile,
  ReviewedAnswer,
  WorkEntry,
} from "./types";
import { ago, Confirm, Empty, ErrorLine, External, Field, KeyValues, Loading, plural, stamp } from "./ui";
import { ProfilePhoto } from "./profile-photo";
import { Actions, ConfirmButton, Facts, FormGrid, Lede, ListInput, SaveBar, SecretInput } from "./setup-kit";
import { Button, Drawer, Empty as AxEmpty, Input, Panel, Pill, Table, Tabs, type PillTone } from "./ui/index";

// ---------- Profile ----------
type ProfileDraft = { value: Profile; base: string; dirty: boolean };
// Learned answers are server-owned; they never travel back in a profile save.
const comparable = (p: Profile) => {
  const { reviewed_answers: _r, approved_answers: _a, ...rest } = p as Profile & Record<string, unknown>;
  return JSON.stringify(rest);
};
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

// A profile that arrives thin (a new account, an older server) is filled in so no section reads a missing list.
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
export function normalizeProfile(raw: Partial<Profile> | null | undefined): Profile {
  const p = (raw || {}) as Partial<Profile>;
  const text = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
  return {
    ...(p as Profile),
    name: text(p.name),
    email: text(p.email),
    phone: text(p.phone),
    location: text(p.location),
    linkedin: text(p.linkedin),
    website: text(p.website),
    github: text(p.github),
    first_name: text(p.first_name),
    last_name: text(p.last_name),
    middle_name: text(p.middle_name),
    preferred_name: text(p.preferred_name),
    phone_country_code: text(p.phone_country_code),
    phone_type: text(p.phone_type),
    salary_expectation: text(p.salary_expectation),
    availability: text(p.availability),
    application_source: text(p.application_source),
    address: { line1: "", line2: "", city: "", state: "", postal_code: "", country: "", county: "", ...(p.address || {}) },
    work: arr<WorkEntry>(p.work).map((w) => ({ ...EMPTY_WORK, ...w })),
    education: arr<EducationEntry>(p.education).map((e) => ({ ...EMPTY_EDU, ...e })),
    skills: arr<string>(p.skills),
    languages: arr<string>(p.languages),
    evidence: arr<Profile["evidence"][number]>(p.evidence),
    target_roles: arr<string>(p.target_roles),
    target_locations: arr<string>(p.target_locations),
    excluded_keywords: arr<string>(p.excluded_keywords),
    approved_consents: arr<string>(p.approved_consents),
    facts: p.facts && typeof p.facts === "object" && !Array.isArray(p.facts) ? p.facts : {},
    minimum_fit: Number.isFinite(Number(p.minimum_fit)) ? Number(p.minimum_fit) : 70,
    decline_demographics: !!p.decline_demographics,
    allow_account_creation: !!p.allow_account_creation,
    allow_application_consents: !!p.allow_application_consents,
    accept_all_application_terms: !!p.accept_all_application_terms,
    autonomous: !!p.autonomous,
  };
}

export function ProfileSettings() {
  const { snapshot, refresh, route } = useWorkspace();
  const server = useMemo(() => normalizeProfile(snapshot.profile), [snapshot.profile]);
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
      className="ax-stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (!factsError) save();
      }}
    >
      <Tabs
        label="Profile sections"
        value={section}
        onChange={setSection}
        tabs={SECTIONS.map(([id, label]) => ({ id, label: id === "facts" && factsError ? label + " (fix)" : label }))}
      />
      {changedElsewhere && (
        <div className="ax-notice" role="status">
          <span>
            Your profile changed elsewhere (for example from Telegram) after you started editing. Saving replaces the
            fields shown here.
          </span>
          <Button size="sm" onClick={discard}>
            Discard my edits and reload
          </Button>
        </div>
      )}
      {section === "identity" && <Identity p={p} set={set} />}
      {section === "experience" && <Experience p={p} set={set} />}
      {section === "facts" && (
        <Panel title="Confirmed facts">
          <Lede>Explicit facts answer screening questions first: authorization,
              sponsorship, age, relocation and similar. Give each a clear
              time and country scope. Missing facts are asked once, never guessed.</Lede>
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
            <Button
              type="button"
              size="sm"
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
            </Button>
            <span className="muted sm">
              {draft.dirty ? "Save or discard your edits first. " : ""}Only restructures facts you already saved; nothing is invented.
            </span>
          </div>
        </Panel>
      )}
      {section === "policies" && <Policies p={p} set={set} />}
      {section === "rules" && <AnswerRulesEditor p={p} set={set} />}
      {section === "preferences" && <Preferences p={p} set={set} />}
      {draft.dirty && (
        <SaveBar
          dirty
          saving={pending === "save"}
          onSave={() => {
            if (!factsError) save();
          }}
          onDiscard={discard}
          disabled={!!factsError}
          label="Save profile"
          note={factsError ? "Fix the facts JSON to save." : "Unsaved changes are kept while you move around."}
        />
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
      <ProfilePhoto name={p.name || [p.first_name, p.last_name].filter(Boolean).join(" ")} />
      <Panel title="Name and contact">
        <Lede>Filled directly into forms. First and last names are used exactly as written.</Lede>
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
      </Panel>
      <Panel title="Address">
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
      </Panel>
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
      <Panel title="From your resume" actions={<><Button
            type="button"
            size="sm"
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
          </Button></>}>
        <Lede>Proposes roles and degrees found in your uploaded resume. Nothing is replaced; you choose what to add.</Lede>
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
                <Button
                  type="button"
                  size="sm"
                  onClick={() =>
                    item.kind === "work" ? set("work", [...p.work, item.w]) : set("education", [...p.education, item.e])
                  }
                >
                  Add
                </Button>
              </div>
            ))}
            <div className="form-actions start">
              {newWork.length + newEdu.length > 1 && (
                <Button
                  type="button"
                  size="sm"
                  onClick={() => {
                    set("work", [...p.work, ...newWork]);
                    set("education", [...p.education, ...newEdu]);
                  }}
                >
                  Add all new
                </Button>
              )}
              {Object.keys(proposal.contact).length > 0 && (
                <Button
                  type="button"
                  size="sm"
                  onClick={() => {
                    for (const [k, v] of Object.entries(proposal.contact)) set(k as keyof Profile, v as never);
                  }}
                >
                  Fill {plural(Object.keys(proposal.contact).length, "empty contact field")}
                </Button>
              )}
              {proposal.skills.length > 0 && !p.skills.length && (
                <Button type="button" size="sm" onClick={() => set("skills", proposal.skills)}>
                  Use {plural(proposal.skills.length, "skill")}
                </Button>
              )}
              <Button type="button" size="sm" variant="ghost" onClick={() => setProposal(null)}>
                Done
              </Button>
            </div>
          </div>
        )}
      </Panel>
      <Panel title="Work history" actions={<><Button type="button" size="sm" onClick={() => set("work", [...p.work, { ...EMPTY_WORK }])}>
            <Plus size={14} aria-hidden="true" /> Add role
          </Button></>}>
        <Lede>Dates as YYYY-MM, or YYYY when the month isn't known.</Lede>
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
            <ConfirmButton label="Remove role" confirm="Remove" onConfirm={() => set("work", p.work.filter((_, j) => j !== i))} />
          </fieldset>
        ))}
      </Panel>
      <Panel title="Education" actions={<><Button type="button" size="sm" onClick={() => set("education", [...p.education, { ...EMPTY_EDU }])}>
            <Plus size={14} aria-hidden="true" /> Add degree
          </Button></>}>
        <Lede>List every degree; each is answered separately on forms.</Lede>
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
            <ConfirmButton label="Remove degree" confirm="Remove" onConfirm={() => set("education", p.education.filter((_, j) => j !== i))} />
          </fieldset>
        ))}
      </Panel>
      <Panel title="Skills and languages">
        <div className="form-grid">
          <Field label="Skills" hint="Comma separated" wide>
            <ListInput value={p.skills} onChange={(v) => set("skills", v)} />
          </Field>
          <Field label="Languages" hint="Comma separated" wide>
            <ListInput value={p.languages || []} onChange={(v) => set("languages", v)} />
          </Field>
        </div>
      </Panel>
      <Panel title="Stories and evidence" actions={<><Button
            type="button"
            size="sm"
            onClick={() => {
              const used = new Set(p.evidence.map((e) => e.id));
              let n = p.evidence.length + 1;
              while (used.has("note-" + n)) n++;
              set("evidence", [...p.evidence, { id: "note-" + n, text: "" }]);
            }}
          >
            <Plus size={14} aria-hidden="true" /> Add note
          </Button></>}>
        <Lede>Projects, achievements and examples used to write supported narrative answers. Nothing beyond these notes is claimed.</Lede>
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
            <ConfirmButton label="Remove" confirm="Remove note" onConfirm={() => set("evidence", p.evidence.filter((_, j) => j !== i))} />
          </div>
        ))}
      </Panel>
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
    <Panel title="What AXIOM may do for you">
      <Lede>These decide which steps run unattended.</Lede>
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
    </Panel>
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
      <Panel title="Degree, major or school not listed">
        <Lede>What to choose when the form’s list doesn’t include your exact value. Enabled steps run in this order.</Lede>
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
                    <Button size="sm" variant="ghost" aria-label={`Move ${title} earlier`} disabled={i === 0} onClick={() => move(step, -1)}>
                      ↑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Move ${title} later`}
                      disabled={i === rules.education.length - 1}
                      onClick={() => move(step, 1)}
                    >
                      ↓
                    </Button>
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
            <ListInput value={rules.source_order} onChange={(v) => update({ source_order: v })} />
          </Field>
          <Check checked={rules.source_any_channel} onChange={(v) => update({ source_any_channel: v })} title="Then any other plain channel">
            Accepts an offered channel that claims no referral or insider relationship.
          </Check>
        </div>
      </Panel>
      <Panel title="Self-identification questions">
        <Lede>Each question runs: “prefer not to say” when on and offered → your answer → if your answer isn’t listed, the
            fallback you choose. Nothing is guessed from other facts.</Lede>
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
      </Panel>
      <Panel title="Buttons and steps">
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
      </Panel>
    </>
  );
}

function Preferences({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const rule = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  return (
    <Panel title="Targets and answers about the role">
      <div className="form-grid">
        <Field
          label="Target roles"
          hint="Comma separated. Usual alternate titles count too: Software Development Engineer also finds Software Engineer and SDE."
          wide
        >
          <ListInput value={p.target_roles} onChange={(v) => set("target_roles", v)} />
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
          <ListInput value={p.target_locations} onChange={(v) => set("target_locations", v)} />
        </Field>
        <Field label="Exclude titles containing" hint="Comma separated keywords" wide>
          <ListInput value={p.excluded_keywords} onChange={(v) => set("excluded_keywords", v)} />
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
    </Panel>
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
// What the owner sees for each recorded engine state comes from the server (accounts/overview.py); the tone here
// only colours it.
const ACCOUNT_TONE: Record<EmployerAccountStatus, string> = {
  active: "ok",
  verification: "ask",
  password_unknown: "fix",
  reset_pending: "hold",
  locked: "bad",
  created: "run",
  signing_in: "run",
  registered: "idle",
  unknown: "idle",
};
const ACCOUNT_FILTERS: { id: string; label: string; match: (a: AccountRow) => boolean }[] = [
  { id: "all", label: "All accounts", match: () => true },
  { id: "attention", label: "Needs you", match: (a) => a.attention },
  { id: "active", label: "Active", match: (a) => a.status === "active" },
  { id: "password_unknown", label: "Password unknown", match: (a) => a.status === "password_unknown" },
  { id: "verification", label: "Needs email verification", match: (a) => a.status === "verification" },
  { id: "reset_pending", label: "Reset pending", match: (a) => a.status === "reset_pending" },
  { id: "locked", label: "Locked", match: (a) => a.status === "locked" },
  {
    id: "other",
    label: "Created or not signed in yet",
    match: (a) => ["created", "signing_in", "registered", "unknown"].includes(a.status),
  },
];

const ACCOUNT_PILL: Record<string, PillTone> = {
  ok: "ok",
  ask: "warn",
  fix: "warn",
  hold: "info",
  bad: "bad",
  run: "info",
  idle: "neutral",
};

export function AccountSettings() {
  const { snapshot } = useWorkspace();
  const summary = usePoll<AccountSummary>((signal) => api("/accounts", "GET", undefined, signal), 60000);
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [resetting, setResetting] = useState<AccountRow | null>(null);
  const q = query.trim().toLowerCase();
  const all = useMemo(() => (Array.isArray(summary.data?.accounts) ? summary.data!.accounts : []), [summary.data]);
  const counts = useMemo(
    () => Object.fromEntries(ACCOUNT_FILTERS.map((f) => [f.id, all.filter(f.match).length])),
    [all],
  );
  const chosen = ACCOUNT_FILTERS.find((f) => f.id === filter) || ACCOUNT_FILTERS[0];
  const accounts = useMemo(
    () =>
      all.filter(
        (a) =>
          chosen.match(a) &&
          (!q || `${a.host} ${a.employer} ${a.login_email} ${a.label}`.toLowerCase().includes(q)),
      ),
    [all, chosen, q],
  );
  const paging = usePagination(accounts, q + filter, 25);
  const cfg = snapshot.config || ({} as typeof snapshot.config);
  const attention = counts.attention || 0;
  const problems = summary.data?.password_problems || [];
  return (
    <>
      <Panel title="How sign-in works">
        <Lede>
          Employer accounts are created or reused with one login. Passwords stay encrypted on the server and are never
          shown here. Your mailbox handles verification codes and resets.
        </Lede>
        <Facts
          items={[
            ["Login email", cfg.account_email || "Profile email"],
            ["Shared password", cfg.account ? "Set on the server" : "Generated and encrypted"],
            ["Gmail IMAP", cfg.imap ? "Configured" : <span style={{ color: "var(--warn)" }}>Not configured</span>],
            ["Captcha service", cfg.twocaptcha ? "Configured" : "Not configured"],
            [
              "Account creation",
              snapshot.profile?.allow_account_creation ? (
                "Allowed"
              ) : (
                <span style={{ color: "var(--warn)" }}>Off (Profile › Policies)</span>
              ),
            ],
          ]}
        />
        {problems.length > 0 && (
          <div className="ax-notice" style={{ marginTop: "var(--space-3)" }}>
            Some portals may reject the shared password: {problems.join("; ")}.
          </div>
        )}
        <Actions start note={!cfg.imap ? "Add the Gmail address and app password in Setup › Connections." : undefined}>
          <Button
            size="sm"
            loading={pending === "imap"}
            disabled={!cfg.imap || (pending !== null && pending !== "imap")}
            onClick={() => run("imap", () => api("/inbox/check", "POST"), "Gmail IMAP sign-in works.")}
          >
            Test Gmail connection
          </Button>
        </Actions>
      </Panel>
      <Panel title={`Employer accounts${summary.data ? " · " + all.length.toLocaleString() : ""}`}>
        <div className="ax-stack">
          {attention > 0 && (
            <p className="ax-lede" style={{ margin: 0, color: "var(--warn)" }}>
              {attention} {attention === 1 ? "account needs" : "accounts need"} you.
            </p>
          )}
          <div className="ax-toolbar">
            <label className="ax-toolbar-filter">
              <span className="sr-only">Show accounts</span>
              <select className="ax-control" aria-label="Show accounts" value={filter} onChange={(e) => setFilter(e.target.value)}>
                {ACCOUNT_FILTERS.map((f) => (
                  <option key={f.id} value={f.id} disabled={f.id !== "all" && !counts[f.id] && filter !== f.id}>
                    {f.label} ({counts[f.id] || 0})
                  </option>
                ))}
              </select>
            </label>
            <label className="search">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                aria-label="Search employer accounts"
                placeholder="Search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          </div>
          <Table
            label="Employer accounts"
            rows={paging.items}
            rowKey={(a) => a.id}
            loading={!summary.data && !summary.error}
            error={summary.error && !summary.data ? <AxEmpty title="Could not load accounts" hint={summary.error.message} action={<Button size="sm" onClick={summary.refresh}>Try again</Button>} /> : undefined}
            empty={
              <AxEmpty
                title={q || filter !== "all" ? "No accounts match" : "No employer accounts yet"}
                hint={q || filter !== "all" ? undefined : "They appear after the first application that needs one."}
              />
            }
            minWidth={980}
            columns={[
              {
                key: "site",
                header: "Employer site",
                truncate: true,
                width: 230,
                cell: (a) => (a.origin ? <External href={a.origin}>{a.host}</External> : a.host),
              },
              {
                key: "status",
                header: "Status",
                cell: (a) => (
                  <span title={a.detail}>
                    <Pill tone={ACCOUNT_PILL[ACCOUNT_TONE[a.status] || "idle"] || "neutral"} dot>
                      {a.label}
                    </Pill>
                  </span>
                ),
              },
              {
                key: "actions",
                header: <span className="sr-only">Actions</span>,
                cell: (a) => (
                  <span className="ax-row-end" style={{ flexWrap: "nowrap" }}>
                    {a.can_retry_verification && (
                      <Button
                        size="sm"
                        loading={pending === "verify" + a.id}
                        disabled={pending !== null && pending !== "verify" + a.id}
                        onClick={() =>
                          run(
                            "verify" + a.id,
                            async () => {
                              await api(`/accounts/${encodeURIComponent(a.id)}/verify`, "POST");
                              await summary.refresh();
                            },
                            "Verification restarted. The paused application asks the employer for a new email.",
                          )
                        }
                      >
                        Retry verification
                      </Button>
                    )}
                    <Button size="sm" onClick={() => setResetting(a)}>
                      I reset it myself
                    </Button>
                    <ConfirmButton
                      label="Forget"
                      variant="secondary"
                      confirm="Forget account"
                      note="The next application signs in again or creates the account."
                      pending={pending === "forget" + a.id}
                      onConfirm={() =>
                        run(
                          "forget" + a.id,
                          async () => {
                            await api("/accounts/" + encodeURIComponent(a.id), "DELETE");
                            await summary.refresh();
                          },
                          "Account record forgotten.",
                        )
                      }
                    />
                  </span>
                ),
              },
              {
                key: "login",
                header: "Login",
                truncate: true,
                width: 240,
                cell: (a) => (
                  <span title={[a.employer && "for " + a.employer, a.login_email].filter(Boolean).join(" · ")}>
                    {[a.employer && "for " + a.employer, a.login_email].filter(Boolean).join(" · ") || "—"}
                  </span>
                ),
              },
              {
                key: "updated",
                header: "Updated",
                cell: (a) => <span title={stamp(a.updated_at)}>{ago(a.updated_at)}</span>,
              },
            ]}
          />
          <Pagination paging={paging} label="Account pages" />
        </div>
      </Panel>
      <SessionImport />
      <Drawer
        open={!!resetting}
        onClose={() => setResetting(null)}
        title={resetting ? `Reset password · ${resetting.host}` : "Reset password"}
        width={480}
      >
        {resetting && (
          <EmployerPassword
            account={resetting}
            close={() => setResetting(null)}
            saved={async () => {
              setResetting(null);
              await summary.refresh();
            }}
          />
        )}
      </Drawer>
    </>
  );
}

/** "I reset it myself": the password the owner set on the employer's site, sealed on the server. */
function EmployerPassword({
  account: a,
  close,
  saved,
}: {
  account: AccountRow;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const notify = useNotify();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const id = useId();
  return (
    <form
      className="ax-stack"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        if (!password.trim()) return setError(`Enter the new password you set on ${a.host}.`);
        if (confirm !== password) return setError("The two passwords don't match.");
        setBusy(true);
        setError("");
        try {
          await api(`/accounts/${encodeURIComponent(a.id)}/password`, "PUT", { password });
          notify("ok", `Password saved. The next application to ${a.host} signs in with it.`);
          await saved();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="ax-lede" style={{ margin: 0 }}>
        Reset the password on {a.origin ? <External href={a.origin}>{a.host}</External> : a.host} yourself
        {a.login_email ? (
          <>
            {" "}
            (sign-in email <strong>{a.login_email}</strong>)
          </>
        ) : null}
        , then enter it here. It's encrypted on the server and never shown again; AXIOM stops asking this employer
        for resets.
      </p>
      <div className="ax-field">
        <label className="ax-label" htmlFor={id + "-p"}>
          New password
        </label>
        <SecretInput id={id + "-p"} label="password" value={password} onChange={setPassword} />
      </div>
      <div className="ax-field">
        <label className="ax-label" htmlFor={id + "-c"}>
          Confirm password
        </label>
        <SecretInput id={id + "-c"} label="confirmation" value={confirm} onChange={setConfirm} />
        {error && (
          <p className="ax-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <Actions>
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={busy}>
          Save password
        </Button>
      </Actions>
    </form>
  );
}

function SessionImport() {
  const { pending, run } = useAction();
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState(0);
  return (
    <details className="ax-panel ax-fold">
      <summary>
        <strong>Import an employer session</strong>
        <span className="ax-hint">· only for SMS or passkey sign-ins</span>
      </summary>
      <div className="ax-panel-body">
        <Lede>
          Run <code>jobpilot login https://employer.example</code> on your own machine, or import a Playwright
          storage-state JSON. Only cookies for that exact employer host are accepted; the file stays in the private data
          directory.
        </Lede>
        <form
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
          <FormGrid>
            <Input label="Employer URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <Input
              key={key}
              label="Storage-state JSON"
              type="file"
              accept="application/json"
              required
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </FormGrid>
          <Actions>
            <Button type="submit" variant="primary" loading={pending === "session"} disabled={!file}>
              Import session
            </Button>
          </Actions>
        </form>
      </div>
    </details>
  );
}
