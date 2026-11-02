// Employers stopped as a whole: their own stated limit, a wait their site asked for, or the loop guard (jobpilot/loop_guard.py).
// One item each, whatever the number of jobs behind it (owner, 2026-11-01: "Tiktok applications went on a never ending loop").
// Try again is the way past a limit that was misread or does not apply to these jobs: a real one comes back with the next refusal.
import { api } from "./api";
import { useWorkspace } from "./context";
import { plural, plainReason } from "./format";
import { useAction, usePoll } from "./hooks";
import "./identity/overview.css";
import type { Startable } from "./types";
import { Button, MonogramStack, Panel } from "./ui/index";

export function HeldEmployers() {
  const { version, refresh } = useWorkspace();
  const said = usePoll<Startable>((signal) => api("/startable", "GET", undefined, signal), 30000, [version]);
  const { pending, run } = useAction();
  const items = said.data?.employers ?? [];
  if (!items.length) return null;
  const release = (key: string) =>
    run("release:" + key, async () => {
      await api("/held-employers/release", "POST", { key });
      await refresh();
      said.refresh();
    });
  return (
    <Panel title={items.length === 1 ? "An employer on hold" : `${items.length} employers on hold`} flush>
      <ul className="ov-list ov-held">
        {items.map((item) => (
          <li key={item.key} className="ov-q">
            <MonogramStack names={[item.company || "Employer"]} max={1} />
            <div className="ov-q-text">
              <p>{plainReason(item.reason)}</p>
              {item.jobs > 0 && <small>{plural(item.jobs, "job")} waiting, nothing opens a browser</small>}
            </div>
            <Button size="sm" variant="ghost" disabled={pending !== null} loading={pending === "release:" + item.key} onClick={() => release(item.key)}>
              Try again
            </Button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
