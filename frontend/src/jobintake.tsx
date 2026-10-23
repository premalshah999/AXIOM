import { useEffect, useState } from "react";
import type { Job } from "./types";

export const OPEN_JOB_LINK = "applypilot:add-job-link";

type Request = <T = any>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;

export function JobLinkIntake({
  request,
  refresh,
  autoApply,
  onImported,
}: {
  request: Request;
  refresh: () => Promise<void>;
  autoApply: boolean;
  // Optional: the shell opens the added job and shows the message itself.
  onImported?: (jobId: string, message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    if (open) window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);
  // Other screens (the first-run checklist) open this dialog without owning its state.
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_JOB_LINK, show);
    return () => window.removeEventListener(OPEN_JOB_LINK, show);
  }, []);
  return (
    <div>
      <button
        className="button compact"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls="manual-job-link"
      >
        Add job link
      </button>
      {open && (
        <div className="overlay" onClick={() => setOpen(false)}>
          <section
            className="drawer"
            id="manual-job-link"
            role="dialog"
            aria-modal="true"
            aria-label="Add application link"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="close"
              aria-label="Close application link"
              onClick={() => setOpen(false)}
            >
              Close
            </button>
            <h2>Add application link</h2>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setPending(true);
                setError("");
                setMessage("");
                try {
                  // Owner, 2026-10-22: a pasted link goes straight into the queue as the owner's own pick, the same
                  // as Jobs › Pipeline's box (this button only saved it, or ranked it first, before).
                  let jobId = "";
                  let text = "";
                  try {
                    const answer = await request<{
                      results: { job_id?: string; state: string; message?: string }[];
                    }>("/queue/links", "POST", { urls: [url.trim()] });
                    const r = answer.results[0];
                    if (r.state === "refused" || !r.job_id) throw new Error(r.message || "This link was not added");
                    jobId = r.job_id;
                    text = r.message || "Added to the queue.";
                  } catch (e) {
                    // Without application workers (a demo installation) the link is saved instead.
                    if (!/workers are disabled/i.test((e as Error).message)) throw e;
                    const saved = await request<{ job: Job; created: boolean }>("/jobs", "POST", { url: url.trim() });
                    jobId = saved.job.id;
                    text = saved.created ? "Job saved. Workers are off, so it was not queued." : "This job is already saved.";
                  }
                  setMessage(text);
                  setUrl("");
                  await refresh();
                  if (onImported) {
                    setMessage("");
                    setOpen(false);
                    onImported(jobId, text);
                  }
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setPending(false);
                }
              }}
            >
              <label htmlFor="application-url">Application URL</label>
              <input
                id="application-url"
                type="url"
                autoFocus
                required
                maxLength={2048}
                placeholder="https://company.com/careers/job/..."
                value={url}
                disabled={pending}
                onChange={(e) => setUrl(e.target.value)}
              />
              <p className="muted">
                {autoApply
                  ? "It goes straight into the queue as your own pick, without the fit check. Duplicates and finished applications are protected."
                  : "It goes into the queue and waits until automatic submission is on. Duplicates are protected."}
              </p>
              <button
                className="button primary"
                type="submit"
                disabled={pending}
              >
                {pending ? "Adding…" : "Add to queue"}
              </button>
              {message && <p role="status">{message}</p>}
              {error && (
                <p role="alert" className="error-text">
                  {error}
                </p>
              )}
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
