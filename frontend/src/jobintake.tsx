import { useEffect, useState } from "react";
import type { Job, Run } from "./types";

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
  // Optional: the shell opens the imported job and shows the message itself.
  onImported?: (job: Job, message: string) => void;
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
                  const result = await request<{
                    job: Job;
                    created: boolean;
                    run?: Run;
                    notice?: string;
                  }>("/jobs", "POST", { url: url.trim() });
                  const text = result.run
                    ? "Application queued. Track it in Jobs."
                    : result.notice ||
                      (result.created
                        ? "Job saved. Classification and queue status are available in Jobs."
                        : "This job is already saved. Its existing progress was preserved.");
                  setMessage(text);
                  setUrl("");
                  await refresh();
                  if (onImported) {
                    setMessage("");
                    setOpen(false);
                    onImported(result.job, text);
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
                  ? "The agent checks the role and applies if it matches your profile. Existing applications are protected from duplicates."
                  : "Save a job now. Automatic application follows your current batch settings."}
              </p>
              <button
                className="button primary"
                type="submit"
                disabled={pending}
              >
                {pending
                  ? "Checking job…"
                  : autoApply
                    ? "Add and apply"
                    : "Save job"}
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
