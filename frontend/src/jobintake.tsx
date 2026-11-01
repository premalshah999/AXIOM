import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useNotify } from "./hooks";
import { appliedState } from "./model";
import type { Job } from "./types";
import { linkHost, plural } from "./format";
import { Button, Dialog } from "./ui/index";
import { StatusPill } from "./worktools";

/** Dispatch this on window to open the dialog from anywhere: Jobs' Add job button, the palette, the first-run checklist. */
export const OPEN_JOB_LINK = "applypilot:add-job-link";

type LinkResult = { url: string; job_id?: string; state: string; message?: string };

// Every http(s) link in what was pasted: one per line, comma- or space-separated, or inside other text. A comma belongs to the
// link unless another link follows it: career sites put one in the path (/job/Nashville,-TN-Engineer/123), and a pasted link cut
// there opened the city's page instead of the posting.
const LINKS = /https?:\/\/[^\s<>"']+/gi;
const linksIn = (text: string) => [
  ...new Set((text.match(LINKS) || []).flatMap((m) => m.split(/,(?=https?:\/\/)/i)).map((u) => u.replace(/[.,;)]+$/, ""))),
];
const MAX_LINKS = 25;

/**
 * "Add job": paste a link, or several, and they go straight into the queue as your own pick (owner, 2026-10-22). One link opens the
 * job it made; several list what happened to each. main.tsx mounts it once; it has no button of its own.
 */
export function AddJobDialog() {
  const { snapshot, route, go, refresh, markJobs, openJob } = useWorkspace();
  const notify = useNotify();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<LinkResult[]>([]);
  const links = linksIn(text);
  const autoApply = snapshot.profile.autonomous && snapshot.control.auto_submit && !snapshot.control.paused;

  useEffect(() => {
    const show = () => {
      setOpen(true);
      setError("");
      setResults([]);
    };
    window.addEventListener(OPEN_JOB_LINK, show);
    return () => window.removeEventListener(OPEN_JOB_LINK, show);
  }, []);

  const add = async (urls: string[]): Promise<LinkResult[]> => {
    try {
      const answer = await api<{ results: LinkResult[] }>("/queue/links", "POST", { urls });
      return answer.results;
    } catch (e) {
      // Without application workers (a demo installation) the links are saved instead.
      if (!/workers are disabled/i.test((e as Error).message)) throw e;
      const saved: LinkResult[] = [];
      for (const url of urls) {
        const r = await api<{ job: Job; created: boolean }>("/jobs", "POST", { url });
        saved.push({ url, job_id: r.job.id, state: "saved", message: r.created ? "Saved. Workers are off, so it was not queued." : "This job is already saved." });
      }
      return saved;
    }
  };

  const submit = async () => {
    if (!links.length || pending) return;
    setPending(true);
    setError("");
    try {
      const out = await add(links.slice(0, MAX_LINKS));
      for (const r of out) if (r.job_id && r.state !== "refused" && r.state !== "saved") markJobs([r.job_id], appliedState(r.state));
      setText("");
      void refresh();
      if (out.length === 1) {
        const only = out[0]!;
        if (only.state === "refused" || !only.job_id) {
          setError(only.message || "This link was not added.");
          setText(only.url);
          return;
        }
        setOpen(false);
        notify("ok", only.message || "Added to the queue.");
        go({ ...route, job: only.job_id, run: undefined });
      } else setResults(out);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  const close = () => setOpen(false);
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add a job"
      sub={autoApply ? "It goes straight into the queue, without the fit check." : "It waits in the queue until automatic submission is on."}
      footer={
        results.length ? (
          <Button variant="primary" onClick={close}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!links.length} loading={pending} onClick={submit}>
              {links.length > 1 ? `Add ${Math.min(links.length, MAX_LINKS)} jobs` : "Add to queue"}
            </Button>
          </>
        )
      }
    >
      {results.length ? (
        <ul className="ax-rows" aria-label="Pasted links">
          {results.map((r) => (
            <li key={r.url} className="ax-row">
              <span className="ax-row-main">
                {r.job_id ? (
                  <button
                    type="button"
                    className="ax-link"
                    onClick={() => {
                      close();
                      openJob(r.job_id!);
                    }}
                  >
                    {linkHost(r.url) || r.url}
                  </button>
                ) : (
                  <strong>{linkHost(r.url) || r.url}</strong>
                )}
                {r.message && <small>{r.message}</small>}
              </span>
              <StatusPill state={r.state === "refused" ? "failed" : r.state === "saved" ? "job:ready" : appliedState(r.state)} label={r.state === "refused" ? "Not added" : r.state === "saved" ? "Saved" : undefined} />
            </li>
          ))}
        </ul>
      ) : (
        <div className="ax-field">
          <textarea
            className="ax-control"
            aria-label="Job links"
            aria-invalid={error ? true : undefined}
            data-autofocus
            rows={3}
            maxLength={40000}
            placeholder="https://company.com/careers/job/…"
            value={text}
            disabled={pending}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          {error ? (
            <p className="ax-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="ax-hint">
              {links.length
                ? `${plural(links.length, "link")} found${links.length > MAX_LINKS ? `; the first ${MAX_LINKS} are added` : ""}. Enter adds them.`
                : "One link, or several on separate lines."}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
