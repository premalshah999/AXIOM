import { ArrowRight } from "lucide-react";
import { api } from "../api";
import { useWorkspace, type AnyRoute } from "../context";
import { platformName, plural } from "../format";
import { usePoll } from "../hooks";
import { Empty, ErrorLine, Panel, Skeleton, Tooltip } from "../ui/index";
import type { Success } from "../types";

type Funnel = { discovered: number; eligible: number; applied: number; platforms: Record<string, number> };

const percent = (part: number, whole: number) => (whole > 0 ? `${Math.min(100, Math.round((part / whole) * 100))}%` : "—");

/**
 * Everything found, down to what the employer confirmed. All time, and every stage is real: `GET /api/funnel` counts the first three
 * over the jobs table (pipeline.funnel), and Confirmed is the Applications page's own list. Each bar is drawn to one scale from one
 * baseline; only Confirmed, the point of the machine, is at full strength.
 */
function FunnelPanel({ data, error, retry, confirmed }: { data: Funnel | null; error: Error | null; retry: () => void; confirmed: number | null }) {
  const { go, counts } = useWorkspace();
  const stages: { label: string; n: number; help: string; to: AnyRoute; lit?: boolean }[] = data
    ? [
        { label: "Discovered", n: data.discovered, help: "Every posting AXIOM has found, all time.", to: { view: "jobs", tab: "discovered" } },
        { label: "Eligible", n: data.eligible, help: "Passed the fit check, or you picked it yourself.", to: { view: "jobs", tab: "discovered", filter: "eligible" } },
        { label: "Applied", n: data.applied, help: "An application started. Not all of these reached the final step.", to: { view: "jobs", tab: "pipeline" } },
        { label: "Confirmed", n: confirmed ?? 0, help: "Proven by the employer's confirmation page or an email from the employer.", to: { view: "ledger" }, lit: true },
      ]
    : [];
  const top = Math.max(1, ...stages.map((s) => s.n));
  return (
    <Panel title="From discovered to confirmed">
      {error && !data ? (
        <ErrorLine error={error} retry={retry} />
      ) : !data ? (
        <Skeleton rows={4} mono={false} />
      ) : data.discovered === 0 ? (
        <Empty compact title="Nothing found yet" hint="Once discovery finds jobs, this shows how many reach each stage." />
      ) : (
        <>
          <ol className="ov-funnel">
            {stages.map((s, i) => (
              <li key={s.label}>
                <Tooltip label={s.help} className="ov-funnel-tip">
                  <button type="button" className="ov-funnel-row" onClick={() => go(s.to)}>
                    <span className="ov-funnel-label">{s.label}</span>
                    <span className="ov-funnel-bar">
                      <i className={s.lit ? "is-lit" : ""} style={{ width: `${Math.max(s.n ? 1 : 0, (s.n / top) * 100)}%` }} />
                    </span>
                    <span className="ov-num">{s.n.toLocaleString()}</span>
                    <span className="ov-rate">{i === 0 ? "" : percent(s.n, stages[i - 1]!.n)}</span>
                  </button>
                </Tooltip>
              </li>
            ))}
          </ol>
          <p className="ov-caption">
            All time; each percentage is of the stage above.
            {(counts.discovered ?? 0) > 0 && ` Discovered lists the ${counts.discovered!.toLocaleString()} recent ones not applied to yet.`}
          </p>
        </>
      )}
    </Panel>
  );
}

// Past six platforms the tail folds into Other: it is a table of what matters, not an inventory.
const SHOWN = 6;

function PlatformPanel({ data, ledger }: { data: Funnel | null; ledger: Success[] | null }) {
  const { go } = useWorkspace();
  if (!data || !ledger) return null;
  const confirmed = new Map<string, number>();
  for (const a of ledger) confirmed.set(a.ats, (confirmed.get(a.ats) ?? 0) + 1);
  const all = Object.entries(data.platforms)
    .map(([ats, applied]) => ({ ats, applied, confirmed: confirmed.get(ats) ?? 0 }))
    .filter((r) => r.applied > 0)
    .sort((a, b) => b.applied - a.applied);
  if (!all.length) return null;
  const rows = all.slice(0, SHOWN);
  const tail = all.slice(SHOWN);
  const other = tail.length
    ? { applied: tail.reduce((n, r) => n + r.applied, 0), confirmed: tail.reduce((n, r) => n + r.confirmed, 0) }
    : null;
  const bar = (part: number, whole: number) => (
    <span className="ov-meter" aria-hidden="true">
      <i style={{ width: percent(part, whole) === "—" ? 0 : percent(part, whole) }} />
    </span>
  );
  return (
    <Panel title="Success by platform" flush>
      <table className="ov-table">
        <thead>
          <tr>
            <th scope="col">Platform</th>
            <th scope="col" className="is-num">
              Applied
            </th>
            <th scope="col" className="is-num">
              Confirmed
            </th>
            <th scope="col" className="is-num">
              Success
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ats}>
              <th scope="row">
                <button type="button" className="ax-quiet-link" onClick={() => go({ view: "jobs", tab: "pipeline", q: r.ats })} title={`See ${plural(r.applied, "job")} on ${platformName(r.ats)}`}>
                  {platformName(r.ats)}
                </button>
              </th>
              <td className="is-num">{r.applied.toLocaleString()}</td>
              <td className="is-num">{r.confirmed.toLocaleString()}</td>
              <td className="is-num">
                {bar(r.confirmed, r.applied)}
                {percent(r.confirmed, r.applied)}
              </td>
            </tr>
          ))}
          {other && (
            <tr>
              <th scope="row">
                Other <span className="ax-note">({tail.length})</span>
              </th>
              <td className="is-num">{other.applied.toLocaleString()}</td>
              <td className="is-num">{other.confirmed.toLocaleString()}</td>
              <td className="is-num">
                {bar(other.confirmed, other.applied)}
                {percent(other.confirmed, other.applied)}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Panel>
  );
}

/** Overview's right column: how the whole thing has performed, polled gently because it changes slowly. */
export function Results({ ledger }: { ledger: Success[] | null }) {
  const funnel = usePoll<Funnel>((signal) => api("/funnel", "GET", undefined, signal), 60000);
  return (
    <>
      <FunnelPanel data={funnel.data} error={funnel.error} retry={funnel.refresh} confirmed={ledger?.length ?? null} />
      <PlatformPanel data={funnel.data} ledger={ledger} />
    </>
  );
}

/** "All applications →": the way from a panel to the list behind it. */
export function MoreLink({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" className="ax-more-link" onClick={onClick}>
      {children}
      <ArrowRight size={13} aria-hidden="true" />
    </button>
  );
}
