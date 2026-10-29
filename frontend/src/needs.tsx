// Needs you: the questions waiting on the owner, as its own page (it was a block on Batch).
// The question cards themselves are questions.tsx; this page only lists and pages them.
import { MessageCircleQuestion } from "lucide-react";
import { useMemo } from "react";
import { useWorkspace } from "./context";
import { Pagination, usePagination } from "./pagination";
import { groupQuestions, QuestionCard } from "./questions";
import { plural } from "./ui";
import { Button, Empty } from "./ui/index";

export function NeedsView() {
  const { snapshot, go } = useWorkspace();
  const jobFor = useMemo(() => {
    const runs = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    return (runId: string) => jobs.get(runs.get(runId) || "");
  }, [snapshot]);
  const groups = groupQuestions(snapshot.reviews, jobFor);
  const paging = usePagination(groups, "", 3);
  return (
    <div className="page needs-page">
      {groups.length ? (
        <>
          <p className="page-sub">
            {plural(groups.length, "question")} waiting. You are asked only for personal details your profile doesn't have; other
            applications keep going while these wait.
          </p>
          <div className="needs-list">
            {paging.items.map((g) => (
              <QuestionCard key={g.review.id} group={g} />
            ))}
          </div>
          <Pagination paging={paging} label="Question pages" sizes={false} />
        </>
      ) : (
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
      )}
    </div>
  );
}
