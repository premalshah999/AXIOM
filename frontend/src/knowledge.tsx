// What applications ask for beyond a resume, as Profile sections: supervisors and references (one per job, and the one with
// applications waiting on it), the questionnaires as progress cards, and what AXIOM has saved (facts, employers, answers).
// The questionnaire forms live in qform.tsx so the setup guide can embed the same ones.
import { ArrowRight, Check, ClipboardList } from "lucide-react";
import { useEffect, useMemo } from "react";
import { api } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useDraft } from "./hooks";
import { Attestations, EmployerMemory, Facts, useFeature } from "./features";
import { LearnedAnswers } from "./autonomy";
import { plural } from "./format";
import type { ProfileState } from "./profile";
import { SectionHead } from "./profile-fields";
import { FactsEditor } from "./profile-rules";
import { ModuleForm, Unavailable, type Module, type useQuestionnaires } from "./qform";
import { Button, Empty, ErrorLine, Loading, MonogramStack, Skeleton, Tabs } from "./ui/index";

type Qs = ReturnType<typeof useQuestionnaires>;

// ---------- who is waiting on supervisors and references ----------
export type Waiting = { applications: number; employers: string[] };

// A question an application stopped on that supervisors and references would have answered. "Reference number" is something else.
const ASKS_CONTACTS = /supervisor|\breferences?\b(?!\s*(?:number|code|id|#))/i;

/** The applications whose open questions ask for a supervisor or a reference, counted once each. Read from the live snapshot. */
export function useWaitingOnContacts(): Waiting {
  const { snapshot } = useWorkspace();
  return useMemo(() => {
    const jobOf = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    const apps = new Map<string, string>();
    for (const r of snapshot.reviews) {
      if (!ASKS_CONTACTS.test(r.display_question || r.question)) continue;
      const jobId = jobOf.get(r.run_id) || "";
      const key = jobId || "run:" + r.run_id;
      if (!apps.has(key)) apps.set(key, jobs.get(jobId)?.company || "");
    }
    return { applications: apps.size, employers: [...apps.values()].filter(Boolean) };
  }, [snapshot]);
}

/** A bar that fills as answers come in. The words beside it say the same thing. */
function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max ? Math.round((100 * value) / max) : 0;
  return (
    <span className="pf-meter" role="img" aria-label={label}>
      <i style={{ width: pct + "%" }} />
    </span>
  );
}

const unanswered = (m: Module) => m.total - m.answered;

/** Where the open questions are, by the section the server groups them under: "Availability, Location and travel and 1 more". */
function left(m: Module) {
  if (m.status !== "partial") return "";
  const names = [...new Set(m.questions.filter((q) => !q.answered && q.section).map((q) => q.section))];
  if (!names.length) return "";
  return names.length <= 3 ? names.join(", ") : `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

// ---------- Supervisors and references ----------
export function ContactsSection({ qs, waiting }: { qs: Qs; waiting: Waiting }) {
  const { go } = useWorkspace();
  const { data, error, reload, setModule } = qs;
  if (error && !data) return <Unavailable error={error} retry={reload} />;
  if (!data) return <Skeleton rows={5} mono={false} />;
  const module = data.modules.find((m) => m.id === "contacts");
  if (!module)
    return (
      <Empty
        icon={ClipboardList}
        title="Supervisors and references aren't available yet"
        hint="They arrive with the next update of this installation. Nothing else is affected."
      />
    );
  const jobs = module.questions.find((q) => q.type === "work_rows");
  const refs = module.questions.find((q) => q.type === "references");
  const supTotal = jobs?.rows?.length ?? 0;
  const supDone = (jobs?.rows || []).filter((r) => Object.values(r.answer || {}).some(Boolean)).length;
  const refMax = refs?.max ?? 3;
  const refDone = Array.isArray(refs?.answer) ? (refs!.answer as unknown[]).length : 0;
  const n = waiting.applications;
  return (
    <div className="pf-stack">
      <SectionHead
        title="Supervisors and references"
        line="Applications ask who supervised each job and, often, for up to three references."
      />
      <section className={"pf-hero" + (n ? " is-waiting" : "")}>
        <div className="pf-hero-main">
          {n > 0 && <MonogramStack names={waiting.employers} total={waiting.employers.length} max={4} />}
          <div>
            <h3>
              {n > 0 && <i className="ax-lamp is-ask" aria-hidden="true" />}
              {n > 0
                ? `${plural(n, "application")} ${n === 1 ? "is" : "are"} waiting on this`
                : "Name them once and AXIOM never has to ask"}
            </h3>
            <p>
              AXIOM never makes these up, so an application that asks for one stops until you answer.
              {n > 0 ? " Add them here once and every application that asks gets them." : " Add them here and every application that asks gets them."}
            </p>
          </div>
          {n > 0 && (
            <a
              className="ax-more-link"
              href={routeUrl({ view: "needs" })}
              onClick={(e) => {
                e.preventDefault();
                go({ view: "needs" });
              }}
            >
              See them <ArrowRight size={13} aria-hidden="true" />
            </a>
          )}
        </div>
        <dl className="pf-hero-meters">
          <div>
            <dt>Supervisors</dt>
            <dd>
              <Meter value={supDone} max={supTotal} label={`${supDone} of ${supTotal} supervisors added`} />
              <span className="mono">
                {supDone} of {supTotal}
              </span>
            </dd>
          </div>
          <div>
            <dt>References</dt>
            <dd>
              <Meter value={refDone} max={refMax} label={`${refDone} of ${refMax} references added`} />
              <span className="mono">
                {refDone} of {refMax}
              </span>
            </dd>
          </div>
        </dl>
      </section>
      <ModuleForm module={module} onModule={setModule} bare />
    </div>
  );
}

// ---------- Questionnaires ----------
const WORDS: Record<Module["status"], string> = { not_started: "Not started", partial: "In progress", complete: "Complete" };
const VERB: Record<Module["status"], string> = { not_started: "Start", partial: "Continue", complete: "Review" };

export function QuestionnairesSection({ qs, waiting }: { qs: Qs; waiting: Waiting }) {
  const { go } = useWorkspace();
  const { data, error, reload, setModule } = qs;
  const [open, setOpen] = useDraft<string>("knowledge-module", "");
  const module = data?.modules.find((m) => m.id === open && m.id !== "contacts");
  // The form is its own focus: move to the top of the region when one opens or closes.
  useEffect(() => {
    document.querySelector(".ax-scroll")?.scrollTo({ top: 0 });
  }, [open]);
  if (error && !data) return <Unavailable error={error} retry={reload} />;
  if (!data) return <Skeleton rows={4} mono={false} />;
  if (module) return <ModuleForm module={module} onModule={setModule} onClose={() => setOpen("")} />;
  const { answered, total } = data.completion;
  return (
    <div className="pf-stack">
      <SectionHead
        title="Questionnaires"
        line={`Optional. Answer once and AXIOM fills it in on every application that asks. ${answered} of ${total} answered; each answer saves as you go.`}
      />
      <ul className="pf-quests">
        {data.modules.map((m) => {
          const contacts = m.id === "contacts";
          return (
            <li className="pf-quest" key={m.id}>
              <div className="pf-quest-top">
                <h3>{m.title}</h3>
                <span className={"pf-quest-state is-" + m.status}>
                  {m.status === "complete" ? <Check size={13} aria-hidden="true" /> : <i aria-hidden="true" />}
                  {WORDS[m.status]}
                </span>
              </div>
              <p>{m.purpose}</p>
              {left(m) && <p className="pf-quest-left">Still to answer: {left(m)}</p>}
              {contacts && waiting.applications > 0 && (
                <p className="pf-quest-note">
                  {plural(waiting.applications, "application")} {waiting.applications === 1 ? "is" : "are"} waiting on {unanswered(m) === 1 ? "this" : "these"}.
                </p>
              )}
              <Meter value={m.answered} max={m.total} label={`${m.answered} of ${m.total} answered`} />
              <div className="pf-quest-foot">
                <span className="mono">
                  {m.answered} of {m.total} answered
                </span>
                <Button
                  size="sm"
                  aria-label={`${VERB[m.status]} ${m.title}`}
                  onClick={() => (contacts ? go({ view: "profile", tab: "knowledge", filter: "references" }) : setOpen(m.id))}
                >
                  {VERB[m.status]}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------- Saved facts and memory ----------
export function MemorySection({ state }: { state: ProfileState }) {
  const probe = useFeature<{
    categories: Record<string, Record<string, number>>;
    employers: number;
    store_version: number;
  }>("knowledge", (signal) => api("/v2/knowledge/summary", "GET", undefined, signal), 120000);
  const { route } = useWorkspace();
  const full = !probe.unavailable;
  // The old link to "Edit facts as JSON" opens that tab.
  const [tab, setTab] = useDraft<string>("profile-memory-tab", route.filter === "facts" ? "json" : full ? "facts" : "answers");
  const tabs = [
    ...(full ? [{ id: "facts", label: "Confirmed facts" }] : []),
    ...(full ? [{ id: "employers", label: "Employers", count: probe.data?.employers }] : []),
    { id: "answers", label: "Saved answers" },
    { id: "json", label: "Edit as JSON" },
  ];
  const current = tabs.some((t) => t.id === tab) ? tab : tabs[0]!.id;
  return (
    <div className="pf-stack">
      <SectionHead
        title="Saved facts and memory"
        line="What AXIOM has saved from your answers: the facts you confirmed, what it remembers about each employer, and answers it reuses. Missing facts are asked once, never guessed."
      />
      <Tabs tabs={tabs} value={current} onChange={setTab} label="Saved facts and memory" />
      {current === "facts" &&
        (probe.data ? (
          <div className="pf-stack">
            <Attestations />
            <Facts summary={probe.data.categories} />
          </div>
        ) : probe.error ? (
          <ErrorLine error={probe.error} retry={probe.refresh} />
        ) : (
          <Loading />
        ))}
      {current === "employers" && <EmployerMemory />}
      {current === "answers" && <LearnedAnswers />}
      {current === "json" && <FactsEditor state={state} />}
    </div>
  );
}
