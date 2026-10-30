// The profile: one draft and one Save for every field, whichever Profile tab or Knowledge section it sits on.
// ProfileProvider (mounted once by the Profile page) owns the draft; ProfileSettings shows the sections a tab asks for.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft } from "./hooks";
import { plural } from "./format";
import { ProfilePhoto } from "./profile-photo";
import { Actions, FormGrid, ListInput, SaveBar } from "./setup-kit";
import type {
  AnswerRules,
  DemographicRule,
  EducationEntry,
  EducationStep,
  Profile,
  WorkEntry,
} from "./types";
import { Button, Check, ConfirmButton, Field, Input, Loading, Panel, Select, Table, Textarea } from "./ui/index";

type ProfileDraft = { value: Profile; base: string; dirty: boolean };
// Learned answers are server-owned; they never travel back in a profile save.
const comparable = (p: Profile) => {
  const { reviewed_answers: _r, approved_answers: _a, ...rest } = p as Profile & Record<string, unknown>;
  return JSON.stringify(rest);
};
const EMPTY_WORK: WorkEntry = { company: "", title: "", location: "", start: "", end: "", current: false, description: "" };
const EMPTY_EDU: EducationEntry = { school: "", degree: "", field_of_study: "", start: "", end: "", gpa: "" };

export type ProfileSection = "identity" | "experience" | "facts" | "policies" | "rules";
type Setter = <K extends keyof Profile>(key: K, value: Profile[K]) => void;

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

// ---------- the shared draft ----------
type ProfileState = {
  /** null until the first read of the profile has landed. */
  p: Profile | null;
  set: Setter;
  factsText: string;
  setFactsText: (text: string) => void;
  factsError: string;
  parsedFacts: Record<string, unknown>;
  dirty: boolean;
  saving: boolean;
  save: () => void;
  discard: () => void;
  /** Re-read the profile after something changed it on the server (organized facts). */
  reload: () => Promise<void>;
};
const ProfileContext = createContext<ProfileState | null>(null);
const useProfile = () => {
  const value = useContext(ProfileContext);
  if (!value) throw new Error("ProfileProvider missing");
  return value;
};

/**
 * Holds the one draft (kept in memory across tab switches and remounts, see useDraft) and the one Save. Edits made on
 * Identity, Experience and Knowledge all go out together in a single PUT /api/profile.
 */
export function ProfileProvider({ children }: { children: ReactNode }) {
  const { snapshot, refresh } = useWorkspace();
  const server = useMemo(() => normalizeProfile(snapshot.profile), [snapshot.profile]);
  const [draft, setDraft] = useDraft<ProfileDraft | null>("profile", null);
  const [factsText, setFactsText] = useDraft<string | null>("profile-facts", null);
  const { pending, run } = useAction();

  // Follow the server until the first edit; afterwards keep the draft and flag concurrent changes instead of silently
  // replacing either side. Saving or discarding clears the draft, which brings it back here to be rebased.
  useEffect(() => {
    if (!draft || (!draft.dirty && draft.base !== comparable(server))) {
      setDraft({ value: server, base: comparable(server), dirty: false });
      setFactsText(JSON.stringify(server.facts || {}, null, 2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server, draft === null, draft?.dirty]);

  const p = draft?.value ?? null;
  const text = factsText ?? JSON.stringify(p?.facts || {}, null, 2);
  let factsError = "";
  let parsedFacts: Record<string, unknown> = {};
  try {
    const v = JSON.parse(text || "{}");
    if (!v || Array.isArray(v) || typeof v !== "object") factsError = "Facts must be a JSON object { … }.";
    else parsedFacts = v;
  } catch (e) {
    factsError = "Not valid JSON: " + (e as Error).message;
  }

  // Functional updates: several fields can change in one event without losing any.
  const set: Setter = (key, value) =>
    setDraft((d) => d && { ...d, value: { ...d.value, [key]: value }, dirty: true });
  const discard = () => {
    setDraft(null);
    setFactsText(null);
  };
  const save = () => {
    if (!draft || factsError) return;
    const v = draft.value;
    run(
      "save",
      async () => {
        await api("/profile", "PUT", {
          ...v,
          facts: parsedFacts,
          reviewed_answers: [],
          work: v.work.filter((w) => Object.values(w).some((x) => x !== "" && x !== false)),
          education: v.education.filter((e) => Object.values(e).some(Boolean)),
          evidence: v.evidence.filter((e) => e.text.trim()),
        });
        await refresh();
        discard();
      },
      "Profile saved. New applications use it immediately.",
    );
  };
  const changedElsewhere = !!draft?.dirty && draft.base !== comparable(server);

  const state: ProfileState = {
    p,
    set,
    factsText: text,
    setFactsText: (t) => {
      setFactsText(t);
      setDraft((d) => d && { ...d, dirty: true });
    },
    factsError,
    parsedFacts,
    dirty: !!draft?.dirty,
    saving: pending === "save",
    save,
    discard,
    reload: async () => {
      await refresh();
      discard();
    },
  };
  return (
    <ProfileContext.Provider value={state}>
      {changedElsewhere && (
        <div className="ax-notice is-warn" role="status">
          <span>
            Your profile changed elsewhere (for example from Telegram) after you started editing. Saving replaces the
            fields you changed.
          </span>
          <Button size="sm" onClick={discard}>
            Discard my edits and reload
          </Button>
        </div>
      )}
      {children}
      <SaveBar
        dirty={state.dirty}
        saving={state.saving}
        onSave={save}
        onDiscard={discard}
        disabled={!!factsError}
        label="Save profile"
        note={factsError ? "Fix the facts JSON to save." : "Unsaved changes are kept while you move between tabs."}
      />
    </ProfileContext.Provider>
  );
}

const SECTION_VIEW: Record<ProfileSection, (props: { p: Profile; set: Setter }) => ReactNode> = {
  identity: (a) => <Identity {...a} />,
  experience: (a) => <Experience {...a} />,
  facts: () => <FactsEditor />,
  policies: (a) => <Policies {...a} />,
  rules: (a) => <AnswerRulesEditor {...a} />,
};

/** The sections a tab shows, one after another. All of them read and write the shared draft. */
export function ProfileSettings({ sections }: { sections: ProfileSection[] }) {
  const { p, set, factsError, save } = useProfile();
  if (!p) return <Loading />;
  return (
    <form
      className="ax-sections"
      onSubmit={(e) => {
        e.preventDefault();
        if (!factsError) save();
      }}
    >
      {sections.map((id) => (
        <div key={id} id={"profile-" + id}>
          {SECTION_VIEW[id]({ p, set })}
        </div>
      ))}
    </form>
  );
}

// ---------- Identity ----------
function Text({
  p,
  set,
  k,
  label,
  type = "text",
  placeholder,
  autoComplete,
}: {
  p: Profile;
  set: Setter;
  k: keyof Profile;
  label: string;
  type?: string;
  placeholder?: string;
  autoComplete?: string;
}) {
  return (
    <Input
      label={label}
      type={type}
      placeholder={placeholder}
      autoComplete={autoComplete || "off"}
      value={String(p[k] ?? "")}
      onChange={(e) => set(k, e.target.value as never)}
    />
  );
}

function Identity({ p, set }: { p: Profile; set: Setter }) {
  const addr = p.address;
  const setAddr = (k: keyof Profile["address"], v: string) => set("address", { ...addr, [k]: v });
  return (
    <div className="ax-sections">
      <ProfilePhoto name={p.name || [p.first_name, p.last_name].filter(Boolean).join(" ")} />
      <Panel title="Name and contact">
        <FormGrid>
          <Text p={p} set={set} k="name" label="Full legal name" />
          <Text p={p} set={set} k="preferred_name" label="Preferred name" />
          <Text p={p} set={set} k="first_name" label="Legal first name" />
          <Text p={p} set={set} k="middle_name" label="Middle name" />
          <Text p={p} set={set} k="last_name" label="Legal last name" />
          <Text p={p} set={set} k="email" label="Email" type="email" />
          <Text p={p} set={set} k="phone" label="Phone" type="tel" />
          <Text p={p} set={set} k="phone_country_code" label="Phone country code" placeholder="+1" />
          <Text p={p} set={set} k="phone_type" label="Phone type" />
          <Text p={p} set={set} k="location" label="Current location" placeholder="City, State" />
          <Text p={p} set={set} k="linkedin" label="LinkedIn URL" type="url" />
          <Text p={p} set={set} k="github" label="GitHub URL" type="url" />
          <Text p={p} set={set} k="website" label="Website or portfolio" type="url" />
        </FormGrid>
      </Panel>
      <Panel title="Address">
        <FormGrid>
          {(
            [
              ["line1", "Address line 1"],
              ["line2", "Address line 2"],
              ["city", "City"],
              ["state", "State or province"],
              ["postal_code", "Postal code"],
              ["county", "County"],
              ["country", "Country"],
            ] as [keyof Profile["address"], string][]
          ).map(([k, label]) => (
            <Input key={k} label={label} value={addr[k] || ""} onChange={(e) => setAddr(k, e.target.value)} />
          ))}
        </FormGrid>
      </Panel>
    </div>
  );
}

// ---------- Experience ----------
/** One role or degree: a fold titled with what it is, open when it is new and still empty. */
function Entry({ title, meta, blank, children }: { title: string; meta?: string; blank: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(blank);
  return (
    <details className="ax-fold ax-entry" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <strong>{title}</strong>
        {meta && <span className="ax-hint">{meta}</span>}
      </summary>
      {children}
    </details>
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
    p.work.some(
      (x) => x.company.trim().toLowerCase() === w.company.trim().toLowerCase() && x.title.trim().toLowerCase() === w.title.trim().toLowerCase(),
    );
  const hasEdu = (e: EducationEntry) =>
    p.education.some(
      (x) => x.school.trim().toLowerCase() === e.school.trim().toLowerCase() && x.degree.trim().toLowerCase() === e.degree.trim().toLowerCase(),
    );
  const updateWork = (i: number, patch: Partial<WorkEntry>) =>
    set("work", p.work.map((w, j) => (j === i ? { ...w, ...patch } : w)));
  const updateEdu = (i: number, patch: Partial<EducationEntry>) =>
    set("education", p.education.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const newWork = proposal?.work.filter((w) => !hasWork(w)) || [];
  const newEdu = proposal?.education.filter((e) => !hasEdu(e)) || [];
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
  return (
    <div className="ax-sections">
      <Panel
        title="Work history"
        actions={
          <>
            <Button size="sm" loading={pending === "extract"} disabled={pending !== null} onClick={extract}>
              Read from resume
            </Button>
            <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => set("work", [...p.work, { ...EMPTY_WORK }])}>
              Add role
            </Button>
          </>
        }
      >
        {proposal && (
          <div className="ax-stack">
            <p className="ax-hint">
              Found {plural(proposal.work.length, "role")} and {plural(proposal.education.length, "degree")};{" "}
              {newWork.length + newEdu.length ? `${newWork.length + newEdu.length} not yet in your profile.` : "all are already in your profile."}
            </p>
            <ul className="ax-rows">
              {[
                ...newWork.map((w) => ({ kind: "work" as const, w })),
                ...newEdu.map((e) => ({ kind: "edu" as const, e })),
              ].map((item, i) => (
                <li className="ax-row" key={i}>
                  <span className="ax-row-main">
                    {item.kind === "work" ? (
                      <>
                        <strong>{item.w.title}</strong>
                        <small>
                          {item.w.company} · {item.w.start}–{item.w.current ? "present" : item.w.end}
                        </small>
                      </>
                    ) : (
                      <>
                        <strong>
                          {item.e.degree}
                          {item.e.field_of_study && ` in ${item.e.field_of_study}`}
                        </strong>
                        <small>
                          {item.e.school} · {item.e.end}
                        </small>
                      </>
                    )}
                  </span>
                  <Button
                    size="sm"
                    onClick={() =>
                      item.kind === "work" ? set("work", [...p.work, item.w]) : set("education", [...p.education, item.e])
                    }
                  >
                    Add
                  </Button>
                </li>
              ))}
            </ul>
            <Actions start>
              {newWork.length + newEdu.length > 1 && (
                <Button
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
            </Actions>
          </div>
        )}
        {p.work.length === 0 && !proposal && <p className="ax-hint">No roles yet.</p>}
        {p.work.map((w, i) => (
          <Entry
            key={i}
            blank={!w.company && !w.title}
            title={w.title || w.company ? `${w.title || "Role"} · ${w.company || "Company"}` : `Role ${i + 1}`}
            meta={[w.start, w.current ? "present" : w.end].filter(Boolean).join(" – ")}
          >
            <FormGrid>
              <Input label="Company" value={w.company} onChange={(e) => updateWork(i, { company: e.target.value })} />
              <Input label="Title" value={w.title} onChange={(e) => updateWork(i, { title: e.target.value })} />
              <Input label="Location" value={w.location} onChange={(e) => updateWork(i, { location: e.target.value })} />
              <Input label="Start" placeholder="YYYY-MM" value={w.start} onChange={(e) => updateWork(i, { start: e.target.value })} />
              <Input
                label="End"
                placeholder={w.current ? "Present" : "YYYY-MM"}
                disabled={w.current}
                value={w.end}
                onChange={(e) => updateWork(i, { end: e.target.value })}
              />
              <Check
                label="Current role"
                checked={w.current}
                onChange={(on) => updateWork(i, { current: on, end: on ? "" : w.end })}
              />
              <Textarea
                label="What you did"
                rows={3}
                value={w.description}
                onChange={(e) => updateWork(i, { description: e.target.value })}
              />
            </FormGrid>
            <Actions start>
              <ConfirmButton label="Remove role" confirm="Remove" onConfirm={() => set("work", p.work.filter((_, j) => j !== i))} />
            </Actions>
          </Entry>
        ))}
      </Panel>

      <Panel
        title="Education"
        actions={
          <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => set("education", [...p.education, { ...EMPTY_EDU }])}>
            Add degree
          </Button>
        }
      >
        {p.education.length === 0 && <p className="ax-hint">No degrees yet.</p>}
        {p.education.map((x, i) => (
          <Entry
            key={i}
            blank={!x.school && !x.degree}
            title={x.degree || x.school ? `${x.degree || "Degree"} · ${x.school || "School"}` : `Degree ${i + 1}`}
            meta={x.end}
          >
            <FormGrid>
              <Input label="School" value={x.school} onChange={(e) => updateEdu(i, { school: e.target.value })} />
              <Input label="Degree" value={x.degree} onChange={(e) => updateEdu(i, { degree: e.target.value })} />
              <Input label="Field of study" value={x.field_of_study} onChange={(e) => updateEdu(i, { field_of_study: e.target.value })} />
              <Input label="Start" placeholder="YYYY-MM" value={x.start} onChange={(e) => updateEdu(i, { start: e.target.value })} />
              <Input
                label="End or expected"
                placeholder="YYYY-MM"
                hint="A future date means the degree is in progress."
                value={x.end}
                onChange={(e) => updateEdu(i, { end: e.target.value })}
              />
              <Input label="GPA" value={x.gpa} onChange={(e) => updateEdu(i, { gpa: e.target.value })} />
            </FormGrid>
            <Actions start>
              <ConfirmButton label="Remove degree" confirm="Remove" onConfirm={() => set("education", p.education.filter((_, j) => j !== i))} />
            </Actions>
          </Entry>
        ))}
      </Panel>

      <Panel title="Skills and languages">
        <FormGrid>
          <Field label="Skills" hint="Comma separated">
            <ListInput value={p.skills} onChange={(v) => set("skills", v)} />
          </Field>
          <Field label="Languages" hint="Comma separated">
            <ListInput value={p.languages || []} onChange={(v) => set("languages", v)} />
          </Field>
        </FormGrid>
      </Panel>

      <Panel
        title="Stories and evidence"
        actions={
          <Button
            size="sm"
            icon={<Plus size={14} aria-hidden="true" />}
            onClick={() => {
              const used = new Set(p.evidence.map((e) => e.id));
              let n = p.evidence.length + 1;
              while (used.has("note-" + n)) n++;
              set("evidence", [...p.evidence, { id: "note-" + n, text: "" }]);
            }}
          >
            Add note
          </Button>
        }
      >
        {p.evidence.length === 0 && <p className="ax-hint">No notes yet. Projects and achievements here are used to write supported answers.</p>}
        <FormGrid>
          {p.evidence.map((note, i) => (
            <div key={note.id}>
              <Textarea
                label={`Note ${i + 1}`}
                rows={3}
                maxLength={8000}
                value={note.text}
                onChange={(e) => set("evidence", p.evidence.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
              />
              <Actions start>
                <ConfirmButton label="Remove" confirm="Remove note" onConfirm={() => set("evidence", p.evidence.filter((_, j) => j !== i))} />
              </Actions>
            </div>
          ))}
        </FormGrid>
      </Panel>

      <div id="profile-preferences">
        <Preferences p={p} set={set} />
      </div>
    </div>
  );
}

// ---------- Preferences ----------
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

function Preferences({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const rule = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  return (
    <Panel title="Preferences">
      <FormGrid>
        <Field label="Target roles" hint="Comma separated. Usual alternate titles count too.">
          <ListInput value={p.target_roles} onChange={(v) => set("target_roles", v)} />
        </Field>
        <Select
          label="Apply first to"
          value={p.job_priority ?? "new_grad_first"}
          onChange={(e) => set("job_priority", e.target.value as Profile["job_priority"])}
        >
          <option value="new_grad_first">New grad roles, then best fit</option>
          <option value="best_fit_first">Best fit, any level</option>
        </Select>
        <Field label="Target locations" hint="Comma separated">
          <ListInput value={p.target_locations} onChange={(v) => set("target_locations", v)} />
        </Field>
        <Field label="Exclude titles containing" hint="Comma separated keywords">
          <ListInput value={p.excluded_keywords} onChange={(v) => set("excluded_keywords", v)} />
        </Field>
        <Input
          label="Minimum fit for ranking"
          hint="0–100"
          type="number"
          min={0}
          max={100}
          value={p.minimum_fit}
          onChange={(e) => set("minimum_fit", Number(e.target.value))}
        />
        <Input
          label="Salary expectation"
          hint="Leave blank to skip optional salary questions."
          value={p.salary_expectation}
          onChange={(e) => set("salary_expectation", e.target.value)}
        />
        <Input
          label="Stance on desired pay"
          hint="Said when pay is asked and no salary is saved, for example Negotiable."
          maxLength={60}
          value={rules.salary_stance}
          onChange={(e) => rule({ salary_stance: e.target.value })}
        />
        <Input
          label="Willingness to travel"
          placeholder="Open to travel"
          maxLength={100}
          value={rules.travel_willingness}
          onChange={(e) => rule({ travel_willingness: e.target.value })}
        />
        <Input
          label="Employment sought"
          placeholder="Full-time early-career employment"
          maxLength={100}
          value={rules.employment_type_sought}
          onChange={(e) => rule({ employment_type_sought: e.target.value })}
        />
        <Check
          label="Include internships and co-ops"
          note="Off applies to full-time roles only."
          checked={!!p.include_internships}
          onChange={(v) => set("include_internships", v)}
        />
        <Input label="Availability or notice" value={p.availability} onChange={(e) => set("availability", e.target.value)} />
        <Input
          label="How you heard about the job"
          value={p.application_source}
          onChange={(e) => set("application_source", e.target.value)}
        />
      </FormGrid>
    </Panel>
  );
}

// ---------- Policies ----------
function Policies({ p, set }: { p: Profile; set: Setter }) {
  return (
    <Panel title="What AXIOM may do for you">
      <FormGrid>
        <Check
          checked={p.autonomous}
          onChange={(v) => set("autonomous", v)}
          label="Apply autonomously"
          note="Applications missing a required personal fact wait for your answer while other jobs continue."
        />
        <Check
          checked={p.allow_account_creation}
          onChange={(v) => set("allow_account_creation", v)}
          label="Create and reuse employer accounts"
          note="Needed for Workday, iCIMS and other portals that require an account."
        />
        <Check
          checked={p.allow_application_consents}
          onChange={(v) => set("allow_application_consents", v)}
          label="Accept privacy notices and certify accuracy"
          note="Certifies that saved information is accurate where a form requires it."
        />
        <Check
          checked={p.accept_all_application_terms}
          onChange={(v) => set("accept_all_application_terms", v)}
          label="Accept application terms and conditions"
        />
        <Check
          checked={!!p.accept_required_marketing}
          onChange={(v) => set("accept_required_marketing", v)}
          label="Accept required marketing consent"
          note="Only when a form refuses to continue without it."
        />
        <Check
          checked={p.decline_demographics}
          onChange={(v) => set("decline_demographics", v)}
          label="Decline voluntary demographic questions"
          note="Chooses “prefer not to say” unless you confirmed a specific answer."
        />
      </FormGrid>
    </Panel>
  );
}

// ---------- Facts ----------
function FactsEditor() {
  const { p, set, factsText, setFactsText, factsError, parsedFacts, dirty, reload } = useProfile();
  const { pending, run } = useAction();
  if (!p) return null;
  return (
    <Panel title="Confirmed facts">
      <FormGrid>
        <Textarea
          label="Facts (JSON)"
          mono
          spellCheck={false}
          rows={16}
          value={factsText}
          hint={factsError ? undefined : `${Object.keys(parsedFacts).length} facts. Missing facts are asked once, never guessed.`}
          error={factsError || undefined}
          onChange={(e) => setFactsText(e.target.value)}
        />
        <Textarea
          label="Approved consent statements"
          hint="One exact checkbox label per line. List only commitments you accept."
          rows={4}
          value={p.approved_consents.join("\n")}
          onChange={(e) => set("approved_consents", e.target.value.split("\n").filter((x) => x.trim()))}
        />
      </FormGrid>
      <Actions start note={dirty ? "Save or discard your edits first." : "Only restructures facts you already saved; nothing is invented."}>
        <Button
          size="sm"
          loading={pending === "organize"}
          disabled={pending !== null || dirty}
          onClick={() =>
            run(
              "organize",
              async () => {
                const r = await api<{ saved?: number }>("/profile/organize", "POST");
                await reload();
                return r;
              },
              (r) => (r?.saved != null ? `Organized and saved ${plural(r.saved, "fact")}.` : "Saved facts organized."),
            )
          }
        >
          Organize saved facts with AI
        </Button>
      </Actions>
    </Panel>
  );
}

// ---------- When options don't match ----------
const EDUCATION_STEPS: [EducationStep, string, string][] = [
  ["closest", "Closest match", "Your fallback major (below) and the same-level degree first, then the most similar option."],
  ["pick3", "Assistant chooses from the 3 closest", "Only options close to your value are offered; unrelated ones never are."],
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

function AnswerRulesEditor({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const update = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  const topic = (key: string, next: Partial<DemographicRule>) =>
    update({ demographics: { ...rules.demographics, [key]: { ...rules.demographics[key], ...next } } });
  // Enabled steps keep their saved order; disabled ones follow in their usual place.
  const ordered = [...rules.education, ...EDUCATION_STEPS.map(([s]) => s).filter((s) => !rules.education.includes(s))];
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
      education: on ? ordered.filter((s) => s === step || rules.education.includes(s)) : rules.education.filter((s) => s !== step),
    });
  return (
    <div className="ax-sections">
      <Panel title="Degree, major or school not listed">
        <ol className="ax-rows">
          {ordered.map((step) => {
            const [, title, note] = EDUCATION_STEPS.find(([s]) => s === step)!;
            const on = rules.education.includes(step);
            const i = rules.education.indexOf(step);
            return (
              <li key={step} className="ax-row">
                <Check checked={on} onChange={(v) => toggle(step, v)} label={title} note={note} />
                {on && (
                  <span className="ax-row-end">
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
        <FormGrid>
          <Input
            label="Fallback major"
            hint="Chosen when a form’s list lacks your major, for example Computer Science. Empty chooses none."
            maxLength={100}
            value={rules.major_fallback}
            onChange={(e) => update({ major_fallback: e.target.value })}
          />
          <Field label="“How did you hear about us” order" hint="Used when your own source isn’t offered. Comma separated, best first. Empty asks you.">
            <ListInput value={rules.source_order} onChange={(v) => update({ source_order: v })} />
          </Field>
          <Check
            checked={rules.source_any_channel}
            onChange={(v) => update({ source_any_channel: v })}
            label="Then any other plain channel"
            note="Accepts an offered channel that claims no referral or insider relationship."
          />
        </FormGrid>
      </Panel>

      <Panel title="Self-identification questions" flush>
        <Table
          label="Self-identification answers"
          rows={IDENTITY_TOPICS}
          rowKey={([key]) => key}
          minWidth={680}
          columns={[
            { key: "q", header: "Question", width: 180, cell: ([, label]) => label },
            {
              key: "decline",
              header: "Prefer not to say",
              cell: ([key, label]) => (
                <input
                  type="checkbox"
                  aria-label={`Prefer not to say for ${label}`}
                  checked={rules.demographics[key].decline_first}
                  onChange={(e) => topic(key, { decline_first: e.target.checked })}
                />
              ),
            },
            {
              key: "answer",
              header: "Your answer",
              width: 240,
              cell: ([key, label]) => (
                <input
                  aria-label={`Your answer for ${label}`}
                  placeholder="From Knowledge"
                  value={rules.demographics[key].answer}
                  onChange={(e) => topic(key, { answer: e.target.value })}
                />
              ),
            },
            {
              key: "missing",
              header: "If your answer isn’t listed",
              width: 200,
              cell: ([key, label]) => (
                <select
                  aria-label={`If your ${label.toLowerCase()} answer isn’t listed`}
                  value={rules.demographics[key].when_missing}
                  onChange={(e) => topic(key, { when_missing: e.target.value as DemographicRule["when_missing"] })}
                >
                  <option value="closest">Closest option</option>
                  <option value="other">“Other”</option>
                  <option value="ask">Ask me</option>
                </select>
              ),
            },
          ]}
        />
      </Panel>

      <Panel title="Buttons and steps">
        <FormGrid>
          <Check
            checked={rules.guest_apply_first}
            onChange={(v) => update({ guest_apply_first: v })}
            label="Apply as a guest when the site allows it"
            note="Skips sign-in and account creation when an employer offers “Apply without an account”."
          />
          <Check
            checked={rules.join_talent_communities}
            onChange={(v) => update({ join_talent_communities: v })}
            label="Join talent communities"
            note="Opts in when a form offers to keep your data on file for other roles. Off declines."
          />
          <Check
            checked={rules.open_agreement_links}
            onChange={(v) => update({ open_agreement_links: v })}
            label="Open linked documents before accepting an agreement"
            note="Some sites accept an agreement only after its privacy and terms links were opened."
          />
        </FormGrid>
      </Panel>
    </div>
  );
}
