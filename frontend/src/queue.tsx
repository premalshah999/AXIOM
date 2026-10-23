import { useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useNow, usePoll } from "./hooks";
import { ago, ErrorLine, linkHost, plural, StatePill } from "./ui";

type QueueItem = {
  job_id: string;
  title: string;
  company: string;
  url: string;
  ats: string;
};
type ActiveItem = QueueItem & { run_id: string; state: string; since: string };
type WaitingItem = QueueItem & {
  requested_at: string;
  code: string;
  reason: string;
  not_before: string;
};
type QueueView = { active: ActiveItem[]; waiting: WaitingItem[] };
type LinkResult = {
  url: string;
  job_id?: string;
  state: string;
  message?: string;
  created?: boolean;
};

// Every http(s) link in what the owner pasted: one per line, comma- or space-separated, or inside other text.
const LINKS = /https?:\/\/[^\s,<>"']+/gi;

export function linksIn(text: string) {
  return [...new Set((text.match(LINKS) || []).map((u) => u.replace(/[.,;)]+$/, "")))];
}

function name(item: QueueItem) {
  if (item.title) return item.company ? `${item.company} · ${item.title}` : item.title;
  return linkHost(item.url) || item.url;
}

// Owner, 2026-10-22: queued applications in one place, and a pasted link goes straight into the queue.
export function QueuePanel() {
  const { refresh, openJob } = useWorkspace();
  const queue = usePoll<QueueView>((signal) => api("/queue", "GET", undefined, signal), 5000);
  const action = useAction();
  const now = useNow(30000);
  const [text, setText] = useState("");
  const [results, setResults] = useState<LinkResult[]>([]);
  const links = linksIn(text);
  const active = queue.data?.active || [];
  const waiting = queue.data?.waiting || [];

  const submit = async () => {
    if (!links.length) return;
    const answer = await action.run(
      "links",
      () => api<{ results: LinkResult[] }>("/queue/links", "POST", { urls: links.slice(0, 25) }),
      (r) => {
        const queued = r.results.filter((x) => x.state !== "refused").length;
        return queued ? `${plural(queued, "link")} added to the queue` : undefined;
      },
    );
    if (answer) {
      setResults(answer.results);
      setText("");
      await Promise.all([queue.refresh(), refresh()]);
    }
  };

  return (
    <section className="block queue" aria-labelledby="queue-title">
      <header className="block-head">
        <h2 id="queue-title">Queue</h2>
        <span className="muted">
          {active.length} applying · {waiting.length} waiting
        </span>
      </header>
      <form
        className="queue-drop"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="field">
          <span className="field-label">Add job links</span>
          <textarea
            rows={2}
            placeholder="Paste one or more job links. Each goes straight into the queue."
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <span className="field-hint">
            {links.length
              ? `${plural(links.length, "link")} found${links.length > 25 ? "; the first 25 are added" : ""}`
              : "Your own picks: added without the fit check. Duplicates and finished applications are protected."}
          </span>
        </label>
        <button className="btn primary" type="submit" disabled={!links.length || action.pending === "links"}>
          {action.pending === "links" ? "Adding…" : "Add to queue"}
        </button>
      </form>
      {results.length > 0 && (
        <ul className="list queue-results" aria-label="Pasted links">
          {results.map((r) => (
            <li key={r.url}>
              <span>
                <strong>{linkHost(r.url) || r.url}</strong>
                <span className="muted">{r.message || ""}</span>
              </span>
              <StatePill
                state={r.state === "refused" ? "failed" : r.state === "waiting" ? "queued" : r.state}
                label={
                  r.state === "refused"
                    ? "Not added"
                    : r.state === "waiting"
                      ? "Waiting"
                      : r.state === "preparing"
                        ? "Preparing resume"
                        : r.state === "queued"
                          ? "Queued"
                          : undefined
                }
              />
            </li>
          ))}
        </ul>
      )}
      <ErrorLine error={queue.error} retry={queue.refresh} />
      {active.length + waiting.length > 0 ? (
        <ol className="list queue-list">
          {active.map((item) => (
            <li key={item.run_id}>
              <span>
                <button className="link" onClick={() => openJob(item.job_id, item.run_id)}>
                  <strong>{name(item)}</strong>
                </button>
                <span className="muted">
                  {item.ats} · started {ago(item.since, now)}
                </span>
              </span>
              <StatePill state={item.state} />
            </li>
          ))}
          {waiting.map((item, i) => (
            <li key={item.job_id}>
              <span>
                <button className="link" onClick={() => openJob(item.job_id)}>
                  <strong>
                    {i + 1}. {name(item)}
                  </strong>
                </button>
                <span className="muted">
                  {item.reason || "Waiting for a free worker"} · asked {ago(item.requested_at, now)}
                </span>
              </span>
              <button
                className="btn sm quiet"
                disabled={action.pending === "remove-" + item.job_id}
                onClick={async () => {
                  await action.run(
                    "remove-" + item.job_id,
                    () => api(`/queue/${item.job_id}`, "DELETE"),
                    "Removed from the queue",
                  );
                  await queue.refresh();
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ol>
      ) : (
        queue.data && <p className="block-note">Nothing is queued. Paste a job link above to start one.</p>
      )}
    </section>
  );
}
