import { useEffect, useState } from "react";
import type { Job } from "./types";
import { Button, Drawer, Input } from "./ui/index";

/** Dispatch this on window to open the dialog from anywhere: the palette, Jobs › Pipeline, the first-run checklist. */
export const OPEN_JOB_LINK = "applypilot:add-job-link";

type Request = <T = any>(
  path: string,
  method?: string,
  body?: unknown,
) => Promise<T>;

/** The "Add a job link" dialog. main.tsx mounts it once; it has no button of its own. */
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
    const show = () => setOpen(true);
    window.addEventListener(OPEN_JOB_LINK, show);
    return () => window.removeEventListener(OPEN_JOB_LINK, show);
  }, []);
  return (
    <Drawer open={open} onClose={() => setOpen(false)} title="Add a job link" width={480}>
      <form
        className="ax-stack"
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
        <Input
          label="Application URL"
          type="url"
          data-autofocus
          required
          maxLength={2048}
          placeholder="https://company.com/careers/job/..."
          value={url}
          disabled={pending}
          error={error || undefined}
          hint={
            autoApply
              ? "It goes straight into the queue, without the fit check."
              : "It waits in the queue until automatic submission is on."
          }
          onChange={(e) => setUrl(e.target.value)}
        />
        <div>
          <Button variant="primary" type="submit" loading={pending}>
            Add to queue
          </Button>
        </div>
        {message && <p role="status">{message}</p>}
      </form>
    </Drawer>
  );
}
