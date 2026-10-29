// Needs you: the questions waiting on the owner, as its own page, grouped by application. The same question asked on
// several applications is one card (one saved answer resolves them all); the cards themselves are questions.tsx.
import { MessageCircleQuestion } from "lucide-react";
import { useCallback, useMemo } from "react";
import { useWorkspace } from "./context";
import { useDraft } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { groupQuestions, QuestionCard, type QuestionGroup } from "./questions";
import { ago, linkHost, plural, urlRole } from "./ui";
import { Button, Empty } from "./ui/index";
import "./styles/work.css";
import type { Job } from "./types";

type Bucket = { key: string; job?: Job; runId: string; since: string; cards: QuestionGroup[] };

const JOBS_PER_PAGE = 4;

export function NeedsView() {
  const { snapshot, go, openJob } = useWorkspace();
  const [skipped, setSkipped] = useDraft<string[]>("needs-skipped", []);
  const jobFor = useMemo(() => {
    const runs = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    return (runId: string) => jobs.get(runs.get(runId) || "");
  }, [snapshot]);
  const groups = useMemo(() => groupQuestions(snapshot.reviews, jobFor), [snapshot.reviews, jobFor]);
  const skippedSet = useMemo(() => new Set(skipped), [skipped]);
  const open = groups.filter((g) => !skippedSet.has(g.review.id));
  const parked = groups.filter((g) => skippedSet.has(g.review.id));

  // By application, the one that has waited longest first.
  const buckets = useMemo(() => {
    const by = new Map<string, Bucket>();
    for (const g of open) {
      const key = g.job?.id || "run:" + g.review.run_id;
      const since = g.review.created_at || "";
      const b = by.get(key);
      if (b) {
        b.cards.push(g);
        if (since && (!b.since || since < b.since)) b.since = since;
      } else by.set(key, { key, job: g.job, runId: g.review.run_id, since, cards: [g] });
    }
    return [...by.values()].sort((a, b) => (a.since || "~").localeCompare(b.since || "~"));
  }, [open]);
  const paging = usePagination(buckets, "", JOBS_PER_PAGE);

  // After an answer or a skip, the next question's field takes focus so a run of answers is typed without the mouse.
  const advance = useCallback(
    (id: string) => {
      const order = paging.items.flatMap((b) => b.cards.map((c) => c.review.id));
      const at = order.indexOf(id);
      const next = order[at + 1] ?? order[at - 1];
      if (!next) return;
      window.setTimeout(() => {
        document.querySelector<HTMLElement>(`[data-qid="${next}"] [data-answer]`)?.focus();
      }, 60);
    },
    [paging.items],
  );
  const skip = (id: string) => {
    advance(id);
    setSkipped((list) => (list.includes(id) ? list : [...list, id]));
  };

  if (!groups.length)
    return (
      <div className="ax-page needs-page">
        <Empty
          icon={MessageCircleQuestion}
          title="Nothing is waiting on you"
          hint="If an application is missing a required personal fact, it appears here with the page it came from. Site and control problems are repaired automatically and never turned into questions."
          action={
            <Button size="sm" onClick={() => go({ view: "batch" })}>
              Back to Overview
            </Button>
          }
        />
      </div>
    );

  const applications = new Set(groups.map((g) => g.job?.id || g.review.run_id)).size;
  return (
    <div className="ax-page needs-page">
      <p className="nq-lede">
        <strong>{plural(groups.length, "question")}</strong> across {plural(applications, "application")}. You are asked only for
        personal details your profile doesn't have; other applications keep going while these wait. A saved answer is reused
        wherever the same question comes up.
      </p>
      {buckets.length === 0 && (
        <p className="ax-note">Every open question is skipped for now. They are listed below.</p>
      )}
      <div className="nq-jobs">
        {paging.items.map((b) => (
          <section key={b.key} className="nq-job" aria-label={b.job?.company || linkHost(b.job?.url) || "Application"}>
            <header className="nq-job-head">
              <div className="nq-job-name">
                <strong>{b.job?.company || linkHost(b.job?.url) || "Employer"}</strong>
                <span>{b.job?.title || (b.job ? urlRole(b.job.url) : "Application")}</span>
              </div>
              <span className="ax-note nq-job-meta">
                {[b.job?.ats, b.since && `waiting since ${ago(b.since)}`, plural(b.cards.length, "question")].filter(Boolean).join(" · ")}
              </span>
              <Button size="sm" variant="ghost" onClick={() => b.job && openJob(b.job.id, b.runId)} disabled={!b.job}>
                Open application
              </Button>
            </header>
            {b.cards.map((g) => (
              <QuestionCard key={g.review.id} group={g} onSaved={advance} onSkip={skip} />
            ))}
          </section>
        ))}
      </div>
      <Pagination paging={paging} label="Question pages" sizes={false} />
      {parked.length > 0 && (
        <details className="nq-parked ax-fold">
          <summary>Skipped for now ({parked.length})</summary>
          <ul>
            {parked.map((g) => (
              <li key={g.review.id}>
                <span className="nq-parked-q">
                  <strong>{g.job?.company || linkHost(g.job?.url) || "Employer"}</strong> {g.review.display_question || g.review.question}
                </span>
                <Button size="sm" variant="ghost" onClick={() => setSkipped((list) => list.filter((x) => x !== g.review.id))}>
                  Answer now
                </Button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
