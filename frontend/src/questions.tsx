import { useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft } from "./hooks";
import type { Job, Review } from "./types";

export type QuestionGroup = { review: Review; job?: Job; others: Job[] };

// Identical questions (same key and options) across jobs share one answer:
// the backend resolves compatible sibling reviews when a reusable answer is saved.
export function groupQuestions(
  reviews: Review[],
  jobFor: (runId: string) => Job | undefined,
): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const review of reviews) {
    const key =
      review.key in { manual: 1, session: 1 }
        ? review.id
        : review.key + "\u0000" + JSON.stringify(review.options);
    const job = jobFor(review.run_id);
    const group = groups.get(key);
    if (group) {
      if (job) group.others.push(job);
    } else groups.set(key, { review, job, others: [] });
  }
  return [...groups.values()];
}

export function QuestionCard({
  group,
  compact,
}: {
  group: QuestionGroup;
  compact?: boolean;
}) {
  const { review: q, job, others } = group;
  const { refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [answer, setAnswer, clear] = useDraft("review:" + q.id, "");
  const [showPage, setShowPage] = useState(false);
  const [noShot, setNoShot] = useState(false);
  const note = q.key === "session" || q.key === "manual";
  const id = "q-" + q.id;
  return (
    <form
      className={"question" + (compact ? " compact" : "")}
      aria-labelledby={id}
      onSubmit={(e) => {
        e.preventDefault();
        run(
          "save",
          async () => {
            const result = await api<{ message?: string }>(
              "/reviews/" + q.id,
              "POST",
              { answer: answer.trim() },
            );
            clear();
            await refresh();
            return result;
          },
          (r) => r?.message || "Answer saved.",
        );
      }}
    >
      {job && !compact && (
        <button
          type="button"
          className="question-job link"
          onClick={() => openJob(job.id, q.run_id)}
        >
          <strong>{job.company || "Employer"}</strong>
          <span>{job.title}</span>
        </button>
      )}
      <p className="question-text" id={id}>
        {q.question}
      </p>
      {q.reason && <p className="question-why">{q.reason}</p>}
      {others.length > 0 && (
        <p className="question-also">
          Also waiting on this question:{" "}
          {others
            .slice(0, 3)
            .map((j) => j.company)
            .join(", ")}
          {others.length > 3 ? ` and ${others.length - 3} more` : ""}. A
          reusable answer is applied to them too.
        </p>
      )}
      <div className="question-answer">
        {q.options.length ? (
          <select
            aria-label={note ? "Resolution note" : "Your answer"}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            required
          >
            <option value="">Choose an answer…</option>
            {q.options.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        ) : (
          <textarea
            aria-label={note ? "Resolution note" : "Your answer"}
            rows={2}
            required
            maxLength={8000}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder={note ? "What you did to resolve it" : "Exact answer"}
          />
        )}
        <button
          className="btn primary sm"
          disabled={pending !== null || !answer.trim()}
        >
          {pending ? "Saving…" : "Save answer"}
        </button>
      </div>
      <div className="question-foot">
        <button
          type="button"
          className="link sm"
          aria-expanded={showPage}
          onClick={() => setShowPage(!showPage)}
        >
          {showPage ? "Hide page" : "Show the application page"}
        </button>
        <span className="muted sm">
          Saved to your profile, and this application continues once answered.
        </span>
      </div>
      {showPage &&
        (noShot ? (
          <p className="muted sm">
            No screenshot was captured for this attempt. Its activity is in the
            job details.
          </p>
        ) : (
          <a
            className="shot"
            href={`/api/runs/${q.run_id}/screenshot`}
            target="_blank"
            rel="noreferrer"
          >
            <img
              alt={`Application page where ${job?.company || "the employer"} asked this question`}
              loading="lazy"
              src={`/api/runs/${q.run_id}/screenshot`}
              onError={() => setNoShot(true)}
            />
          </a>
        ))}
    </form>
  );
}
