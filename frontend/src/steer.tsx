// Take over: finish one application by hand in the app's own Chrome, then hand it back. Optional on any application that stopped,
// failed, is repairing itself or is asking you something; an application nobody takes over carries on by itself, as before.
// The server holds the job while you have it (jobpilot/steer.py): nothing else starts, retries or touches that tab.
import { ExternalLink } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, usePoll } from "./hooks";
import { Button, ConfirmButton } from "./ui/index";

type Steer = { job_id: string; phase: "" | "opening" | "yours" | "released"; tab_open?: boolean; title?: string; browser?: boolean; stuck?: boolean };

/** The steered jobs: the Needs you page lists them as what is in your hands. */
export function useSteered(): { job_id: string; phase: string }[] {
  const { version } = useWorkspace();
  const poll = usePoll<{ jobs: { job_id: string; phase: string }[] }>((signal) => api("/steer", "GET", undefined, signal), 8000, [version]);
  return poll.data?.jobs ?? [];
}

export const takeOverJob = (jobId: string) => api<{ message?: string }>(`/jobs/${jobId}/take-over`, "POST");

/** The one button that starts it. Shown where a stopped application is shown; the page that follows explains the rest. */
export function TakeOverButton({ jobId, size = "sm" }: { jobId: string; size?: "sm" | "md" }) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  return (
    <Button
      size={size}
      variant="ghost"
      loading={pending === "take"}
      title="Open this application in Chrome at the point it stopped, and finish it yourself"
      onClick={() =>
        void run(
          "take",
          async () => {
            const r = await takeOverJob(jobId);
            await refresh();
            return r;
          },
          (r) => r?.message,
        )
      }
    >
      Take over
    </Button>
  );
}

/** What you can do while you have the application: the three ways to end it. */
export function SteerPanel({ jobId }: { jobId: string }) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const poll = usePoll<Steer>((signal) => api(`/jobs/${jobId}/steer`, "GET", undefined, signal), 3000, [jobId]);
  const state = poll.data;
  if (!state || (state.phase !== "opening" && state.phase !== "yours")) return null;
  const end = (how: "done" | "submitted" | "back") =>
    run(
      how,
      async () => {
        const r = await api<{ message?: string }>(`/jobs/${jobId}/steer/${how}`, "POST");
        await refresh();
        await poll.refresh();
        return r;
      },
      (r) => r?.message,
    );
  return (
    <section className="nd-steer" aria-label="You have this application">
      {state.phase === "opening" ? (
        state.stuck ? (
          <>
            <p>The application didn't open. Give it back and it carries on by itself, or take it over again later.</p>
            <div className="nd-actions">
              <Button variant="primary" loading={pending === "back"} disabled={pending !== null} onClick={() => void end("back")}>
                Give it back
              </Button>
            </div>
          </>
        ) : (
          <p>Opening the application in Chrome at the point it stopped. It is yours as soon as the page is ready.</p>
        )
      ) : (
        <>
          <p>
            <strong>This application is open in Chrome for you.</strong>{" "}
            {state.tab_open
              ? "Finish what it stopped on, then tell AXIOM how it went. Nothing else touches that tab meanwhile."
              : "The Chrome tab was closed. Done, continue opens the application again from the draft, and checks every answer as usual: what you typed in that tab counts only if the form saved it."}
          </p>
          <div className="nd-actions">
            <Button variant="primary" loading={pending === "done"} disabled={pending !== null} onClick={() => void end("done")}>
              Done, continue
            </Button>
            <ConfirmButton
              label="I submitted it myself"
              confirm="Yes, it was sent"
              note="Recorded as sent. It will not be sent again."
              variant="secondary"
              size="md"
              pending={pending === "submitted"}
              onConfirm={() => end("submitted")}
            />
            <Button variant="ghost" loading={pending === "back"} disabled={pending !== null} onClick={() => void end("back")}>
              Give it back
            </Button>
          </div>
          <p className="ax-note">
            Done, continue keeps what is on the page you have open and carries on from there. Give it back returns the application to automatic,
            and AXIOM checks every answer as usual. If you pressed Submit yourself, choose I submitted it myself: either of the others could send it twice.
          </p>
        </>
      )}
    </section>
  );
}

/** Needs you › In your hands: every application you have taken over, with its panel. */
export function SteeredJobs({ jobs }: { jobs: { job_id: string }[] }) {
  return (
    <div className="nd-pane-in nd-decide">
      {jobs.map((j) => (
        <SteeredJob key={j.job_id} jobId={j.job_id} />
      ))}
    </div>
  );
}

function SteeredJob({ jobId }: { jobId: string }) {
  const { openJob, version } = useWorkspace();
  // A held job is paused, so the workspace's live rows don't carry it: read it on its own.
  const read = usePoll<{ job: { company?: string; title?: string; url?: string } }>((signal) => api(`/jobs/${jobId}`, "GET", undefined, signal), 600000, [jobId, version]);
  const job = read.data?.job;
  return (
    <div className="nd-card">
      <header className="nd-card-head">
        <h2 className="nd-q">{job?.company || job?.title || "Application"}</h2>
        {job?.company && job.title && <p className="nd-meta">{job.title}</p>}
      </header>
      <SteerPanel jobId={jobId} />
      <div className="nd-actions">
        <Button variant="ghost" size="sm" icon={<ExternalLink size={13} aria-hidden="true" />} onClick={() => openJob(jobId)}>
          Open job
        </Button>
      </div>
    </div>
  );
}
