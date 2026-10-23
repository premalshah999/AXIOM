import { useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft } from "./hooks";
import type { Job, Review } from "./types";

export type QuestionGroup = { review: Review; job?: Job; others: Job[] };

// Identical questions (same key, options and row) across jobs share one answer:
// the backend resolves compatible sibling reviews when a reusable answer is saved.
// Two history rows' "End Date" share a key but are different questions.
export function groupQuestions(
  reviews: Review[],
  jobFor: (runId: string) => Job | undefined,
): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const review of reviews) {
    const key =
      review.key in { manual: 1, session: 1 }
        ? review.id
        : [
            review.key,
            JSON.stringify(review.options),
            review.display_question || review.question,
          ].join("\u0000");
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
  const [file, setFile] = useState<File | null>(null);
  const [showPage, setShowPage] = useState(false);
  const [shot, setShot] = useState<"question" | "run" | "none">("question");
  const note = q.key === "session" || q.key === "manual";
  const kind = q.kind || (q.options.length ? "choice" : "text");
  const id = "q-" + q.id;
  const listId = "q-options-" + q.id;
  const ready = kind === "upload" ? !!file : !!answer.trim();
  return (
    <form
      className={"question" + (compact ? " compact" : "")}
      aria-labelledby={id}
      onSubmit={(e) => {
        e.preventDefault();
        run(
          "save",
          async () => {
            let result: { message?: string };
            if (kind === "upload" && file) {
              const body = new FormData();
              body.append("file", file);
              result = await api<{ message?: string }>(
                "/reviews/" + q.id + "/file",
                "POST",
                body,
              );
              setFile(null);
            } else {
              result = await api<{ message?: string }>(
                "/reviews/" + q.id,
                "POST",
                { answer: answer.trim() },
              );
              clear();
            }
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
        {q.display_question || q.question}
      </p>
      {(q.why || q.reason) && (
        <p className="question-why">{q.why || q.reason}</p>
      )}
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
        {kind === "upload" ? (
          <input
            type="file"
            aria-label="File to attach"
            accept={q.accept || ".pdf,.doc,.docx,.txt,.rtf,.odt,.png,.jpg,.jpeg"}
            required
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        ) : kind === "choice" && q.allow_other ? (
          <>
            <input
              aria-label="Your answer"
              list={listId}
              required
              maxLength={8000}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="Choose or type your answer"
            />
            <datalist id={listId}>
              {q.options.map((x) => (
                <option key={x} value={x} />
              ))}
            </datalist>
          </>
        ) : kind === "choice" ? (
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
          disabled={pending !== null || !ready}
        >
          {pending ? "Saving…" : kind === "upload" ? "Attach file" : "Save answer"}
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
          {kind === "upload"
            ? "Kept with your documents, and attached when another application asks for it."
            : "Saved to your profile, and this application continues once answered."}
        </span>
      </div>
      {showPage &&
        (shot === "none" ? (
          <p className="muted sm">
            No screenshot was captured for this attempt. Its activity is in the
            job details.
          </p>
        ) : (
          <a
            className="shot"
            href={
              shot === "question"
                ? `/api/reviews/${q.id}/screenshot`
                : `/api/runs/${q.run_id}/screenshot`
            }
            target="_blank"
            rel="noreferrer"
          >
            <img
              alt={`Application page where ${job?.company || "the employer"} asked this question`}
              loading="lazy"
              src={
                shot === "question"
                  ? `/api/reviews/${q.id}/screenshot`
                  : `/api/runs/${q.run_id}/screenshot`
              }
              onError={() => setShot(shot === "question" ? "run" : "none")}
            />
          </a>
        ))}
    </form>
  );
}
