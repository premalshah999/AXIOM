import { useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { appliedState } from "./model";
import { linkHost, plural, StatePill } from "./ui";

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

// Owner, 2026-10-22: queued applications in one place, and a pasted link goes straight into the queue.
// The queue itself is the Pipeline list's "In progress" rows (applying, queued, waiting to start, waiting for an
// email), counted by the server like every other figure. A second list of the same jobs here, polled every 5 s with
// its own numbers, disagreed with the chips below it (owner, 2026-10-23: "so confusing for the job pipeline part").
export function QueuePanel() {
  const { refresh, counts, go, openJob, markJobs } = useWorkspace();
  const action = useAction();
  const [text, setText] = useState("");
  const [results, setResults] = useState<LinkResult[]>([]);
  const links = linksIn(text);
  const n = (state: string) => counts.states[state] ?? 0;
  const parts = [
    [n("running") + n("submitting"), "applying"],
    [n("queued"), "queued"],
    [n("pending:waiting"), "waiting to start"],
    [n("waiting_email"), "waiting for an email"],
  ] as const;

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
      for (const r of answer.results)
        if (r.job_id && r.state !== "refused") markJobs([r.job_id], appliedState(r.state));
      void refresh();
    }
  };

  return (
    <section className="block queue" aria-labelledby="queue-title">
      <header className="block-head">
        <h2 id="queue-title">Queue</h2>
        <button
          className="link sm"
          onClick={() => go({ view: "jobs", tab: "pipeline", filter: "active" })}
        >
          {parts.map(([count, label]) => `${count} ${label}`).join(" · ")}
        </button>
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
                {r.job_id ? (
                  <button className="link job-open" onClick={() => openJob(r.job_id!)}>
                    <strong>{linkHost(r.url) || r.url}</strong>
                  </button>
                ) : (
                  <strong>{linkHost(r.url) || r.url}</strong>
                )}
                <span className="muted">{r.message || ""}</span>
              </span>
              <StatePill
                state={r.state === "refused" ? "failed" : appliedState(r.state)}
                label={r.state === "refused" ? "Not added" : undefined}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
