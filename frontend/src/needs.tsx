// Needs you: an inbox of what only the owner can settle. The list on the left leads with what frees the most applications; the
// pane on the right answers one thing, and Enter moves to the next. The same question on several jobs is one entry, and supervisors
// and references are one entry with a card per past job. What no answer can complete, and what has no question at all, look different.
import { ArrowLeft, Check, CircleSlash, FileText, MessageCircleQuestion, ShieldCheck, Users, Video } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKey } from "react";
import { api, applyJob } from "./api";
import { useWorkspace } from "./context";
import { ago, employerOf, jobWords, plainReason, plural } from "./format";
import { useAction, useDraft, usePoll } from "./hooks";
import { HistoryList } from "./history";
import { OverLimitJobs } from "./overlimit";
import { ReservedJobs } from "./reserved";
import { EMAIL, entryOf, useQuestionnaires, type Field, type Module, type WorkRow } from "./qform";
import {
  BLIND_TITLE,
  blockOf,
  deliver,
  groupQuestions,
  isBlind,
  postAnswer,
  QuestionCard,
  questionParts,
  whoOf,
  type QuestionGroup,
} from "./questions";
import { SteeredJobs, useSteered } from "./steer";
import { TopLine } from "./shell/Shell";
import { useMedia } from "./shell/prefs";
import type { Job, OverLimitPage, ReservedPage, Review, Run } from "./types";
import { Button, ConfirmButton, Empty, ErrorLine, Input, Monogram, MonogramStack, Segmented, Skeleton } from "./ui/index";
import { MoreLink } from "./identity/Results";
import "./styles/needs.css";

// ---------- supervisors and references: which saved detail answers which waiting question ----------
// The same reading as the engine's own (jobpilot/answers/people_rules.py), so what is sent here is what the next attempt would fill.
// A saved questionnaire alone frees nothing: a waiting application only reads its question's answer, so saving also answers them.
const SUPERVISOR =
  /supervisor|\bsup(?:name|title|phone|email|contact)\b|(?:reporting|direct|immediate) manager|may we contact (?:your )?(?:current |present |previous |former )?employer|employer contact permission/i;
const REFERENCE = /\breference\b|\bref ?\d|\breferee\b/i;
const PREVIOUS = /\b(?:prev(?:ious)?|prior|former|past|last)\b/i;

type Part = "name" | "first" | "last" | "title" | "name_title" | "phone" | "email" | "company" | "relationship" | "may_contact";

function partOf(label: string): Part | null {
  const text = label.toLowerCase();
  if (/\bmay we\b|\bcan we\b|permission|okay to|\bok to|allow us|authori[sz]e us|contact (?:your|this)/.test(text)) return /contact/.test(text) ? "may_contact" : null;
  if (/e-?mail/.test(text)) return "email";
  if (/phone|telephone|mobile|\bcell\b|supphone/.test(text)) return "phone";
  if (/name\s*(?:&|and|\/)\s*title|title\s*(?:&|and|\/)\s*name/.test(text)) return "name_title";
  if (/first name|given name|firstname/.test(text)) return "first";
  if (/last name|surname|family name|lastname/.test(text)) return "last";
  if (/relationship|how do you know|how you know/.test(text)) return "relationship";
  if (/company|employer|organi[sz]ation/.test(text) && !/supervisor/.test(text)) return "company";
  if (/title|position|suptitle/.test(text)) return "title";
  if (/name|supname/.test(text)) return "name";
  return null;
}

/** The text one saved person gives one waiting question, or "" when that detail isn't filled in or doesn't fit the question. */
function valueFor(part: Part, entry: Record<string, string>, review: Review) {
  const name = (entry.name || "").split(/\s+/).filter(Boolean);
  let value = "";
  if (part === "first") value = name[0] || "";
  else if (part === "last") value = name.slice(1).join(" ");
  else if (part === "name_title") value = entry.name && entry.title ? `${entry.name}, ${entry.title}` : "";
  else if (part === "may_contact") {
    const later = review.options.find((o) => /after|upon|offer|conditional|later/i.test(o));
    if (entry.may_contact === "after_offer") value = later || (review.options.length ? "No" : "After an offer only");
    else if (entry.may_contact) value = entry.may_contact === "yes" ? "Yes" : "No";
  } else value = entry[part] || "";
  return value && (!review.options.length || review.options.includes(value)) ? value : "";
}

const same = (a?: string, b?: string) => !!a && !!b && a.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim() === b.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

type Claim = { person: string; part: Part };

/** Which person a short supervisor or reference box belongs to, or null when it stays a question of its own. */
function claimOf(review: Review, rows: WorkRow[]): Claim | null {
  const q = review;
  const label = (q.question || "").replace(/[*:\s]+$/, "");
  const fits = q.kind === "text" || (q.kind === "choice" && !q.multiple);
  // A sentence ("Provide two professional references...") isn't one box: the words "name" and "email" in it say nothing.
  if (!fits || !label || label.split(/\s+/).length > 7 || q.context?.type === "checkbox") return null;
  const part = partOf(label);
  if (!part || (q.kind === "choice" && part !== "may_contact")) return null;
  if (REFERENCE.test(label) && !SUPERVISOR.test(label)) {
    if (part === "may_contact") return null;
    const number = /\bref(?:erence)?\s*#?\s*(\d)/i.exec(label) || /(?:reference|referee|ref)\D{0,3}(\d)/i.exec(label);
    const index = number ? Number(number[1]) - 1 : 0;
    return index >= 0 && index < 3 ? { person: "ref:" + index, part } : null;
  }
  if (!SUPERVISOR.test(label)) return null;
  if (q.context?.history_kind) {
    if (q.context.history_kind !== "work") return null;
    const hits = rows.filter((r) => same(r.company, q.context?.history_identity?.company));
    return hits.length === 1 ? { person: hits[0]!.row_id, part } : null;
  }
  // "Prev Supervisor Phone" doesn't say which past job; the owner decides. A form with no rows asks about the current job.
  if (PREVIOUS.test(label)) return null;
  const row = rows.find((r) => r.current) ?? rows[0];
  return row ? { person: row.row_id, part } : null;
}

type Person = {
  id: string;
  row?: WorkRow;
  index: number;
  title: string;
  where: string;
  when: string;
  asks: { review: Review; part: Part }[];
  saved: Record<string, string>;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthOf(value: string) {
  const m = /^(\d{4})-(\d{1,2})/.exec(value);
  return m ? `${MONTHS[Number(m[2]) - 1] ?? ""} ${m[1]}` : value;
}
const spanOf = (r: WorkRow) => [monthOf(r.start), r.current ? "Present" : monthOf(r.end)].filter(Boolean).join(" to ");

// ---------- the inbox's model ----------
type Reach = { frees: { job?: Job; left: number }[]; helps: { job?: Job; left: number }[] };
type Entry = {
  id: string;
  type: "people" | "question" | "blocked" | "none";
  names: string[];
  title: string;
  who: string;
  since: string;
  reach: Reach;
  group?: QuestionGroup;
  run?: Run;
  job?: Job;
};
const WAITING = new Set(["waiting_answer", "needs_review"]);
const roleOf = (job: Job) => jobWords(job).role;

function useInbox() {
  const { snapshot } = useWorkspace();
  const forms = useQuestionnaires();
  const contacts = forms.data?.modules.find((m) => m.id === "contacts");
  // The questions show at once. Supervisor and reference boxes join their one entry when the saved questionnaire arrives; waiting for
  // it kept the whole page on a skeleton for as long as that one request took (3.4 s when it took 3 s).
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setWaited(true), 350);
    return () => window.clearTimeout(timer);
  }, []);
  const settled = !!forms.data || !!forms.error || waited;

  const model = useMemo(() => {
    const runs = new Map(snapshot.runs.map((r) => [r.id, r]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    const jobOf = (runId: string) => jobs.get(runs.get(runId)?.job_id || "");
    const reviews = snapshot.reviews;

    const supervisors = contacts?.questions.find((q) => q.type === "work_rows");
    const references = contacts?.questions.find((q) => q.type === "references");
    const rows = supervisors?.rows || [];
    const claims = new Map<string, Claim>();
    if (contacts)
      for (const r of reviews) {
        const claim = claimOf(r, rows);
        if (claim) claims.set(r.id, claim);
      }

    // Every application's open questions: an application moves only when its last one is answered (resume_review).
    const keyOf = (r: Review) => runs.get(r.run_id)?.job_id || "run:" + r.run_id;
    const openBy = new Map<string, Set<string>>();
    for (const r of reviews) {
      const key = keyOf(r);
      if (!openBy.has(key)) openBy.set(key, new Set());
      openBy.get(key)!.add(r.id);
    }
    const reachOf = (list: Review[]): Reach => {
      const ids = new Set(list.map((r) => r.id));
      const seen = new Map<string, { job?: Job; left: number }>();
      for (const r of list) {
        const key = keyOf(r);
        if (!seen.has(key)) seen.set(key, { job: jobOf(r.run_id), left: [...(openBy.get(key) || [])].filter((id) => !ids.has(id)).length });
      }
      const all = [...seen.values()];
      return { frees: all.filter((x) => !x.left), helps: all.filter((x) => x.left) };
    };
    const namesOf = (r: Reach) => [...r.frees, ...r.helps].map((x) => employerOf(x.job?.company, x.job?.url));
    const jobsOf = (r: Reach) => [...r.frees, ...r.helps].map((x) => x.job);

    const entries: Entry[] = [];
    const groups = groupQuestions(reviews.filter((r) => !claims.has(r.id)), jobOf);
    for (const group of groups) {
      const reach = reachOf(group.reviews);
      const { text } = questionParts(group.review);
      entries.push({
        id: group.review.id,
        type: blockOf(group.review) ? "blocked" : "question",
        names: namesOf(reach),
        // Three "Begin Date"s on one form differ by the past job each one sits in.
        title: isBlind(group.review) ? BLIND_TITLE : [text, group.review.context?.history_identity?.company].filter(Boolean).join(", "),
        who: whoOf(jobsOf(reach)),
        since: group.reviews[0]?.created_at || "",
        reach,
        group,
      });
    }
    // What frees the most comes first; among equals, what has waited longest.
    entries.sort(
      (a, b) =>
        b.reach.frees.length - a.reach.frees.length ||
        b.reach.helps.length - a.reach.helps.length ||
        (a.since || "~").localeCompare(b.since || "~"),
    );

    // One entry per person the forms ask about.
    const people: Person[] = [];
    const claimed = reviews.filter((r) => claims.has(r.id));
    const asksOf = (id: string) =>
      claimed.filter((r) => claims.get(r.id)!.person === id).map((review) => ({ review, part: claims.get(review.id)!.part }));
    for (const r of rows)
      people.push({
        id: r.row_id,
        row: r,
        index: r.index,
        title: r.title || "Role",
        where: r.company || "Employer",
        when: spanOf(r),
        asks: asksOf(r.row_id),
        saved: entryOf(supervisors?.fields, r.answer),
      });
    const savedRefs = (references?.answer as Record<string, string>[] | undefined) || [];
    for (let i = 0; i < (references?.max || 3); i++) {
      const asks = asksOf("ref:" + i);
      if (asks.length || savedRefs[i])
        people.push({
          id: "ref:" + i,
          index: i,
          title: "Reference " + (i + 1),
          where: savedRefs[i]?.name || (asks.length ? "Not entered yet" : ""),
          when: "",
          asks,
          saved: entryOf(references?.fields, savedRefs[i]),
        });
    }
    people.sort((a, b) => Number(!b.asks.length) - Number(!a.asks.length));
    if (claimed.length) {
      const reach = reachOf(claimed);
      entries.unshift({
        id: "people",
        type: "people",
        names: namesOf(reach),
        title: "Supervisors and references",
        who: whoOf(jobsOf(reach)),
        since: claimed[0]?.created_at || "",
        reach,
      });
    }

    // Applications stopped on the owner with no question open. The list of questions is capped at 100, so past that it can't tell.
    if (reviews.length < 100 && settled) {
      const latest = new Map<string, Run>();
      for (const r of snapshot.runs) {
        const seen = latest.get(r.job_id);
        if (!seen || (r.created_at || "") > (seen.created_at || "")) latest.set(r.job_id, r);
      }
      for (const run of latest.values()) {
        if (!WAITING.has(run.state) || openBy.has(run.job_id)) continue;
        const job = jobs.get(run.job_id);
        const reach = { frees: [{ job, left: 0 }], helps: [] };
        entries.push({
          id: "run:" + run.id,
          type: "none",
          names: namesOf(reach),
          title: job ? [jobWords(job).role, jobWords(job).hint].filter(Boolean).join(" · ") : "Application",
          who: employerOf(job?.company, job?.url),
          since: run.created_at || "",
          reach,
          run,
          job,
        });
      }
    }
    const order = { people: 0, question: 1, blocked: 2, none: 3 };
    entries.sort((a, b) => order[a.type] - order[b.type]);

    const applications = new Set(reviews.map(keyOf)).size;
    // The number the sidebar and Overview show: one per distinct question.
    const questions = groups.length + groupQuestions(claimed, jobOf).length;
    // A sentence asking for references ("Please add 3 references") can take the saved ones, once the owner has read them.
    const saved = savedRefs.filter((r) => r && Object.values(r).some(Boolean));
    const suggestionFor = (review: Review) =>
      saved.length && /\breferences?\b/i.test(review.question)
        ? {
            label: `Use my ${plural(saved.length, "saved reference")}`,
            value: saved.map((r) => [r.name, r.relationship, r.company, r.phone, r.email].filter(Boolean).join(", ")).join("\n"),
          }
        : undefined;
    return { entries, people, applications, questions, peopleQuestions: groupQuestions(claimed, jobOf).length, suggestionFor };
  }, [snapshot.reviews, snapshot.runs, snapshot.jobs, contacts, settled]);

  return { ...model, contacts, setModule: forms.setModule, settled };
}

// What only you can decide that is not a question: jobs waiting for your approval (over your employer limit, at a reserved employer),
// and applications sent without a receipt. Each count is read from the list it opens.
type Decision = { id: string; title: string; note: string; count: number };

function useDecisions(): Decision[] {
  const { version, counts, route } = useWorkspace();
  const steered = useSteered();
  const over = usePoll<OverLimitPage>((signal) => api("/over-limit?page=1&size=1", "GET", undefined, signal), 30000, [version]);
  const reserved = usePoll<ReservedPage>((signal) => api("/reserved?page=1&size=1", "GET", undefined, signal), 30000, [version]);
  const list: Decision[] = [
    { id: "decide:over", title: "Over your employer limit", note: "Approve to apply anyway", count: over.data?.count ?? 0 },
    { id: "decide:reserved", title: "Reserved", note: "Applied only after you approve each one", count: (reserved.data?.sections ?? []).reduce((n, s) => n + s.held, 0) },
    { id: "decide:steer", title: "In your hands", note: "Applications you took over", count: steered.length },
    { id: "decide:unconfirmed", title: "Sent, with no receipt", note: "Check your email, or confirm it yourself", count: counts.groups.unconfirmed ?? 0 },
  ];
  return list.filter((d) => d.count > 0 || d.id === "decide:" + route.filter);
}

function DecisionRow({ entry, current, onPick }: { entry: Decision; current: boolean; onPick: () => void }) {
  return (
    <button type="button" className="nd-row is-decide" aria-current={current || undefined} data-entry={entry.id} onClick={onPick}>
      <span className="nd-row-mono">
        <span className="ax-mono nd-people-mono" aria-hidden="true">
          <ShieldCheck size={13} />
        </span>
      </span>
      <span className="nd-row-main">
        <span className="nd-row-title">{entry.title}</span>
        <small>{entry.note}</small>
      </span>
      <span className="nd-row-end">
        <span className="nd-frees">{entry.count.toLocaleString()}</span>
      </span>
    </button>
  );
}

// ---------- the page ----------
export function NeedsView() {
  const { counts, go, route } = useWorkspace();
  const inbox = useInbox();
  const decisions = useDecisions();
  const steered = useSteered();
  const phone = useMedia("(max-width: 899px)");
  const [skipped, setSkipped] = useDraft<string[]>("needs-skipped", []);
  const [done, setDone] = useDraft("needs-done", 0);
  const [sending, setSending] = useState<string[]>([]);
  const [selected, setSelected] = useState(route.filter === "over" || route.filter === "reserved" || route.filter === "unconfirmed" ? "decide:" + route.filter : "");
  const [opened, setOpened] = useState(selected !== "");
  const [said, setSaid] = useState("");
  const [ticked, setTicked] = useState<ReadonlySet<string>>(() => new Set());
  const bulk = useAction();
  const { refresh } = useWorkspace();
  const pane = useRef<HTMLDivElement>(null);
  // Progress is what really left the list, by whichever way: answered here, dropped, or settled by the server from another answer.
  const seen = useRef<number | null>(null);
  useEffect(() => {
    if (!inbox.settled) return;
    const gone = seen.current === null ? 0 : seen.current - inbox.questions;
    if (gone > 0) setDone((d) => d + gone);
    seen.current = inbox.questions;
  }, [inbox.questions, inbox.settled, setDone]);
  const wantFocus = useRef(!matchMedia("(pointer: coarse)").matches);

  const parked = inbox.entries.filter((e) => skipped.includes(e.id) && !sending.includes(e.id));
  const open = inbox.entries.filter((e) => !skipped.includes(e.id) && !sending.includes(e.id));
  const answerable = open.filter((e) => e.type !== "none");
  const deciding = selected.startsWith("decide:") ? selected : !open.length && !parked.length && decisions[0] ? decisions[0].id : "";
  const current = deciding ? undefined : inbox.entries.find((e) => e.id === selected && !sending.includes(e.id)) ?? open[0];
  const currentId = current?.id;

  // Answering moves straight on: the next question's field has focus, so a run of answers is typed without the mouse.
  useEffect(() => {
    pane.current?.scrollTo?.({ top: 0 });
    if (!wantFocus.current || !currentId) return;
    const field = pane.current?.querySelector<HTMLElement>('[data-answer]:not([type="file"]), .nd-control .ax-listbox, .nd-person-form input');
    wantFocus.current = false;
    field?.focus({ preventScroll: true });
  }, [currentId, inbox.settled]);
  useEffect(() => {
    document.querySelector(".nd-row[aria-current]")?.scrollIntoView({ block: "nearest" });
  }, [currentId]);

  const pick = useCallback((id: string) => {
    setSelected(id);
    setOpened(true);
    wantFocus.current = !matchMedia("(pointer: coarse)").matches;
  }, []);
  const order = open.map((e) => e.id);
  // Several at once: what they share is leaving the list. Every answer stays a person's own, one question at a time.
  const pickable = open;
  const pickedRows = pickable.filter((e) => ticked.has(e.id));
  const allPicked = pickable.length > 0 && pickedRows.length === pickable.length;
  const toggle = useCallback((id: string) => setTicked((was) => {
    const next = new Set(was);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }), []);
  const pickedJobs = [...new Map(pickedRows.flatMap((e) => [...e.reach.frees, ...e.reach.helps]).filter((x) => !!x.job).map((x) => [x.job!.id, x.job!])).values()];
  const skipPicked = () => {
    setSkipped((list) => [...new Set([...list, ...pickedRows.map((e) => e.id)])]);
    if (current && ticked.has(current.id)) setSelected(order.find((id) => !ticked.has(id)) ?? "");
    setTicked(new Set());
  };
  const dropPicked = () =>
    bulk.run(
      "drop",
      async () => {
        const ids = pickedJobs.map((j) => j.id);
        let dropped = 0;
        for (let i = 0; i < ids.length; i += 4) {
          const done = await Promise.allSettled(ids.slice(i, i + 4).map((id) => api("/jobs/" + id, "DELETE")));
          dropped += done.filter((d) => d.status === "fulfilled").length;
        }
        setSkipped((list) => list.filter((x) => !ticked.has(x)));
        setTicked(new Set());
        await refresh();
        return dropped;
      },
      (dropped) => `Dropped ${plural(dropped ?? 0, "job")}. Nothing was sent to ${(dropped ?? 0) === 1 ? "the employer" : "those employers"}.`,
    );
  // An application with nothing to answer can only be tried again: the server's one-click path decides each.
  const retryPicked = () =>
    bulk.run(
      "retry",
      async () => {
        const ids = pickedJobs.map((j) => j.id);
        const results: { outcome: string }[] = [];
        for (let i = 0; i < ids.length; i += 50) results.push(...(await api<{ results: { outcome: string }[] }>("/jobs/apply-many", "POST", { job_ids: ids.slice(i, i + 50) })).results);
        setTicked(new Set());
        await refresh();
        return { started: results.filter((r) => !["already", "refused"].includes(r.outcome)).length, not: results.filter((r) => ["already", "refused"].includes(r.outcome)).length };
      },
      (r) => r && `Started ${plural(r.started, "job")} again.${r.not ? ` ${plural(r.not, "job")} didn't start: open ${r.not === 1 ? "it" : "them"} to see why.` : ""}`,
    );
  const advance = (from: string) => {
    const at = order.indexOf(from);
    const next = order[at + 1] ?? order[at - 1] ?? "";
    setSelected(next);
    wantFocus.current = !matchMedia("(pointer: coarse)").matches;
    if (!next) setOpened(false);
  };
  const skip = (id: string) => {
    advance(id);
    setSkipped((list) => (list.includes(id) ? list : [...list, id]));
  };
  const unskip = (id: string) => {
    setSkipped((list) => list.filter((x) => x !== id));
    pick(id);
  };

  // Option Up and Down walk the list from anywhere; Option S skips. Nothing here fires while a drawer or dialog is on top.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!e.altKey || !currentId || document.querySelector(".ax-drawer, .ax-dialog")) return;
      if (e.code === "KeyS") {
        e.preventDefault();
        skip(currentId);
      } else if (e.code === "ArrowDown" || e.code === "ArrowUp") {
        e.preventDefault();
        const at = order.indexOf(currentId);
        const next = order[at + (e.code === "ArrowDown" ? 1 : -1)];
        if (next) pick(next);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });
  const listKeys = (e: ReactKey<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = [...e.currentTarget.querySelectorAll<HTMLElement>(".nd-row")];
    const at = rows.indexOf(document.activeElement as HTMLElement);
    const next = rows[at + (e.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      e.preventDefault();
      next.focus();
      next.click();
      wantFocus.current = false;
    }
  };

  if (!inbox.settled)
    return (
      <div className="ax-page">
        <Skeleton rows={6} />
      </div>
    );

  if (!inbox.entries.length && !sending.length && !decisions.length)
    return (
      <div className="ax-page">
        <Empty
          icon={done ? Check : MessageCircleQuestion}
          title={done ? "That's everything" : "Nothing is waiting on you"}
          hint={
            done
              ? `${plural(done, "question")} cleared. The applications carry on by themselves.`
              : "Questions appear here when an application needs a personal fact your profile doesn't have."
          }
          action={
            <Button size="sm" onClick={() => go({ view: "batch" })}>
              Back to Overview
            </Button>
          }
        />
      </div>
    );

  const sections: { name: string; note?: string; items: Entry[] }[] = [
    { name: "", items: open.filter((e) => e.type === "people") },
    { name: "Questions", items: open.filter((e) => e.type === "question") },
    { name: "Need something only you can make", items: open.filter((e) => e.type === "blocked") },
    { name: "Nothing to answer", items: open.filter((e) => e.type === "none") },
  ];
  const left = answerable.reduce((n, e) => n + (e.type === "people" ? inbox.peopleQuestions : 1), 0);
  const total = done + inbox.questions;
  const showList = !phone || !opened || (!current && !deciding);
  const showPane = !phone || (opened && (!!current || !!deciding));
  const waitingOnYou = decisions.filter((d) => d.id !== "decide:unconfirmed" && d.id !== "decide:steer").reduce((n, d) => n + d.count, 0);

  return (
    <div className="ax-page ax-page-fill nd-page-root">
      <TopLine>
        {inbox.questions
          ? `${plural(inbox.questions, "question")} ${inbox.questions === 1 ? "is" : "are"} holding back ${plural(inbox.applications, "application")}.`
          : "No questions are open."}
        {waitingOnYou ? ` ${plural(waitingOnYou, "job")} ${waitingOnYou === 1 ? "waits" : "wait"} for your approval.` : ""}
      </TopLine>
      <div className={"nd" + (showList ? "" : " is-pane") + (showPane ? "" : " is-list")}>
        {showList && (
          <div className="nd-list">
            <div className="nd-progress">
              <p aria-live="polite">
                <span>
                  {done} of {total} cleared
                </span>
                {said && <small>{said}</small>}
              </p>
              <div className="nd-track" aria-hidden="true">
                <i style={{ width: total ? `${(100 * done) / total}%` : 0 }} />
              </div>
            </div>
            {pickable.length > 0 && (
              <div className="nd-bulk" role="status">
                <label className="ax-check">
                  <input
                    type="checkbox"
                    checked={allPicked}
                    ref={(el) => {
                      if (el) el.indeterminate = !allPicked && pickedRows.length > 0;
                    }}
                    onChange={() => setTicked(allPicked ? new Set() : new Set(pickable.map((e) => e.id)))}
                  />
                  <span>{pickedRows.length ? `${pickedRows.length.toLocaleString()} selected` : `Select all ${pickable.length.toLocaleString()}`}</span>
                </label>
                {pickedRows.length > 0 && (
                  <>
                    <Button size="sm" variant="ghost" disabled={bulk.pending !== null} onClick={skipPicked}>
                      Skip
                    </Button>
                    {pickedRows.every((e) => e.type === "none") && (
                      <ConfirmButton label="Try again" confirm="Yes, try again" note={`AXIOM sends a real application to each, in its turn. One already sent is never sent twice.`} variant="secondary" size="sm" pending={bulk.pending === "retry"} onConfirm={retryPicked} />
                    )}
                    {pickedJobs.length > 0 && (
                      <ConfirmButton label={`Drop ${plural(pickedJobs.length, "job")}`} confirm="Yes, drop them" note="They leave your lists. Nothing is sent to the employers." size="sm" pending={bulk.pending === "drop"} onConfirm={dropPicked} />
                    )}
                  </>
                )}
              </div>
            )}
            <div className="nd-scroll" onKeyDown={listKeys}>
              {sections.map(
                (s) =>
                  s.items.length > 0 && (
                    <section key={s.name || "people"} aria-label={s.name || "Supervisors and references"}>
                      {s.name && (
                        <h2 className="nd-sec">
                          {s.name} <span>{s.items.length}</span>
                        </h2>
                      )}
                      {s.items.map((e) => (
                        <div key={e.id} className="nd-rowline">
                          <label className="nd-pick">
                            <input type="checkbox" aria-label={`Select ${e.type === "none" ? e.who : e.title}`} checked={ticked.has(e.id)} onChange={() => toggle(e.id)} />
                          </label>
                          <InboxRow entry={e} questions={inbox.peopleQuestions} current={e.id === currentId && !phone} onPick={pick} />
                        </div>
                      ))}
                    </section>
                  ),
              )}
              {decisions.length > 0 && (
                <section aria-label="Waiting for your decision">
                  <h2 className="nd-sec">Waiting for your decision</h2>
                  {decisions.map((d) => (
                    <DecisionRow key={d.id} entry={d} current={d.id === deciding && !phone} onPick={() => pick(d.id)} />
                  ))}
                </section>
              )}
              {parked.length > 0 && (
                <details className="nd-parked" open={parked.some((e) => e.id === currentId) || undefined}>
                  <summary>
                    Skipped <span>{parked.length}</span>
                  </summary>
                  {parked.map((e) => (
                    <InboxRow key={e.id} entry={e} questions={inbox.peopleQuestions} current={e.id === currentId && !phone} onPick={pick} />
                  ))}
                </details>
              )}
              {(counts.groups.needs ?? 0) > inbox.applications + inbox.entries.filter((e) => e.type === "none").length && (
                <div className="nd-foot">
                  <MoreLink onClick={() => pick("decide:all")}>
                    All {(counts.groups.needs ?? 0).toLocaleString()} applications
                  </MoreLink>
                </div>
              )}
            </div>
          </div>
        )}
        {showPane && (
          <div className="nd-pane" ref={pane}>
            {phone && (
              <div className="nd-back">
                <Button variant="ghost" size="sm" icon={<ArrowLeft size={14} aria-hidden="true" />} onClick={() => setOpened(false)}>
                  All questions
                </Button>
                <span className="ax-note">{plural(left, "question")} to go</span>
              </div>
            )}
            {deciding ? (
              <div className="nd-pane-in nd-decide">
                {deciding === "decide:steer" && <SteeredJobs jobs={steered} />}
                {deciding === "decide:over" && <OverLimitJobs />}
                {deciding === "decide:reserved" && <ReservedJobs />}
                {deciding === "decide:unconfirmed" && <HistoryList word="unconfirmed" inline />}
                {deciding === "decide:all" && <HistoryList word="needs" inline />}
              </div>
            ) : current ? (
              <div className="nd-pane-in">
                {skipped.includes(current.id) && (
                  <p className="nd-parked-note">
                    You skipped this.{" "}
                    <button type="button" className="ax-link" onClick={() => unskip(current.id)}>
                      Put it back in the list
                    </button>
                  </p>
                )}
                <Asking entry={current} />
                {current.type === "people" && inbox.contacts ? (
                  <PeoplePane
                    key="people"
                    module={inbox.contacts}
                    people={inbox.people}
                    questions={inbox.peopleQuestions}
                    applications={current.reach.frees.length + current.reach.helps.length}
                    onModule={inbox.setModule}
                    onAnswered={setSaid}
                    onFinished={() => advance(current.id)}
                    onSkip={() => skip(current.id)}
                  />
                ) : current.group ? (
                  <QuestionCard
                    key={current.group.review.id}
                    group={current.group}
                    suggestion={inbox.suggestionFor(current.group.review)}
                    onSkip={skip}
                    onSending={(id) => {
                      advance(id);
                      setSaid("");
                      setSending((list) => [...list, id]);
                    }}
                    onSaved={(id, message) => {
                      setSending((list) => list.filter((x) => x !== id));
                      setSkipped((list) => list.filter((x) => x !== id));
                      setSaid(message || "");
                    }}
                    onFailed={(id) => {
                      setSending((list) => list.filter((x) => x !== id));
                      setSelected(id);
                      setOpened(true);
                    }}
                    onDropped={(id) => {
                      advance(id);
                      setSkipped((list) => list.filter((x) => x !== id));
                    }}
                  />
                ) : current.run ? (
                  <NothingPane key={current.id} entry={current} onGone={() => advance(current.id)} onSkip={() => skip(current.id)} />
                ) : null}
                {current.type !== "none" && <Unblocks entry={current} />}
              </div>
            ) : (
              <Empty
                compact
                icon={Check}
                title={parked.length ? "Only what you skipped is left" : "That's the last one"}
                hint={parked.length ? "Open Skipped in the list whenever you're ready." : `${plural(done, "question")} cleared.`}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------- a row of the list ----------
const InboxRow = memo(function InboxRow({ entry: e, questions, current, onPick }: { entry: Entry; questions: number; current: boolean; onPick: (id: string) => void }) {
  const frees = e.reach.frees.length;
  const asksVideo = e.type === "blocked" && !!e.group && blockOf(e.group.review)?.hard;
  const sub =
    e.type === "people"
      ? `${plural(questions, "question")} on ${plural(frees + e.reach.helps.length, "application")}`
      : [e.who, e.since && e.type !== "none" && ago(e.since)].filter(Boolean).join(" · ");
  return (
    <button type="button" className={"nd-row is-" + e.type} aria-current={current || undefined} data-entry={e.id} onClick={() => onPick(e.id)}>
      <span className="nd-row-mono">
        {e.type === "people" ? (
          <span className="ax-mono nd-people-mono" aria-hidden="true">
            <Users size={13} />
          </span>
        ) : (
          <MonogramStack names={e.names} max={e.names.length > 2 ? 1 : 2} />
        )}
      </span>
      <span className="nd-row-main">
        <span className="nd-row-title">{e.type === "none" ? e.who : e.title}</span>
        <small>{e.type === "none" ? e.title : sub}</small>
      </span>
      <span className="nd-row-end">
        {e.type === "blocked" && (asksVideo ? <Video size={15} aria-label="Asks for a video" /> : <FileText size={15} aria-label="Asks for your own work" />)}
        {e.type === "none" && <CircleSlash size={15} aria-label="Nothing to answer" />}
        {e.type !== "blocked" && e.type !== "none" && frees > 1 && <span className="nd-frees">Frees {frees}</span>}
      </span>
    </button>
  );
});

// ---------- the pane's parts ----------
/** Who is asking: the first thing in the pane, each one opening its job. */
function Asking({ entry: e }: { entry: Entry }) {
  const { openJob } = useWorkspace();
  if (e.type === "people" || e.type === "none") return null;
  const rows = [...e.reach.frees, ...e.reach.helps];
  const only = rows.length === 1 ? rows[0]?.job : undefined;
  const who = (
    <>
      <MonogramStack names={e.names} max={3} />
      <span className="nd-asking-who" title={only?.title}>
        <strong>{e.who}</strong>
        {only && <span>{roleOf(only)}</span>}
      </span>
    </>
  );
  return only ? (
    <button type="button" className="nd-asking" onClick={() => openJob(only.id, e.group?.review.run_id)}>
      {who}
    </button>
  ) : (
    <div className="nd-asking">{who}</div>
  );
}

/** What answering changes, in the owner's terms: an application moves only once its last question is answered. */
function Unblocks({ entry: e }: { entry: Entry }) {
  const { openJob } = useWorkspace();
  const { frees, helps } = e.reach;
  const blocked = e.type === "blocked";
  const rows = [
    ...frees.map((x) => ({ ...x, note: blocked ? "" : "Continues" })),
    ...helps.map((x) => ({ ...x, note: `${plural(x.left, "more question")} after this` })),
  ];
  if (!rows.length) return null;
  // One application is already named at the top of the pane: a sentence is enough.
  if (rows.length === 1 && e.type === "question")
    return (
      <p className="nd-reach-one">
        <i className={"ax-lamp " + (frees.length ? "is-ok" : "is-hold")} aria-hidden="true" />
        {frees.length
          ? "This is the last thing it needs: answering lets the application continue."
          : `It still needs ${plural(helps[0]!.left, "more question")} from you after this one.`}
      </p>
    );
  if (rows.length === 1 && blocked) return null;
  const heading = blocked
    ? `Holding up ${plural(rows.length, "application")}`
    : frees.length
      ? `Answering frees ${plural(frees.length, "application")}${helps.length ? `, and helps ${helps.length} more` : ""}`
      : `Helps ${plural(helps.length, "application")}, which ${helps.length === 1 ? "needs" : "need"} more from you`;
  const SHOW = 8;
  return (
    <section className="nd-reach" aria-label="What this frees">
      <h3>{heading}</h3>
      <ul>
        {rows.slice(0, SHOW).map((x, i) => {
          const words = x.job ? jobWords(x.job) : null;
          const name = words?.company || "Employer";
          return (
            <li key={x.job?.id || i}>
              <button type="button" disabled={!x.job} onClick={() => x.job && openJob(x.job.id)}>
                <Monogram name={name} employer={!!words?.company} />
                <strong title={name}>{name}</strong>
                <span className="nd-reach-role" title={x.job?.title}>
                  {words && [words.role, x.job?.location].filter(Boolean).join(" · ")}
                  {words?.hint && <span className="nd-reach-hint">{words.hint}</span>}
                </span>
                {x.note && <small>{x.note}</small>}
              </button>
            </li>
          );
        })}
      </ul>
      {rows.length > SHOW && <p className="ax-note">And {rows.length - SHOW} more.</p>}
    </section>
  );
}

/** An application stopped on the owner, with no question to answer. Not dressed as one. */
function NothingPane({ entry: e, onGone, onSkip }: { entry: Entry; onGone: () => void; onSkip: () => void }) {
  const { refresh, openJob, snapshot } = useWorkspace();
  const { pending, run } = useAction();
  const job = e.job;
  const reason = plainReason(e.run?.reason);
  // Waiting for an answer with none open: every question has one, and it hasn't started again.
  const answered = e.run?.state === "waiting_answer";
  return (
    <div className="nd-card">
      <div className="nd-asking">
        <Monogram name={e.who} size="md" employer={e.names.length <= 1} />
        <span className="nd-asking-who">
          <strong>{e.who}</strong>
          <span>{e.title}</span>
        </span>
      </div>
      <header className="nd-card-head">
        <h2 className="nd-q">Nothing to answer here</h2>
        <p className="nd-meta">
          {answered ? "Everything it asked has an answer, but it hasn't started again." : "This application stopped without leaving a question for you."}{" "}
          {snapshot.control.paused ? "Applying is paused, so it waits for that." : "Try it again, or let it go."}
        </p>
      </header>
      {reason && (
        <div className="nd-block">
          <CircleSlash size={18} aria-hidden="true" />
          <p>It stopped on: {reason}</p>
        </div>
      )}
      <div className="nd-actions">
        {job && (
          <Button
            variant="primary"
            loading={pending === "retry"}
            onClick={() =>
              void run(
                "retry",
                async () => {
                  const r = await applyJob(job.id);
                  await refresh();
                  onGone();
                  return r;
                },
                (r) => r?.message,
              )
            }
          >
            Try again
          </Button>
        )}
        <Button variant="ghost" onClick={onSkip}>
          Skip
        </Button>
        {job && (
          <Button variant="ghost" onClick={() => openJob(job.id, e.run?.id)}>
            Open job
          </Button>
        )}
        {job && (
          <span className="nd-actions-end">
            <ConfirmButton
              label="Drop this job"
              confirm="Yes, drop it"
              size="md"
              pending={pending === "drop"}
              onConfirm={() =>
                run(
                  "drop",
                  async () => {
                    await api("/jobs/" + job.id, "DELETE");
                    await refresh();
                    onGone();
                  },
                  "Job dropped.",
                )
              }
            />
          </span>
        )}
      </div>
    </div>
  );
}

// ---------- supervisors and references: one entry per past job ----------
function PeoplePane({
  module,
  people,
  questions,
  applications,
  onModule,
  onAnswered,
  onFinished,
  onSkip,
}: {
  module: Module;
  people: Person[];
  questions: number;
  applications: number;
  onModule: (m: Module) => void;
  onAnswered: (message: string) => void;
  onFinished: () => void;
  onSkip: () => void;
}) {
  const { refresh, snapshot } = useWorkspace();
  const [entries, setEntries] = useState<Record<string, Record<string, string>>>({});
  const needed = people.filter((p) => p.asks.length);
  const [openId, setOpenId] = useState(needed[0]?.id || people[0]?.id || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [bad, setBad] = useState("");
  const supervisors = module.questions.find((q) => q.type === "work_rows");
  const references = module.questions.find((q) => q.type === "references");

  const entryFor = (p: Person) => entries[p.id] ?? p.saved;
  const fieldsFor = (p: Person) => (p.row ? supervisors : references)?.fields || [];
  const move = (from: Person) => {
    const at = needed.indexOf(from);
    const next = [...needed.slice(at + 1), ...needed.slice(0, Math.max(at, 0))].find((p) => p.id !== from.id && !Object.values(entryFor(p)).some(Boolean));
    if (next) setOpenId(next.id);
    else onFinished();
  };

  const save = async (p: Person) => {
    const entry = Object.fromEntries(Object.entries(entryFor(p)).map(([k, v]) => [k, (v || "").trim()]));
    if (entry.email && !EMAIL.test(entry.email)) {
      setBad(p.id);
      return;
    }
    setBad("");
    setError("");
    setBusy(true);
    try {
      if (JSON.stringify(entry) !== JSON.stringify(p.saved)) {
        const answers = p.row
          ? { [supervisors!.key]: { [p.row.row_id]: entry } }
          : {
              [references!.key]: people
                .filter((x) => !x.row)
                .map((x) => (x.id === p.id ? entry : entryFor(x)))
                .filter((x) => Object.values(x).some(Boolean)),
            };
        const saved = await api<{ module: Module }>(`/knowledge/questionnaires/${module.id}`, "PUT", { answers, accept_suggestions: false });
        onModule(saved.module);
      }
      // Saving teaches the next form; the applications already waiting only move on their own question's answer.
      // A form's own rows go first. An answer with no row also lands on every open row of the same field (answer_review), which
      // would give the other jobs this supervisor's name, so it waits until those rows have their own; any save can then send it.
      const rowOf = (r: Review) => !!r.context?.history_kind;
      const rowed = p.asks.filter((a) => rowOf(a.review)).map((a) => ({ review: a.review, value: valueFor(a.part, entry, a.review) })).filter((a) => a.value);
      const sent = new Set(rowed.map((a) => a.review.id));
      const clear = (r: Review) => !snapshot.reviews.some((o) => o.key === r.key && o.id !== r.id && rowOf(o) && !sent.has(o.id));
      const rowless = people
        .flatMap((x) => x.asks.filter((a) => !rowOf(a.review)).map((a) => ({ review: a.review, value: valueFor(a.part, x.id === p.id ? entry : x.saved, a.review) })))
        .filter((a) => a.value && clear(a.review));
      const ready = [...rowed, ...rowless];
      const values = new Map(ready.map((a) => [a.review.id, a.value]));
      if (ready.length) {
        await deliver(
          ready.map((a) => a.review),
          (r) => postAnswer(r, values.get(r.id)!),
        );
        await refresh();
        onAnswered(`Saved. ${plural(ready.length, "waiting question")} answered.`);
      }
      move(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="nd-card nd-people">
      <header className="nd-card-head">
        <h2 className="nd-q">Supervisors and references</h2>
        <p className="nd-meta">
          {plural(questions, "question")} on {plural(applications, "application")} come down to {plural(needed.length, "person", "people")}. Enter each one once:
          it goes to every form asking and stays in your profile, so new forms fill themselves.
        </p>
      </header>
      <div className="nd-persons">
        {people.map((p) => (
          <PersonCard
            key={p.id}
            person={p}
            fields={fieldsFor(p)}
            contact={(supervisors?.option_labels?.may_contact || {}) as Record<string, string>}
            entry={entryFor(p)}
            open={openId === p.id}
            busy={busy}
            bad={bad === p.id}
            error={openId === p.id ? error : ""}
            onOpen={() => setOpenId(openId === p.id ? "" : p.id)}
            onChange={(entry) => setEntries((all) => ({ ...all, [p.id]: entry }))}
            onSave={() => void save(p)}
            onNext={() => move(p)}
          />
        ))}
      </div>
      <div className="nd-actions">
        <Button variant="ghost" onClick={onSkip}>
          Skip all of these for now
        </Button>
      </div>
    </div>
  );
}

function PersonCard({
  person: p,
  fields,
  contact,
  entry,
  open,
  busy,
  bad,
  error,
  onOpen,
  onChange,
  onSave,
  onNext,
}: {
  person: Person;
  fields: Field[];
  contact: Record<string, string>;
  entry: Record<string, string>;
  open: boolean;
  busy: boolean;
  bad: boolean;
  error: string;
  onOpen: () => void;
  onChange: (entry: Record<string, string>) => void;
  onSave: () => void;
  onNext: () => void;
}) {
  const form = useRef<HTMLFormElement>(null);
  const filled = Object.values(entry).some(Boolean);
  const hasSaved = Object.values(p.saved).some(Boolean);
  const employers = [...new Set(p.asks.map((a) => a.review.run_id))].length;
  // Each new card opens ready to type.
  useEffect(() => {
    if (open) form.current?.querySelector<HTMLElement>("input")?.focus({ preventScroll: true });
  }, [open]);
  // In the fields' own words: "name and title" and "name" are two questions but two details.
  const details = [
    ...new Set(p.asks.flatMap((a): string[] => (a.part === "name_title" ? ["name", "title"] : a.part === "first" || a.part === "last" ? ["name"] : a.part === "may_contact" ? ["permission to contact"] : [a.part]))),
  ];
  const asksFor = details.length > 1 ? `${details.slice(0, -1).join(", ")} and ${details[details.length - 1]}` : details[0];
  const status = p.asks.length ? `Asked on ${plural(employers, "application")}` : hasSaved ? "Saved" : "Not asked yet";
  const inputs = fields.filter((f) => f.key !== "may_contact");
  return (
    <article className={"nd-person" + (open ? " is-open" : "") + (p.asks.length ? "" : " is-quiet")}>
      <button type="button" className="nd-person-head" aria-expanded={open} onClick={onOpen}>
        <Monogram name={p.row ? p.where : p.title} />
        <span className="nd-person-name">
          <strong title={p.title}>{p.title}</strong>
          <small title={p.where}>{[p.where, p.when].filter(Boolean).join(" · ")}</small>
        </span>
        <span className="nd-person-state">
          {hasSaved && !p.asks.length && <Check size={13} aria-hidden="true" />}
          {status}
        </span>
      </button>
      {open && (
        <form
          ref={form}
          className="nd-person-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (filled && !busy) onSave();
          }}
        >
          <div className="nd-person-grid">
            {inputs.map((f) => (
              <Input
                key={f.key}
                label={f.prompt}
                type={f.key === "email" ? "email" : f.key === "phone" ? "tel" : "text"}
                autoComplete="off"
                maxLength={200}
                value={entry[f.key] || ""}
                error={f.key === "email" && bad ? "Enter a valid email address" : undefined}
                onChange={(e) => onChange({ ...entry, [f.key]: e.target.value })}
              />
            ))}
          </div>
          {fields.some((f) => f.key === "may_contact") && (
            <div className="nd-person-contact">
              <span className="ax-label">May we contact this supervisor?</span>
              <Segmented
                label={`May we contact the supervisor at ${p.where}?`}
                value={entry.may_contact || ""}
                options={Object.entries(contact).map(([value, label]) => ({ value, label }))}
                onChange={(value) => onChange({ ...entry, may_contact: value })}
              />
            </div>
          )}
          <ErrorLine error={error} />
          <div className="nd-actions">
            <Button type="submit" variant="primary" disabled={!filled} loading={busy}>
              {p.asks.length ? "Save and send" : "Save"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={onNext}>
              Skip
            </Button>
            <span className="nd-actions-end nd-enter">{asksFor ? `Forms ask for ${asksFor}` : "Enter to save"}</span>
          </div>
        </form>
      )}
    </article>
  );
}


