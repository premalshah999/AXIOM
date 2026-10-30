import { useId, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft } from "./hooks";
import type { Job, Review } from "./types";
import { ago, linkHost } from "./ui";
import { Button } from "./ui/index";
import "./styles/work.css";

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

// How many questions are waiting, as the Needs you page counts them (the same question on several applications is one).
export const countQuestions = (reviews: Review[]) => groupQuestions(reviews, () => undefined).length;

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

// Date-shaped text fields: the server's requirement line says which shape the form expects ("Type MM/DD/YYYY").
type Shape = { input: "date" | "month" | "text" | "email" | "url" | "paragraph"; format?: string; placeholder?: string; max?: number };
function shapeOf(requirement = ""): Shape {
  const max = Number(/at most (\d+) characters/.exec(requirement)?.[1]) || undefined;
  const typed = /^Type (MM\/DD\/YYYY|DD\/MM\/YYYY|YYYY-MM-DD|MM\/YYYY|YYYY|MM|a number|an email address|a web link)/.exec(requirement)?.[1];
  if (typed === "MM/DD/YYYY" || typed === "DD/MM/YYYY" || typed === "YYYY-MM-DD") return { input: "date", format: typed };
  if (typed === "MM/YYYY") return { input: "month", format: typed };
  if (typed === "YYYY" || typed === "MM") return { input: "text", placeholder: typed, max: typed.length };
  if (typed === "a number") return { input: "text", placeholder: "A number" };
  if (typed === "an email address") return { input: "email", placeholder: "name@example.com" };
  if (typed === "a web link") return { input: "url", placeholder: "https://" };
  if (/^Paragraph/.test(requirement)) return { input: "paragraph", max };
  return { input: "text", max };
}

// A browser date input speaks YYYY-MM-DD (and YYYY-MM for months); the form wants the shape its own field shows.
function toInput(answer: string, format?: string) {
  if (!answer) return "";
  let m: RegExpExecArray | null;
  if (format === "MM/DD/YYYY" && (m = /^(\d\d)\/(\d\d)\/(\d{4})$/.exec(answer))) return `${m[3]}-${m[1]}-${m[2]}`;
  if (format === "DD/MM/YYYY" && (m = /^(\d\d)\/(\d\d)\/(\d{4})$/.exec(answer))) return `${m[3]}-${m[2]}-${m[1]}`;
  if (format === "MM/YYYY" && (m = /^(\d\d)\/(\d{4})$/.exec(answer))) return `${m[2]}-${m[1]}`;
  return answer;
}
function fromInput(value: string, format?: string) {
  let m: RegExpExecArray | null;
  if (!value) return "";
  if (format === "MM/DD/YYYY" && (m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(value))) return `${m[2]}/${m[3]}/${m[1]}`;
  if (format === "DD/MM/YYYY" && (m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(value))) return `${m[3]}/${m[2]}/${m[1]}`;
  if (format === "MM/YYYY" && (m = /^(\d{4})-(\d\d)$/.exec(value))) return `${m[2]}/${m[1]}`;
  return value;
}

/**
 * One question the owner can answer: the complete question, what kind of answer it takes, the control that fits
 * (options for yes/no and short lists, a select for long ones, a date field for dates, a file for uploads) and
 * Save / Skip. Why it is asked and the application page it came from are one click away. Enter saves a one-line
 * answer; Ctrl or ⌘ Enter saves a paragraph. Who asked it is the heading above it on Needs you, and the drawer's own
 * title.
 */
export function QuestionCard({
  group,
  compact,
  onSaved,
  onSkip,
}: {
  group: QuestionGroup;
  /** In the job drawer: the one action on screen (a primary Save), no Skip. */
  compact?: boolean;
  onSaved?: (reviewId: string) => void;
  onSkip?: (reviewId: string) => void;
}) {
  const { review: q, job, others } = group;
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [answer, setAnswer, clear] = useDraft("review:" + q.id, "");
  const [file, setFile] = useState<File | null>(null);
  const [showPage, setShowPage] = useState(false);
  const [showWhy, setShowWhy] = useState(false);
  const [shot, setShot] = useState<"question" | "run" | "none">("question");
  const note = q.key === "session" || q.key === "manual";
  const kind = q.kind || (q.options.length ? "choice" : "text");
  const uid = useId();
  const id = "q-" + q.id;
  const listId = "q-options-" + q.id;
  const ready = kind === "upload" ? !!file : !!answer.trim();
  const text = q.display_question || q.question;
  const why = q.why || q.reason;
  const yesNo = kind === "choice" && !q.multiple && q.options.length === 2 && norm(q.options[0]!) + "|" + norm(q.options[1]!) in { "yes|no": 1, "no|yes": 1 };
  const buttons = kind === "choice" && !q.multiple && !q.allow_other && (yesNo || (q.options.length <= 4 && q.options.every((o) => o.length <= 48)));
  const shape = shapeOf(q.requirement);

  const save = () =>
    run(
      "save",
      async () => {
        let result: { message?: string };
        if (kind === "upload" && file) {
          const body = new FormData();
          body.append("file", file);
          result = await api<{ message?: string }>("/reviews/" + q.id + "/file", "POST", body);
          setFile(null);
        } else {
          result = await api<{ message?: string }>("/reviews/" + q.id, "POST", { answer: answer.trim() });
          clear();
        }
        await refresh();
        onSaved?.(q.id);
        return result;
      },
      (r) => r?.message || "Answer saved.",
    );

  const label = note ? "Resolution note" : "Your answer";
  const control =
    kind === "upload" ? (
      <input
        data-answer
        className="ax-control"
        type="file"
        aria-label="File to attach"
        accept={q.accept || ".pdf,.doc,.docx,.txt,.rtf,.odt,.png,.jpg,.jpeg"}
        required
        onChange={(e) => setFile(e.target.files?.[0] || null)}
      />
    ) : kind === "choice" && q.multiple ? (
      <fieldset className="nq-opts" aria-label="Your answers">
        {q.options.map((x, i) => {
          const picked = answer.split("\n").filter(Boolean);
          return (
            <label key={x} className="ax-check">
              <input
                data-answer={i === 0 ? "" : undefined}
                type="checkbox"
                checked={picked.includes(x)}
                onChange={(e) => setAnswer((e.target.checked ? [...picked, x] : picked.filter((p) => p !== x)).join("\n"))}
              />
              <span>{x}</span>
            </label>
          );
        })}
      </fieldset>
    ) : kind === "choice" && q.allow_other ? (
      <>
        <input
          data-answer
          className="ax-control"
          aria-label={label}
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
    ) : buttons ? (
      <div className="nq-opts" role="radiogroup" aria-label={label}>
        {q.options.map((x, i) => (
          <label key={x} className="ax-check">
            <input
              data-answer={i === 0 ? "" : undefined}
              type="radio"
              name={uid}
              value={x}
              checked={answer === x}
              onChange={() => setAnswer(x)}
            />
            <span>{x}</span>
          </label>
        ))}
      </div>
    ) : kind === "choice" ? (
      <select data-answer className="ax-control" aria-label={label} value={answer} onChange={(e) => setAnswer(e.target.value)} required>
        <option value="">Choose an answer…</option>
        {q.options.map((x) => (
          <option key={x} value={x}>
            {x}
          </option>
        ))}
      </select>
    ) : shape.input === "date" || shape.input === "month" ? (
      <input
        data-answer
        className="ax-control"
        type={shape.input}
        aria-label={`${label} (${shape.format})`}
        required
        value={toInput(answer, shape.format)}
        onChange={(e) => setAnswer(fromInput(e.target.value, shape.format))}
      />
    ) : shape.input === "paragraph" || note ? (
      <textarea
        data-answer
        className="ax-control"
        aria-label={label}
        rows={3}
        required
        maxLength={Math.min(8000, shape.max || 8000)}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && ready && pending === null) {
            e.preventDefault();
            save();
          }
        }}
        placeholder={note ? "What you did to resolve it" : "Your answer"}
      />
    ) : (
      <input
        data-answer
        className="ax-control"
        type={shape.input === "email" || shape.input === "url" ? shape.input : "text"}
        aria-label={label}
        required
        maxLength={Math.min(8000, shape.max || 8000)}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder={shape.placeholder || "Your answer"}
      />
    );

  return (
    <form
      className="dr-stack"
      aria-labelledby={id}
      data-qid={q.id}
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) save();
      }}
    >
      <div>
        <p className="nq-q" id={id}>
          {text}
        </p>
        {(q.requirement || others.length > 0) && (
          <p className="ax-note">
            {q.requirement}
            {q.requirement && others.length > 0 && " · "}
            {others.length > 0 &&
              `Also waiting on this: ${others.slice(0, 3).map((j) => j.company || linkHost(j.url) || "another application").join(", ")}${others.length > 3 ? ` and ${others.length - 3} more` : ""}. A reusable answer is applied to them too.`}
          </p>
        )}
      </div>
      <div className="nq-answer">{control}</div>
      <div className="ax-toolbar">
        <Button type="submit" variant={compact ? "primary" : "secondary"} size="sm" disabled={!ready} loading={pending === "save"}>
          {kind === "upload" ? "Attach file" : "Save answer"}
        </Button>
        {!compact && onSkip && (
          <Button size="sm" variant="ghost" disabled={pending !== null} onClick={() => onSkip(q.id)}>
            Skip for now
          </Button>
        )}
        {why && (
          <button type="button" className="ax-link" aria-expanded={showWhy} onClick={() => setShowWhy(!showWhy)}>
            {showWhy ? "Hide why" : "Why am I asked?"}
          </button>
        )}
        <button type="button" className="ax-link" aria-expanded={showPage} onClick={() => setShowPage(!showPage)}>
          {showPage ? "Hide the application page" : "Show the application page"}
        </button>
      </div>
      {showWhy && (
        <p className="ax-note">
          {why}{" "}
          {kind === "upload"
            ? "It is kept with your documents and attached when another application asks for it."
            : "The answer is saved to your profile, and this application continues once answered."}
          {q.created_at && ` Asked ${ago(q.created_at)}.`}
        </p>
      )}
      {showPage &&
        (shot === "none" ? (
          <p className="ax-note">No screenshot was captured for this attempt. Its activity is in the job details.</p>
        ) : (
          <a
            className="ax-shot"
            href={shot === "question" ? `/api/reviews/${q.id}/screenshot` : `/api/runs/${q.run_id}/screenshot`}
            target="_blank"
            rel="noreferrer"
          >
            <img
              alt={`Application page where ${job?.company || "the employer"} asked this question`}
              loading="lazy"
              src={shot === "question" ? `/api/reviews/${q.id}/screenshot` : `/api/runs/${q.run_id}/screenshot`}
              onError={() => setShot(shot === "question" ? "run" : "none")}
            />
          </a>
        ))}
    </form>
  );
}
