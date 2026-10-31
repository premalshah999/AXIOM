// The Profile page: a list of sections at the left and one section at a time at the right, over ONE draft and ONE Save.
// ProfileProvider (mounted once by the page) owns the draft, so an edit made in Personal details and another made in Job
// preferences go out together in a single PUT /api/profile. The sections themselves are in profile-work.tsx, profile-rules.tsx and
// knowledge.tsx; the questionnaires (supervisors, references, FAQ) save on their own, as you go.
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { api } from "./api";
import { routeUrl, useWorkspace, type Route } from "./context";
import { plural } from "./format";
import { useAction, useDraft } from "./hooks";
import { ContactsSection, MemorySection, QuestionnairesSection, useWaitingOnContacts, type Waiting } from "./knowledge";
import { Group, Pick, Range, SectionHead, Tags } from "./profile-fields";
import { IdentityHeader } from "./profile-photo";
import { AnswerRulesSection, PermissionsSection, permissionCount, rulesWithDefaults } from "./profile-rules";
import { EMPTY_EDU, EMPTY_WORK, WorkSection } from "./profile-work";
import { useQuestionnaires } from "./qform";
import { TopLine } from "./shell/Shell";
import type { Address, AnswerRules, EducationEntry, Profile, WorkEntry } from "./types";
import { Button, Check, Input, Panel, Segmented, Skeleton } from "./ui/index";
import "./styles/profile.css";

export type Setter = <K extends keyof Profile>(key: K, value: Profile[K]) => void;
type SectionId = "about" | "work" | "preferences" | "contacts" | "questionnaires" | "permissions" | "answers" | "memory";

// ---------- the profile, made safe to read ----------
// A profile that arrives thin (a new account, an older server) is filled in so no section reads a missing list.
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
function normalizeProfile(raw: Partial<Profile> | null | undefined): Profile {
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

// ---------- what changed, and where ----------
// JSON with sorted keys, so two profiles that say the same thing compare equal however their keys were written.
function canon(v: unknown): string {
  return JSON.stringify(v, (_k, x) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : 1)))
      : x,
  );
}
// Learned answers are server-owned; they never travel back in a profile save.
const comparable = (p: Profile) => {
  const { reviewed_answers: _r, approved_answers: _a, ...rest } = p as Profile & Record<string, unknown>;
  return canon(rest);
};

const OWN_KEYS: Partial<Record<SectionId, (keyof Profile)[]>> = {
  about: ["name", "preferred_name", "first_name", "middle_name", "last_name", "email", "phone", "phone_country_code", "phone_type", "location", "linkedin", "github", "website", "address"],
  work: ["work", "education", "skills", "languages", "evidence"],
  preferences: ["target_roles", "job_priority", "target_locations", "excluded_keywords", "minimum_fit", "salary_expectation", "availability", "application_source", "include_internships"],
  permissions: ["autonomous", "allow_account_creation", "allow_application_consents", "accept_all_application_terms", "accept_required_marketing", "decline_demographics"],
  memory: ["approved_consents"],
};
// answer_rules is one object on the server but three sections on the page.
const RULE_KEYS: Partial<Record<SectionId, (keyof AnswerRules)[]>> = {
  preferences: ["salary_stance", "travel_willingness", "employment_type_sought"],
  permissions: ["guest_apply_first", "join_talent_communities", "open_agreement_links"],
  answers: ["education", "major_fallback", "source_order", "source_any_channel", "demographics"],
};
function changedSections(a: Profile, b: Profile, factsChanged: boolean): SectionId[] {
  const ra = rulesWithDefaults(a);
  const rb = rulesWithDefaults({ ...b, decline_demographics: a.decline_demographics });
  const ids = Object.keys({ ...OWN_KEYS, ...RULE_KEYS }) as SectionId[];
  const out = ids.filter(
    (id) =>
      OWN_KEYS[id]?.some((k) => canon(a[k]) !== canon(b[k])) ||
      RULE_KEYS[id]?.some((k) => canon(ra[k]) !== canon(rb[k])) ||
      (id === "memory" && factsChanged),
  );
  return out.length ? out : factsChanged ? ["memory"] : [];
}

// ---------- the shared draft ----------
type ProfileDraft = { value: Profile; origin: Profile; base: string; baseFacts: string };
export type ProfileState = {
  p: Profile | null;
  set: Setter;
  factsText: string;
  setFactsText: (text: string) => void;
  factsError: string;
  parsedFacts: Record<string, unknown>;
  /** Something really differs from what is saved (an edit undone is not a change). */
  dirty: boolean;
  /** The sections that hold a change. */
  changed: SectionId[];
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

const factsOf = (p: Profile) => JSON.stringify(p.facts || {}, null, 2);

function ProfileProvider({ children }: { children: ReactNode }) {
  const { snapshot, refresh } = useWorkspace();
  const server = useMemo(() => normalizeProfile(snapshot.profile), [snapshot.profile]);
  const [draft, setDraft] = useDraft<ProfileDraft | null>("profile", null);
  const [factsText, setFactsText] = useDraft<string | null>("profile-facts", null);
  const { pending, run } = useAction();

  const text = factsText ?? (draft ? draft.baseFacts : "{}");
  const factsChanged = !!draft && text !== draft.baseFacts;
  const net = !!draft && (comparable(draft.value) !== draft.base || factsChanged);

  // Follow the server until the first edit; afterwards keep the draft and flag concurrent changes instead of silently
  // replacing either side. Saving or discarding clears the draft, which brings it back here to be rebased.
  useEffect(() => {
    if (!draft || (!net && draft.base !== comparable(server))) {
      setDraft({ value: server, origin: server, base: comparable(server), baseFacts: factsOf(server) });
      setFactsText(factsOf(server));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server, draft === null, net]);

  const p = draft?.value ?? server;
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
  const set: Setter = (key, value) => setDraft((d) => d && { ...d, value: { ...d.value, [key]: value } });
  const discard = () => {
    setDraft(null);
    setFactsText(null);
  };
  const save = () => {
    if (!draft || !net || factsError) return;
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
  const changedElsewhere = net && draft!.base !== comparable(server);
  const changed = useMemo(
    () => (draft && net ? changedSections(draft.origin, draft.value, factsChanged) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [draft?.value, net, factsChanged],
  );

  const state: ProfileState = {
    p,
    set,
    factsText: text,
    setFactsText,
    factsError,
    parsedFacts,
    dirty: net,
    changed,
    saving: pending === "save",
    save,
    discard,
    reload: async () => {
      await refresh();
      discard();
    },
  };

  // Ctrl or ⌘ S saves from anywhere on the page.
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    if (!net) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [net]);

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
      <SaveBar state={state} />
    </ProfileContext.Provider>
  );
}

/** Appears only when something differs from what is saved, and says where. Stays at the foot of the page while you scroll. */
function SaveBar({ state }: { state: ProfileState }) {
  const { dirty, saving, save, discard, factsError, changed } = state;
  if (!dirty && !saving) return null;
  const where = changed.map((id) => SECTIONS.find((s) => s.id === id)?.label).filter(Boolean) as string[];
  const note = factsError
    ? "Fix the facts JSON to save."
    : saving
      ? "Saving…"
      : where.length
        ? where.length > 2
          ? `Unsaved changes in ${where.length} sections.`
          : `Unsaved changes in ${where.join(" and ")}.`
        : "You have unsaved changes.";
  return (
    <div className="pf-savebar" role="group" aria-label="Save changes">
      <span className="pf-savebar-note" role="status">
        {note}
      </span>
      <Button variant="ghost" disabled={saving} onClick={discard}>
        Discard
      </Button>
      <Button variant="primary" loading={saving} disabled={!dirty || !!factsError} onClick={save}>
        Save changes
      </Button>
    </div>
  );
}

// ---------- the sections ----------
type SectionDef = { id: SectionId; label: string; group: string; tab: string; filter?: string };
// Each section's address keeps the old ones working: tab says which of the three old tabs it grew out of, filter is the old section name.
const SECTIONS: SectionDef[] = [
  { id: "about", label: "Personal details", group: "About you", tab: "identity" },
  { id: "work", label: "Work and education", group: "About you", tab: "experience" },
  { id: "preferences", label: "Job preferences", group: "About you", tab: "experience", filter: "preferences" },
  { id: "contacts", label: "Supervisors and references", group: "Applications ask for", tab: "knowledge", filter: "references" },
  { id: "questionnaires", label: "Questionnaires", group: "Applications ask for", tab: "knowledge" },
  { id: "permissions", label: "What AXIOM may do for you", group: "Rules", tab: "knowledge", filter: "policies" },
  { id: "answers", label: "When options don't match", group: "Rules", tab: "knowledge", filter: "rules" },
  { id: "memory", label: "Saved facts and memory", group: "Saved by AXIOM", tab: "knowledge", filter: "memory" },
];
const route_of = (s: SectionDef): Route => ({ view: "profile", tab: s.tab, filter: s.filter });

// The old Knowledge link to the facts editor lands on the saved facts, with the editor open.
function sectionOf(route: Route): SectionId {
  const filter = route.filter === "facts" ? "memory" : route.filter;
  const byFilter = SECTIONS.find((s) => s.filter && s.filter === filter && (!route.tab || s.tab === route.tab));
  if (byFilter) return byFilter.id;
  return SECTIONS.find((s) => !s.filter && s.tab === route.tab)?.id ?? "about";
}

// What each section shows beside its name: a count that is really waiting, or a quiet tally. Nothing is invented.
function metaOf(
  id: SectionId,
  p: Profile | null,
  qs: ReturnType<typeof useQuestionnaires>["data"],
  waiting: Waiting,
  changed: SectionId[],
): ReactNode {
  const dot = changed.includes(id) ? <i className="pf-nav-dot" title="Unsaved changes" /> : null;
  let meta: ReactNode = null;
  if (id === "about" && p) {
    const gaps = missingBasics(p);
    if (gaps) meta = <span className="ax-nav-count" title={`${plural(gaps, "basic detail")} missing`}>{gaps}</span>;
  } else if (id === "preferences" && p && !p.target_roles.length) {
    meta = <span className="ax-nav-count" title="No target roles yet">1</span>;
  } else if (id === "contacts") {
    const m = qs?.modules.find((x) => x.id === "contacts");
    if (waiting.applications > 0)
      meta = <span className="ax-nav-count" title={`${plural(waiting.applications, "application")} waiting`}>{waiting.applications}</span>;
    else if (m && m.status !== "complete") meta = <span className="pf-nav-meta mono">{m.answered}/{m.total}</span>;
  } else if (id === "questionnaires" && qs) {
    const rest = qs.modules.filter((m) => m.id !== "contacts");
    const answered = rest.reduce((n, m) => n + m.answered, 0);
    const total = rest.reduce((n, m) => n + m.total, 0);
    if (total && answered < total) meta = <span className="pf-nav-meta mono">{answered}/{total}</span>;
  } else if (id === "permissions" && p) {
    const { on, total } = permissionCount(p);
    meta = <span className="pf-nav-meta mono">{on}/{total}</span>;
  }
  return dot || meta ? (
    <span className="pf-nav-end">
      {dot}
      {meta}
    </span>
  ) : null;
}

/** The details nearly every application asks for. How many of them are still empty. */
function missingBasics(p: Profile) {
  const a = p.address;
  return [p.first_name, p.last_name, p.email, p.phone, a.line1, a.city, a.state, a.postal_code, a.country].filter((v) => !String(v || "").trim()).length;
}

function SectionNav({
  current,
  go,
  meta,
}: {
  current: SectionId;
  go: (route: Route) => void;
  meta: (id: SectionId) => ReactNode;
}) {
  const groups = [...new Set(SECTIONS.map((s) => s.group))];
  const nav = useRef<HTMLElement>(null);
  // On a phone the sections are one scrolling row: keep the current one in view.
  useEffect(() => {
    nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [current]);
  return (
    <nav className="pf-nav" aria-label="Profile sections" ref={nav}>
      {groups.map((g) => (
        <div className="pf-nav-group" key={g}>
          <p className="ax-nav-group">{g}</p>
          {SECTIONS.filter((s) => s.group === g).map((s) => (
            <a
              key={s.id}
              className="ax-nav-item pf-nav-item"
              href={routeUrl(route_of(s))}
              aria-current={current === s.id ? "page" : undefined}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
                e.preventDefault();
                go(route_of(s));
              }}
            >
              <span className="pf-nav-label">{s.label}</span>
              {meta(s.id)}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function ProfilePage() {
  return (
    <div className="ax-page pf-page">
      <ProfileProvider>
        <ProfileLayout />
      </ProfileProvider>
    </div>
  );
}

function ProfileLayout() {
  const state = useProfile();
  const { p, set } = state;
  const { route, go } = useWorkspace();
  const qs = useQuestionnaires();
  const waiting = useWaitingOnContacts();
  const id = sectionOf(route);

  // Each section starts at its top; the shell does this only when the tab changes.
  useEffect(() => {
    document.querySelector(".ax-scroll")?.scrollTo({ top: 0 });
  }, [id]);

  const contacts = SECTIONS.find((s) => s.id === "contacts")!;
  return (
    <>
      <TopLine>
        {waiting.applications > 0 && id !== "contacts" ? (
          <>
            {plural(waiting.applications, "application")} {waiting.applications === 1 ? "is" : "are"} waiting on supervisor or reference details.{" "}
            <a
              className="ax-link"
              href={routeUrl(route_of(contacts))}
              onClick={(e) => {
                e.preventDefault();
                go(route_of(contacts));
              }}
            >
              Add them
            </a>
          </>
        ) : (
          "What AXIOM uses to fill in your applications."
        )}
      </TopLine>
      <div className="pf">
        <SectionNav current={id} go={go} meta={(s) => metaOf(s, p, qs.data, waiting, state.changed)} />
        <div className="pf-content">
          {id === "contacts" ? (
            <ContactsSection qs={qs} waiting={waiting} />
          ) : id === "questionnaires" ? (
            <QuestionnairesSection qs={qs} waiting={waiting} />
          ) : !p ? (
            <Skeleton rows={6} mono={false} />
          ) : id === "about" ? (
            <About p={p} set={set} />
          ) : id === "work" ? (
            <WorkSection p={p} set={set} />
          ) : id === "preferences" ? (
            <Preferences p={p} set={set} />
          ) : id === "permissions" ? (
            <PermissionsSection p={p} set={set} />
          ) : id === "answers" ? (
            <AnswerRulesSection p={p} set={set} />
          ) : (
            <MemorySection state={state} />
          )}
        </div>
      </div>
    </>
  );
}

// ---------- Personal details ----------
const PHONE_TYPES = ["Mobile", "Home", "Work"];

function About({ p, set }: { p: Profile; set: Setter }) {
  const addr = p.address;
  const setAddr = (k: keyof Address, v: string) => set("address", { ...addr, [k]: v });
  const text = (k: keyof Profile, label: string, span: string, extra: { type?: string; placeholder?: string } = {}) => (
    <Input
      className={span}
      label={label}
      autoComplete="off"
      value={String(p[k] ?? "")}
      onChange={(e) => set(k, e.target.value as never)}
      {...extra}
    />
  );
  const phoneTypes = PHONE_TYPES.includes(p.phone_type) || !p.phone_type ? PHONE_TYPES : [...PHONE_TYPES, p.phone_type];
  return (
    <div className="pf-stack">
      <IdentityHeader
        name={p.preferred_name.trim() || p.name || [p.first_name, p.last_name].filter(Boolean).join(" ")}
        detail={[p.email, p.location].filter(Boolean).join(" · ")}
      />
      <Panel flush className="pf-panel">
        <Group title="Name" hint="As it appears on your ID. Forms ask for these exactly.">
          {text("first_name", "Legal first name", "pf-s2")}
          {text("middle_name", "Middle name", "pf-s2")}
          {text("last_name", "Legal last name", "pf-s2")}
          {text("name", "Full legal name", "pf-s3")}
          {text("preferred_name", "Preferred name", "pf-s3")}
        </Group>
        <Group title="Contact">
          {text("email", "Email", "pf-s3", { type: "email" })}
          {text("location", "Current location", "pf-s3", { placeholder: "City, State" })}
          {text("phone_country_code", "Phone country code", "pf-s2", { placeholder: "+1" })}
          {text("phone", "Phone", "pf-s2", { type: "tel" })}
          <Pick
            className="pf-s2"
            label="Phone type"
            value={p.phone_type || "Mobile"}
            options={phoneTypes.map((v) => ({ value: v, label: v }))}
            onChange={(v) => set("phone_type", v)}
          />
        </Group>
        <Group title="Links">
          {text("linkedin", "LinkedIn", "pf-s2", { type: "url", placeholder: "https://" })}
          {text("github", "GitHub", "pf-s2", { type: "url", placeholder: "https://" })}
          {text("website", "Website or portfolio", "pf-s2", { type: "url", placeholder: "https://" })}
        </Group>
        <Group title="Address">
          <Input className="pf-s3" label="Address line 1" value={addr.line1 || ""} onChange={(e) => setAddr("line1", e.target.value)} />
          <Input className="pf-s3" label="Address line 2" value={addr.line2 || ""} onChange={(e) => setAddr("line2", e.target.value)} />
          <Input className="pf-s2" label="City" value={addr.city || ""} onChange={(e) => setAddr("city", e.target.value)} />
          <Input className="pf-s2" label="State or province" value={addr.state || ""} onChange={(e) => setAddr("state", e.target.value)} />
          <Input className="pf-s2" label="Postal code" value={addr.postal_code || ""} onChange={(e) => setAddr("postal_code", e.target.value)} />
          <Input className="pf-s3" label="County" value={addr.county || ""} onChange={(e) => setAddr("county", e.target.value)} />
          <Input className="pf-s3" label="Country" value={addr.country || ""} onChange={(e) => setAddr("country", e.target.value)} />
        </Group>
      </Panel>
    </div>
  );
}

// ---------- Job preferences ----------
function Preferences({ p, set }: { p: Profile; set: Setter }) {
  const rules = rulesWithDefaults(p);
  const rule = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  return (
    <div className="pf-stack">
      <SectionHead title="Job preferences" line="Which jobs AXIOM looks for, and what it says when an application asks about pay and availability." />
      <Panel flush className="pf-panel">
        <Group title="Roles you want">
          <Tags
            className="pf-s6"
            label="Target roles"
            hint="Usual alternate titles count too."
            placeholder="Data Scientist, ML Engineer…"
            value={p.target_roles}
            onChange={(v) => set("target_roles", v)}
          />
          <Tags
            className="pf-s6"
            label="Target locations"
            placeholder="Anywhere in the USA, Remote…"
            value={p.target_locations}
            onChange={(v) => set("target_locations", v)}
          />
          <Tags
            className="pf-s6"
            label="Skip titles containing"
            hint="Jobs with these words in the title are left out."
            placeholder="Senior, Director…"
            value={p.excluded_keywords}
            onChange={(v) => set("excluded_keywords", v)}
          />
          <div className="ax-field pf-s6">
            <span className="ax-label">Apply first to</span>
            <Segmented
              label="Apply first to"
              value={p.job_priority ?? "new_grad_first"}
              options={[
                { value: "new_grad_first", label: "New grad roles, then best fit" },
                { value: "best_fit_first", label: "Best fit, any level" },
              ]}
              onChange={(v) => set("job_priority", v)}
            />
          </div>
          <div className="pf-s6">
            <Check
              label="Include internships and co-ops"
              note="Off applies to full-time roles only."
              checked={!!p.include_internships}
              onChange={(v) => set("include_internships", v)}
            />
          </div>
          <Range
            className="pf-s6"
            label="Minimum fit"
            hint="Jobs that score below this are not marked ready to apply. 0 to 100."
            value={p.minimum_fit}
            onChange={(v) => set("minimum_fit", v)}
          />
        </Group>
        <Group title="Pay and availability" hint="Used when an application asks and the answer isn't already saved.">
          <Input
            className="pf-s3"
            label="Salary expectation"
            hint="Leave blank to skip optional salary questions."
            value={p.salary_expectation}
            onChange={(e) => set("salary_expectation", e.target.value)}
          />
          <Input
            className="pf-s3"
            label="If no salary is saved, say"
            hint="For example Negotiable."
            maxLength={60}
            value={rules.salary_stance}
            onChange={(e) => rule({ salary_stance: e.target.value })}
          />
          <Input className="pf-s3" label="Availability or notice" placeholder="Immediately" value={p.availability} onChange={(e) => set("availability", e.target.value)} />
          <Input
            className="pf-s3"
            label="Employment sought"
            placeholder="Full-time early-career employment"
            maxLength={100}
            value={rules.employment_type_sought}
            onChange={(e) => rule({ employment_type_sought: e.target.value })}
          />
          <Input
            className="pf-s3"
            label="Willingness to travel"
            placeholder="Open to travel"
            maxLength={100}
            value={rules.travel_willingness}
            onChange={(e) => rule({ travel_willingness: e.target.value })}
          />
          <Input
            className="pf-s3"
            label="How you heard about the job"
            placeholder="LinkedIn"
            value={p.application_source}
            onChange={(e) => set("application_source", e.target.value)}
          />
        </Group>
      </Panel>
    </div>
  );
}

