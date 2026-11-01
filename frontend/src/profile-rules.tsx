// Profile › the rules AXIOM follows: what it may do for you, what it picks when a form's options don't match, and the saved facts
// as raw JSON. All of it reads and writes the page's one draft (profile.tsx).
import { ArrowDown, ArrowUp } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "./api";
import { plural } from "./format";
import { useAction } from "./hooks";
import { Group, Pick, SectionHead, Tags } from "./profile-fields";
import type { ProfileState, Setter } from "./profile";
import { SwitchRow } from "./setup-kit";
import type { AnswerRules, DemographicRule, EducationStep, Profile } from "./types";
import { Button, Check, IconButton, Input, Panel, Textarea } from "./ui/index";

// ---------- the answer rules, with their defaults ----------
const IDENTITY_TOPICS: [string, string][] = [
  ["gender", "Gender"],
  ["race", "Race or ethnicity"],
  ["hispanic", "Hispanic or Latino"],
  ["veteran", "Veteran status"],
  ["disability", "Disability"],
  ["other", "Other identity questions"],
];

export function rulesWithDefaults(p: Profile): AnswerRules {
  const saved = p.answer_rules || {};
  const demographics: Record<string, DemographicRule> = {};
  for (const [key] of IDENTITY_TOPICS) {
    demographics[key] = {
      decline_first: p.decline_demographics,
      answer: "",
      when_missing: "closest",
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

// ---------- what AXIOM may do ----------
type Permission = { label: string; note?: string; on: boolean; toggle: () => void };

/** The permissions as switches, in the order a person thinks of them. `permissionCount` is what the section list shows. */
function permissionGroups(p: Profile, set: Setter): { title: string; items: Permission[] }[] {
  const rules = rulesWithDefaults(p);
  const rule = (next: Partial<AnswerRules>) => set("answer_rules", { ...rules, ...next });
  return [
    {
      title: "Applying",
      items: [
        {
          label: "Submit applications without asking me first",
          note: "If a form needs a fact you haven't given, that application waits for your answer and the others carry on.",
          on: p.autonomous,
          toggle: () => set("autonomous", !p.autonomous),
        },
        {
          label: "Create and reuse employer accounts",
          note: "Needed for Workday, iCIMS and other portals that make you register.",
          on: p.allow_account_creation,
          toggle: () => set("allow_account_creation", !p.allow_account_creation),
        },
        {
          label: "Apply as a guest when a site allows it",
          note: "Skips sign-in and account creation when an employer offers “Apply without an account”.",
          on: rules.guest_apply_first,
          toggle: () => rule({ guest_apply_first: !rules.guest_apply_first }),
        },
      ],
    },
    {
      title: "Agreements and consents",
      items: [
        {
          label: "Certify that my saved information is accurate",
          note: "Ticks the accuracy and privacy-notice boxes a form requires.",
          on: p.allow_application_consents,
          toggle: () => set("allow_application_consents", !p.allow_application_consents),
        },
        {
          label: "Accept application terms and conditions",
          on: p.accept_all_application_terms,
          toggle: () => set("accept_all_application_terms", !p.accept_all_application_terms),
        },
        {
          label: "Accept marketing consent a form insists on",
          note: "Only when the form won't continue without it.",
          on: !!p.accept_required_marketing,
          toggle: () => set("accept_required_marketing", !p.accept_required_marketing),
        },
        {
          label: "Open linked privacy and terms pages before accepting",
          note: "Some sites accept an agreement only after its links were opened.",
          on: rules.open_agreement_links,
          toggle: () => rule({ open_agreement_links: !rules.open_agreement_links }),
        },
      ],
    },
    {
      title: "Your privacy",
      items: [
        {
          label: "Answer “prefer not to say” to voluntary demographic questions",
          note: "Unless you've confirmed a specific answer.",
          on: p.decline_demographics,
          toggle: () => set("decline_demographics", !p.decline_demographics),
        },
        {
          label: "Join an employer's talent community",
          note: "Opts in when a form offers to keep your details on file for other roles. Off declines.",
          on: rules.join_talent_communities,
          toggle: () => rule({ join_talent_communities: !rules.join_talent_communities }),
        },
      ],
    },
  ];
}

/** How many permissions are on, and how many there are. */
export function permissionCount(p: Profile) {
  const items = permissionGroups(p, () => {}).flatMap((g) => g.items);
  return { on: items.filter((i) => i.on).length, total: items.length };
}

export function PermissionsSection({ p, set }: { p: Profile; set: Setter }) {
  const groups = permissionGroups(p, set);
  const { on, total } = permissionCount(p);
  return (
    <div className="pf-stack">
      <SectionHead
        title="What AXIOM may do for you"
        line={`${on} of ${total} on. Switch off anything you don't want done on your behalf. The changes apply to every new application once you save.`}
      />
      {groups.map((g) => (
        <section className="pf-block" key={g.title}>
          <h3 className="pf-subhead">{g.title}</h3>
          <Panel flush>
            <div className="pf-switches">
              {g.items.map((item) => (
                <SwitchRow key={item.label} label={item.label} note={item.note} checked={item.on} onChange={item.toggle} />
              ))}
            </div>
          </Panel>
        </section>
      ))}
    </div>
  );
}

// ---------- when a form's options don't match ----------
const EDUCATION_STEPS: [EducationStep, string, string][] = [
  ["closest", "Closest match", "Your fallback major (below) and the same-level degree first, then the most similar option."],
  ["pick3", "Choose from the 3 closest", "Only options close to your value are offered; unrelated ones never are."],
  ["other", "“Other” or “Not listed”", "When the list offers one."],
  ["ask", "Ask me", "The application waits for your answer while other jobs continue."],
];
const WHEN_MISSING: { value: DemographicRule["when_missing"]; label: string }[] = [
  { value: "closest", label: "Closest option" },
  { value: "other", label: "“Other”" },
  { value: "ask", label: "Ask me" },
];

export function AnswerRulesSection({ p, set }: { p: Profile; set: Setter }) {
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
    <div className="pf-stack">
      <SectionHead
        title="When options don't match"
        line="Forms offer fixed lists. When yours isn't on one, AXIOM follows these rules instead of guessing."
      />

      <Panel flush className="pf-panel">
        <Group title="A degree, major or school that isn't listed" hint="Tried top to bottom. Switch a step off to skip it.">
          <ol className="pf-steps pf-s6">
            {ordered.map((step) => {
              const [, title, note] = EDUCATION_STEPS.find(([s]) => s === step)!;
              const on = rules.education.includes(step);
              const i = rules.education.indexOf(step);
              return (
                <li key={step} className={"pf-step" + (on ? "" : " is-off")}>
                  <span className="pf-step-n mono" aria-hidden="true">
                    {on ? i + 1 : "–"}
                  </span>
                  <span className="pf-step-text">
                    <strong>{title}</strong>
                    <small>{note}</small>
                  </span>
                  <span className="pf-step-end">
                    {on && (
                      <>
                        <IconButton label={`Move ${title} earlier`} icon={<ArrowUp size={15} aria-hidden="true" />} disabled={i === 0} onClick={() => move(step, -1)} />
                        <IconButton
                          label={`Move ${title} later`}
                          icon={<ArrowDown size={15} aria-hidden="true" />}
                          disabled={i === rules.education.length - 1}
                          onClick={() => move(step, 1)}
                        />
                      </>
                    )}
                    <button
                      type="button"
                      role="switch"
                      aria-checked={on}
                      aria-label={title}
                      className="ax-switch"
                      onClick={() => toggle(step, !on)}
                    >
                      <i />
                    </button>
                  </span>
                </li>
              );
            })}
          </ol>
          <Input
            className="pf-s3"
            label="Fallback major"
            hint="Chosen when a list lacks your major, for example Computer Science. Empty chooses none."
            maxLength={100}
            value={rules.major_fallback}
            onChange={(e) => update({ major_fallback: e.target.value })}
          />
        </Group>
        <Group title="“How did you hear about us?”" hint="Your own source is used when it's offered. These are tried when it isn't.">
          <Tags
            className="pf-s6"
            label="Fallback order, best first"
            value={rules.source_order}
            onChange={(v) => update({ source_order: v })}
            placeholder="LinkedIn, Company website…"
            hint="Empty means AXIOM asks you."
          />
          <div className="pf-s6">
            <Check
              checked={rules.source_any_channel}
              onChange={(v) => update({ source_any_channel: v })}
              label="Then any other plain channel"
              note="Accepts an offered channel that claims no referral or insider relationship."
            />
          </div>
        </Group>
      </Panel>

      <section className="pf-block">
        <SectionHead
          title="Self-identification questions"
          line="Voluntary questions about gender, race, veteran status and disability. Choose how each is answered; nothing is answered beyond what you set here."
        />
        <Panel flush>
          <ul className="pf-topics">
            <li className="pf-topic pf-topics-head" aria-hidden="true">
              <span>Question</span>
              <span>Prefer not to say</span>
              <span>Your answer</span>
              <span>If it isn't listed</span>
            </li>
            {IDENTITY_TOPICS.map(([key, label]) => {
              const rule = rules.demographics[key]!;
              return (
                <li className="pf-topic" key={key}>
                  <strong className="pf-topic-name">{label}</strong>
                  <div className="pf-topic-decline">
                    <Check
                      checked={rule.decline_first}
                      onChange={(v) => topic(key, { decline_first: v })}
                      label={<span className="pf-topic-decline-label">Prefer not to say</span>}
                    />
                  </div>
                  <Input
                    className="pf-topic-answer"
                    label={`Your answer for ${label}`}
                    placeholder="From your saved facts"
                    value={rule.answer}
                    onChange={(e) => topic(key, { answer: e.target.value })}
                  />
                  <Pick
                    className="pf-topic-missing"
                    label={`If your ${label.toLowerCase()} answer isn't listed`}
                    value={rule.when_missing}
                    options={WHEN_MISSING}
                    onChange={(v) => topic(key, { when_missing: v })}
                  />
                </li>
              );
            })}
          </ul>
        </Panel>
      </section>
    </div>
  );
}

// ---------- the saved facts, as JSON ----------
export function FactsEditor({ state }: { state: ProfileState }): ReactNode {
  const { p, set, factsText, setFactsText, factsError, parsedFacts, dirty, reload } = state;
  const { pending, run } = useAction();
  if (!p) return null;
  return (
    <div className="pf-stack">
      <Panel flush className="pf-panel">
        <Group title="Confirmed facts" hint="What AXIOM knows about you, as JSON. Missing facts are asked once, never guessed.">
          <Textarea
            className="pf-s6"
            label="Facts (JSON)"
            mono
            spellCheck={false}
            rows={16}
            value={factsText}
            hint={factsError ? undefined : `${plural(Object.keys(parsedFacts).length, "fact")}.`}
            error={factsError || undefined}
            onChange={(e) => setFactsText(e.target.value)}
          />
          <Textarea
            className="pf-s6"
            label="Approved consent statements"
            hint="One exact checkbox label per line. List only commitments you accept."
            rows={4}
            value={p.approved_consents.join("\n")}
            onChange={(e) => set("approved_consents", e.target.value.split("\n").filter((x) => x.trim()))}
          />
        </Group>
      </Panel>
      <div className="pf-proposal-actions">
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
        <span className="ax-hint">{dirty ? "Save or discard your edits first." : "Only restructures facts you already saved; nothing is invented."}</span>
      </div>
    </div>
  );
}
