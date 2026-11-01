import { useCallback, useContext, useEffect, useState } from "react";
import { api } from "../api";
import { WorkspaceContext } from "../context";
import { usePoll } from "../hooks";

/** What the server says about Stop (jobpilot/stop.py): the countdown in `sentence` is its own, read each second while anything ends. */
export type StopStatus = {
  stopped: boolean;
  settled: boolean;
  running: number;
  submitting: number;
  queued: number;
  waiting: number;
  seconds_left: number;
  sentence: string;
};

const CHANGED = "axiom:stop-changed";

/** The one way to stop or resume, wherever the press comes from. */
export async function pressStop(): Promise<StopStatus> {
  const status = await api<StopStatus>("/stop", "POST");
  window.dispatchEvent(new Event(CHANGED));
  return status;
}

export async function pressResume(): Promise<StopStatus> {
  const status = await api<StopStatus>("/resume", "POST");
  window.dispatchEvent(new Event(CHANGED));
  return status;
}

function useStopStatus(enabled: boolean) {
  const [fast, setFast] = useState(false);
  const poll = usePoll<StopStatus>((signal) => api("/stop", "GET", undefined, signal), fast ? 1000 : 4000, [fast], enabled);
  const { data, refresh } = poll;
  useEffect(() => setFast(!!data?.stopped && !data.settled), [data?.stopped, data?.settled]);
  useEffect(() => {
    const changed = () => void refresh();
    window.addEventListener(CHANGED, changed);
    return () => window.removeEventListener(CHANGED, changed);
  }, [refresh]);
  return poll;
}

export type StopControl = {
  status: StopStatus | null;
  busy: boolean;
  problem: string;
  press: () => void;
};

/**
 * The Stop control: in the header of every screen, in the same place, in plain words. Stop asks nothing: the person pressing it
 * wants it stopped now. Resume is its own press and nothing else lifts a stop.
 */
export function useStopControl(): StopControl {
  const workspace = useContext(WorkspaceContext);
  const { data, refresh } = useStopStatus(true);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const stopped = !!data?.stopped;
  const press = useCallback(async () => {
    setBusy(true);
    setProblem("");
    try {
      await (stopped ? pressResume() : pressStop());
      await refresh();
      await workspace?.refresh?.();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [stopped, refresh, workspace]);
  return { status: data, busy, problem, press };
}

export function StopButton({ control }: { control: StopControl }) {
  const stopped = !!control.status?.stopped;
  return (
    <button
      type="button"
      className={"ax-stop" + (stopped ? " is-resume" : "")}
      disabled={control.busy}
      aria-busy={control.busy}
      onClick={control.press}
    >
      {stopped ? (
        <>
          <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
            <path d="M3 1.5v9l7-4.5z" fill="currentColor" />
          </svg>
          Resume applications
        </>
      ) : (
        <>
          <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
            <rect x="2" y="2" width="8" height="8" rx="1" fill="currentColor" />
          </svg>
          Stop applications
        </>
      )}
    </button>
  );
}

/** One sentence: what is stopped, what is finishing with its countdown, and when everything has stopped. */
export function StopLine({ control }: { control: StopControl }) {
  const { status, problem } = control;
  if (!status?.stopped && !problem) return null;
  return (
    <div className={"ax-banner ax-stop-line" + (status?.settled ? "" : " is-ending")} role="status" aria-live="polite">
      <span>{problem || status?.sentence}</span>
    </div>
  );
}

/** For a screen outside the frame (the setup guide reopened while a batch runs). */
export function StopFloating() {
  const control = useStopControl();
  return (
    <div className="ax-stop-float">
      <StopButton control={control} />
      <StopLine control={control} />
    </div>
  );
}
