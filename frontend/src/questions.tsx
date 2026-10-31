import { FileText, Video } from "lucide-react";
import { useId, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft } from "./hooks";
import type { Job, Review } from "./types";
import { ago, employerOf, linkHost, plural } from "./format";
import { Button, ConfirmButton, Dropzone, Listbox } from "./ui/index";
import { RowMenu } from "./worktools";
import "./styles/work.css";
import "./styles/needs.css";

/** One waiting question and everything that answering it reaches: `reviews` are all of its rows, `others` the other jobs asking. */
export type QuestionGroup = { review: Review; reviews: Review[]; job?: Job; others: Job[] };

// What the server adds to a question beyond the Review type (owner_questions.present). A server without the "no answer completes
// this" marker simply leaves those two fields out, and the question reads as an ordinary one.
export type Asked = Omit<Review, "kind"> & {
  kind?: string;
  input_kind?: string;
  /** A plain reason when no answer from the profile or the owner's words completes this field. */
  cannot_automate?: string;
  context?: {
    type?: string;
    section?: string;
    group?: string;
    caption?: string;
    hint?: string;
    history_kind?: string;
    history_identity?: { company?: string; title?: string; school?: string };
  } | null;
};
export const asked = (review: Review) => review as Asked;

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
export const BLIND_TITLE = "A required field AXIOM couldn't read";
const SKIP_KEY = /mac/i.test(navigator.platform) ? "\u2325S" : "Alt S";

// Wording that says the form's own question was never read (the server's owner_questions.UNREAD). Questions like this were put to
// the owner before that rule existed and may still be open; a blank is the same thing.
const UNREAD =
  /\bfield[-_ ]?\d+\b|\bno (?:visible |readable |stated |real )?(?:label|question text|prompt|subject)\b|\bun-?label+ed\b|\bmis-?label+ed\b|\b(?:text|wording)\s+(?:wasn'?t|was not|isn'?t|is not|couldn'?t|could not|was never|is unavailable|is missing)\b|\b(?:text|wording) (?:of|for) (?:each|the) question\b|['"\u201c](?:what|where|when|who|why|how)\?['"\u201d]/i;
export const isBlind = (review: Review) => {
  const text = review.display_question || review.question || "";
  return !norm(text) || UNREAD.test(text);
};

/** Why no typed answer completes this question, or null. `hard`: nothing the owner could type or send fits. */
export function blockOf(review: Review) {
  const q = asked(review);
  const hard = q.kind === "cannot_automate";
  if (!hard && !q.cannot_automate) return null;
  return { hard, reason: q.cannot_automate || "This application needs something the app can't provide." };
}

// Identical questions across jobs share one answer. A key is the form's field identity, and forms worded alike have different keys
// (repair questions never share one), so the wording and options decide. Two history rows' "End Date" read differently because
// the row is part of the wording. A question nobody could read is never "the same" as another.
export function groupQuestions(
  reviews: Review[],
  jobFor: (runId: string) => Job | undefined,
): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const review of reviews) {
    const key =
      review.key in { manual: 1, session: 1 } || isBlind(review)
        ? review.id
        : [norm(review.display_question || review.question), JSON.stringify(review.options)].join("\u0000");
    const job = jobFor(review.run_id);
    const group = groups.get(key);
    if (group) {
      group.reviews.push(review);
      if (job && job.id !== group.job?.id && !group.others.some((o) => o.id === job.id)) group.others.push(job);
    } else groups.set(key, { review, reviews: [review], job, others: [] });
  }
  return [...groups.values()];
}

// How many questions are waiting, as the Needs you page counts them (the same question on several applications is one).
export const countQuestions = (reviews: Review[]) => groupQuestions(reviews, () => undefined).length;

/** The question itself, and where on the form it sits ("Work history: Acme · Intern" over "Begin Date"). */
export function questionParts(review: Review) {
  const crumbs = (review.display_question || review.question || "")
    .split(" \u203a ")
    .map((c) => c.trim())
    .filter((c) => c && !/^required field$/i.test(c));
  const text = crumbs.pop() || "";
  const above = crumbs[crumbs.length - 1];
  // "None of the Above" under the sentence that asks: that sentence is the question.
  if (above && /[:?]$/.test(above)) return { place: crumbs.slice(0, -1).join(" \u203a "), text: `${above} ${text}` };
  return { place: crumbs.join(" \u203a "), text };
}

/** Who is asking, in a few words: "Acme, 2 jobs", "Acme and 1 other". */
export function whoOf(jobs: (Job | undefined)[]) {
  const known = jobs.filter((j): j is Job => !!j);
  const names = [...new Set(known.map((j) => employerOf(j.company, j.url)))];
  if (!names.length) return "Employer";
  if (names.length === 1) return known.length > 1 ? `${names[0]}, ${known.length} jobs` : names[0];
  return `${names[0]} and ${plural(names.length - 1, "other")}`;
}

// The server answers a question's twins itself when it can, so a later send can find its row already answered: that's a success.
const ALREADY = /already answered|no longer exists/i;

/** Sends one answer to every row of a group, one at a time, and says how it went. */
export async function deliver(reviews: Review[], post: (review: Review) => Promise<{ message?: string }>) {
  let message: string | undefined;
  let sent = 0;
  let failure: Error | null = null;
  for (const review of reviews) {
    try {
      message = (await post(review)).message ?? message;
      sent++;
    } catch (e) {
      if (ALREADY.test((e as Error).message)) sent++;
      else failure ??= e as Error;
    }
  }
  if (failure) throw sent ? new Error(`Sent to ${sent} of ${reviews.length}. ${failure.message}`) : failure;
  return { message, sent };
}
export const postAnswer = (review: Review, answer: string) => api<{ message?: string }>("/reviews/" + review.id, "POST", { answer });

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

// The server's requirement line says what the form's field takes; most of it the control already shows. A list of file types goes
// into words instead: nobody should read "application/vnd.oasis.opendocument.text".
function plainRequirement(requirement = "") {
  const first = requirement.split(" · ")[0]!.trim();
  const limit = /at most (\d+) characters/.exec(first)?.[1];
  if (limit) return `At most ${limit} characters`;
  if (/^Paragraph/.test(first)) return "A few sentences";
  if (/^Pick all/.test(first)) return "Pick all that apply";
  if (/^Type (a number|YYYY|MM\b)/.test(first)) return first;
  return "";
}
function fileWords(accept = "") {
  const rules = accept.toLowerCase();
  const words = [
    /pdf/.test(rules) && "PDF",
    /\.docx?|msword|wordprocessing/.test(rules) && "Word",
    /\.pptx?|presentation/.test(rules) && "PowerPoint",
    /\.xlsx?|spreadsheet/.test(rules) && "Excel",
    /\.txt|text\/plain/.test(rules) && "text",
    /\.jpe?g|\.png|\.gif|image\//.test(rules) && "an image",
    /\.zip/.test(rules) && "a zip",
  ].filter(Boolean);
  return words.length ? words.join(", ").replace(/, ([^,]*)$/, " or $1") + "." : "PDF, Word, text or an image.";
}

/**
 * One question: the complete wording, the control that fits (options for yes/no and short lists, a menu for long ones, a date field
 * for dates, a dropzone for uploads), and sending. Who asked is the heading above it. A question no answer completes says so and
 * offers to drop the job; one whose wording was never read shows the application page instead of a blank.
 *
 * `compact` is the job drawer and the Overview's dialog: the one action on screen, with why and the page a click away. Otherwise it's
 * the Needs you inbox: Enter sends and moves on (`onSending` fires at once so the next question is already there; `onFailed` brings
 * this one back), with Skip and "Not applicable to me".
 */
export function QuestionCard({
  group,
  compact,
  onSaved,
  onSkip,
  onSending,
  onFailed,
  onDropped,
  suggestion,
}: {
  group: QuestionGroup;
  compact?: boolean;
  onSaved?: (reviewId: string, message?: string) => void;
  onSkip?: (reviewId: string) => void;
  onSending?: (reviewId: string) => void;
  onFailed?: (reviewId: string) => void;
  onDropped?: (reviewId: string) => void;
  /** Something already saved that could answer this: a button fills the box, and the owner still reads it and sends it. */
  suggestion?: { label: string; value: string };
}) {
  const { review, job, others, reviews } = group;
  const q = asked(review);
  const { refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [answer, setAnswer, clear] = useDraft("review:" + q.id, "");
  const [file, setFile] = useState<File | null>(null);
  const blind = isBlind(review);
  const block = blockOf(review);
  const [answering, setAnswering] = useState(false);
  const [showPage, setShowPage] = useState(blind);
  const [showWhy, setShowWhy] = useState(false);
  const [shot, setShot] = useState<"question" | "run" | "none">("question");
  const note = q.key === "session" || q.key === "manual";
  const kind = block?.hard ? q.input_kind || (q.options.length ? "choice" : "text") : q.kind || (q.options.length ? "choice" : "text");
  const uid = useId();
  const id = "q-" + q.id;
  const listId = "q-options-" + q.id;
  const ready = kind === "upload" ? !!file : !!answer.trim();
  const whole = q.display_question || q.question;
  const { place, text } = questionParts(review);
  const shape = shapeOf(q.requirement);
  const yesNo = kind === "choice" && !q.multiple && q.options.length === 2 && norm(q.options[0]!) + "|" + norm(q.options[1]!) in { "yes|no": 1, "no|yes": 1 };
  const buttons = kind === "choice" && !q.multiple && !q.allow_other && (yesNo || (q.options.length <= 8 && q.options.every((o) => o.length <= 48)) || (q.options.length <= 4 && q.options.every((o) => o.length <= 90)));
  const reach = [job, ...others].filter((j): j is Job => !!j);
  const count = reach.length;

  const send = (post: (r: Review) => Promise<{ message?: string }>, after?: () => void) => {
    onSending?.(q.id);
    return run(
      "save",
      async () => {
        try {
          const result = await deliver(reviews, post);
          after?.();
          await refresh();
          onSaved?.(q.id, result.message);
          return result;
        } catch (e) {
          void refresh();
          throw e;
        }
      },
      // The inbox shows progress and the next question; a toast per answer would only pile up.
      (r) => (compact ? r?.message || "Answer saved." : undefined),
    ).then((r) => {
      if (!r) onFailed?.(q.id);
    });
  };
  const save = () =>
    kind === "upload" && file
      ? send(
          (r) => {
            const body = new FormData();
            body.append("file", file);
            return api<{ message?: string }>("/reviews/" + r.id + "/file", "POST", body);
          },
          () => setFile(null),
        )
      : send((r) => postAnswer(r, answer.trim()), clear);

  // "Not applicable" is only an answer where the form can take those words: free text, or a choice that offers them.
  const notApplicable =
    note || compact
      ? undefined
      : kind === "text" && !shape.placeholder && (shape.input === "text" || shape.input === "paragraph") && (shape.max ?? 8) >= 3
        ? "N/A"
        : kind === "choice"
          ? q.options.find((o) => /^(n\/?a|not applicable|does not apply)\b/i.test(o.trim()))
          : undefined;

  const drop = () =>
    run(
      "drop",
      async () => {
        for (const j of reach) await api("/jobs/" + j.id, "DELETE");
        await refresh();
        onDropped?.(q.id);
      },
      count > 1 ? `Dropped ${plural(count, "job")}.` : "Job dropped.",
    );
  const dropLabel = count > 1 ? `Drop ${plural(count, "job")}` : "Drop this job";
  const dropNote = "It leaves your lists. Nothing is sent to the employer.";

  const focusSend = () => requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-qid="${q.id}"] button[type="submit"]`)?.focus());
  const chooseFile = (chosen: File | null) => {
    setFile(chosen);
    if (chosen) focusSend();
  };
  const label = note ? "Resolution note" : "Your answer";
  const offered = q.options.length <= 8 && q.options.every((o) => o.length <= 48);
  const control =
    kind === "upload" ? (
      <Dropzone label="File to attach" accept={q.accept || ".pdf,.doc,.docx,.txt,.rtf,.odt,.png,.jpg,.jpeg"} file={file} onFile={chooseFile} hint={fileWords(q.accept)} />
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
          list={offered ? undefined : listId}
          required
          maxLength={8000}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          placeholder="Choose or type your answer"
        />
        {offered ? (
          <div className="nd-offers">
            {q.options.map((x) => (
              <Button key={x} size="sm" variant="ghost" aria-pressed={answer === x} onClick={() => setAnswer(x)}>
                {x}
              </Button>
            ))}
          </div>
        ) : (
          <datalist id={listId}>
            {q.options.map((x) => (
              <option key={x} value={x} />
            ))}
          </datalist>
        )}
      </>
    ) : buttons ? (
      <div className={"nq-opts" + (q.options.every((o) => o.length <= 18) ? " is-short" : "")} role="radiogroup" aria-label={label}>
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
      <Listbox
        label={label}
        value={answer}
        options={[{ value: "", label: "Choose an answer" }, ...q.options.map((x) => ({ value: x, label: x }))]}
        onChange={(value) => {
          setAnswer(value);
          // The menu hands focus back to itself, where Enter would only reopen it. Enter should send.
          if (value) focusSend();
        }}
      />
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
        rows={4}
        required
        maxLength={Math.min(8000, shape.max || 8000)}
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter" || e.shiftKey || !ready || pending !== null) return;
          // In the inbox Enter sends, as in a chat, and Shift Enter starts a new line. A phone's Enter is its only way to a new line.
          const sends = e.metaKey || e.ctrlKey || (!compact && !matchMedia("(pointer: coarse)").matches);
          if (sends) {
            e.preventDefault();
            void save();
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

  const picture = showPage && (
    <div className="nd-page">
      {shot === "none" ? (
        <p className="ax-note">
          No picture of this page was captured.
          {job?.url && (
            <>
              {" "}
              <a className="ax-link" href={job.url} target="_blank" rel="noreferrer">
                Open the posting on {linkHost(job.url) || "the employer's site"}
              </a>{" "}
              to see it.
            </>
          )}
        </p>
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
      )}
    </div>
  );
  const pageContext = [q.context?.section, q.context?.group, q.context?.caption, q.context?.hint].filter(Boolean).join(" · ");
  const BlockIcon = /video|record/i.test(whole) ? Video : FileText;
  const blocked = block && (
    <div className={"nd-block" + (block.hard ? " is-hard" : "")}>
      <BlockIcon size={18} aria-hidden="true" />
      <p>{block.reason}</p>
    </div>
  );
  const hideControl = !!block?.hard && !answering;
  const hint = plainRequirement(q.requirement);

  if (compact)
    return (
      <form
        className="dr-stack"
        aria-labelledby={id}
        data-qid={q.id}
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) void save();
        }}
      >
        <div>
          <p className={"nq-q" + (whole.length > 140 ? " is-long" : "")} id={id}>
            {whole}
          </p>
          {(hint || others.length > 0) && (
            <p className="ax-note">
              {hint}
              {hint && others.length > 0 && " · "}
              {others.length > 0 &&
                `Also waiting on this: ${others.slice(0, 3).map((j) => j.company || linkHost(j.url) || "another application").join(", ")}${others.length > 3 ? ` and ${others.length - 3} more` : ""}. Your answer goes to them too.`}
            </p>
          )}
        </div>
        {blocked}
        {!hideControl && (
          <div className="nq-ctl">
            <div className="nq-answer">{control}</div>
            <Button type="submit" variant="primary" disabled={!ready} loading={pending === "save"} aria-label={kind === "upload" ? "Attach file" : "Save answer"}>
              {kind === "upload" ? "Attach" : "Save"}
            </Button>
          </div>
        )}
        <div className="ax-toolbar">
          {block && reach.length > 0 && <ConfirmButton label={dropLabel} confirm="Yes, drop it" note={dropNote} pending={pending === "drop"} onConfirm={drop} />}
          {hideControl && (
            <button type="button" className="ax-link" onClick={() => setAnswering(true)}>
              Answer anyway
            </button>
          )}
          {q.why && (
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
            {q.why}{" "}
            {kind === "upload"
              ? "It is kept with your documents and attached when another application asks for it."
              : "The answer is saved to your profile, and this application continues once answered."}
            {q.created_at && ` Asked ${ago(q.created_at)}.`}
          </p>
        )}
        {picture}
      </form>
    );

  return (
    <form
      className="nd-card"
      aria-labelledby={id}
      data-qid={q.id}
      onSubmit={(e) => {
        e.preventDefault();
        if (ready && !hideControl) void save();
      }}
    >
      <header className="nd-card-head">
        {place && !blind && <p className="nd-place">{place}</p>}
        <h2 className={"nd-q" + (!blind && text.length > 90 ? " is-long" : "")} id={id}>
          {blind ? BLIND_TITLE : text}
        </h2>
        {blind ? (
          <>
            <p className="nd-meta">
              The form's wording didn't come through, so the page is below. Answer from what you see there, or drop the job.
              {pageContext && ` On the page: ${pageContext}.`}
            </p>
            {!!norm(whole) && (
              <p className="nd-meta">
                AXIOM's note: <q>{whole}</q>
              </p>
            )}
          </>
        ) : (
          <p className="nd-meta">
            {!block && q.why && `${q.why} `}
            {q.created_at && `Asked ${ago(q.created_at)}. `}
            <button type="button" className="ax-link" aria-expanded={showPage} onClick={() => setShowPage(!showPage)}>
              {showPage ? "Hide the application page" : "See the application page"}
            </button>
          </p>
        )}
      </header>
      {blocked}
      {!hideControl && (
        <div className="nd-control nq-answer">
          {control}
          {hint && <p className="ax-hint">{hint}</p>}
          {suggestion && kind === "text" && !answer && (
            <div>
              <Button size="sm" onClick={() => setAnswer(suggestion.value)}>
                {suggestion.label}
              </Button>
            </div>
          )}
        </div>
      )}
      <div className="nd-actions">
        {hideControl ? (
          reach.length > 0 && <ConfirmButton size="md" label={dropLabel} confirm="Yes, drop it" note={dropNote} pending={pending === "drop"} onConfirm={drop} />
        ) : (
          <Button type="submit" variant="primary" disabled={!ready} loading={pending === "save"}>
            {kind === "upload" ? "Attach" : "Send"}
            {count > 1 && ` to ${plural(count, "application")}`}
          </Button>
        )}
        {onSkip && (
          <Button variant="ghost" disabled={pending !== null} onClick={() => onSkip(q.id)}>
            Skip
          </Button>
        )}
        {notApplicable && !hideControl && (
          <Button variant="ghost" disabled={pending !== null} onClick={() => void send((r) => postAnswer(r, notApplicable), clear)}>
            Not applicable to me
          </Button>
        )}
        {hideControl && (
          <Button variant="ghost" onClick={() => setAnswering(true)}>
            I have a {kind === "upload" ? "file" : "link"} to send
          </Button>
        )}
        <span className="nd-actions-end">
          {!block && !hideControl && kind !== "upload" && (
            <span className="nd-enter" aria-hidden="true">
              {shape.input === "paragraph" || note ? "Enter sends, Shift Enter for a new line" : "Enter to send"}
              {onSkip && ` · ${SKIP_KEY} to skip`}
            </span>
          )}
          {block && !hideControl && reach.length > 0 && <ConfirmButton size="md" label={dropLabel} confirm="Yes, drop it" note={dropNote} pending={pending === "drop"} onConfirm={drop} />}
          {!block && (
            <RowMenu
              label="More about this question"
              actions={[
                ...(reach.length === 1 ? [{ id: "open", label: "Open job", onSelect: () => openJob(reach[0]!.id, q.run_id) }] : []),
                ...(reach.length ? [{ id: "drop", label: dropLabel, danger: true, confirm: `${dropLabel}? ${dropNote}`, confirmLabel: "Drop", onSelect: () => void drop() }] : []),
              ]}
            />
          )}
        </span>
      </div>
      {picture}
    </form>
  );
}
