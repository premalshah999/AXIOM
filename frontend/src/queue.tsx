import { useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { appliedState } from "./model";
import { linkHost, plural } from "./ui";
import { Button } from "./ui/index";
import { StatusPill } from "./worktools";

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
  const { refresh, openJob, markJobs } = useWorkspace();
  const action = useAction();
  const [text, setText] = useState("");
  const [focused, setFocused] = useState(false);
  const [results, setResults] = useState<LinkResult[]>([]);
  const links = linksIn(text);

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
    <section className="ax-queue-box" aria-label="Add job links to the queue">
      <form
        className="ax-queue"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {/* One line until used: the box took a fifth of the Pipeline above the list (owner 2026-10-28: "cluttered"). */}
        <textarea
          className="ax-control"
          aria-label="Job links to add to the queue"
          rows={text || focused ? 3 : 1}
          placeholder="Paste job links to queue"
          value={text}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <Button variant="primary" type="submit" disabled={!links.length} loading={action.pending === "links"}>
          Add to queue
        </Button>
      </form>
      {links.length > 0 && (
        <p className="ax-hint">
          {plural(links.length, "link")} found{links.length > 25 ? "; the first 25 are added" : ""}. Ctrl or ⌘ Enter adds them.
        </p>
      )}
      {results.length > 0 && (
        <ul className="ax-rows" aria-label="Pasted links">
          {results.map((r) => (
            <li key={r.url} className="ax-row">
              <span className="ax-row-main">
                {r.job_id ? (
                  <button type="button" className="ax-link" onClick={() => openJob(r.job_id!)}>
                    {linkHost(r.url) || r.url}
                  </button>
                ) : (
                  <strong>{linkHost(r.url) || r.url}</strong>
                )}
                {r.message && <small>{r.message}</small>}
              </span>
              <StatusPill
                state={r.state === "refused" ? "failed" : appliedState(r.state)}
                label={r.state === "refused" ? "Not added" : undefined}
              />
            </li>
          ))}
          <li className="ax-row">
            <Button size="sm" variant="ghost" onClick={() => setResults([])}>
              Dismiss
            </Button>
          </li>
        </ul>
      )}
    </section>
  );
}
