import { ArrowRight } from "lucide-react";
import { Fragment } from "react";
import { api } from "../api";
import { useWorkspace, type AnyRoute } from "../context";
import { platformName, plural } from "../format";
import { usePoll } from "../hooks";
import { Empty, ErrorLine, Panel, Skeleton, Tooltip } from "../ui/index";
import type { Success, SuccessReport } from "../types";

type Funnel = { discovered: number; eligible: number; applied: number; platforms: Record<string, number> };

const percent = (part: number, whole: number) => (whole > 0 ? `${Math.min(100, Math.round((part / whole) * 100))}%` : "—");

/**
 * Everything found, down to what the employer confirmed. All time, and every stage is real: `GET /api/funnel` counts the first three
 * over the jobs table (pipeline.funnel), and Confirmed is the Applications page's own list. Each bar is drawn to one scale from one
 * baseline; only Confirmed, the point of the machine, is at full strength.
 */
function FunnelPanel({ data, error, retry, confirmed }: { data: Funnel | null; error: Error | null; retry: () => void; confirmed: number | null }) {
  const { go } = useWorkspace();
  const stages: { label: string; n: number; help: string; to: AnyRoute; lit?: boolean }[] = data
    ? [
        { label: "Found", n: data.discovered, help: "Every posting AXIOM has found, all time. The Job board lists the open ones.", to: { view: "board" } },
        { label: "Good match", n: data.eligible, help: "Scored at least your How close a match number, or you picked it yourself.", to: { view: "board" } },
        { label: "Tried", n: data.applied, help: "An application was started. Some are still running and some did not go through; the ones the employer confirmed are under Applications.", to: { view: "ledger" } },
        { label: "Confirmed", n: confirmed ?? 0, help: "Proven by the employer's confirmation page or an email from the employer.", to: { view: "ledger" }, lit: true },
      ]
    : [];
  const top = Math.max(1, ...stages.map((s) => s.n));
  return (
    <Panel title="From found to confirmed">
      {error && !data ? (
        <ErrorLine error={error} retry={retry} />
      ) : !data ? (
        <Skeleton rows={4} mono={false} />
      ) : data.discovered === 0 ? (
        <Empty compact title="Nothing found yet" hint="Once AXIOM finds jobs, this shows how many reach each stage." />
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
            All time; each percentage is of the stage above. The Job board lists only the postings that are still open.
          </p>
        </>
      )}
    </Panel>
  );
}

const pct = (n: number | null) => (n == null ? "—" : `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`);

/** Why a job left the count (jobpilot/api/success_core.py EXCLUDED), in the person's words. */
const LEFT_OUT: Record<string, string> = {
  already_on_file: "Already on file, not sent by AXIOM",
  in_progress: "Still running",
  employer_limit: "The employer's limit on your applications",
  sensitive_id_required: "Asks for an SSN or ID number",
  closed: "Posting closed or not open to you",
  cancelled: "Cancelled or removed",
  paused_by_owner: "You paused it",
  waiting_on_owner: "Only you can answer",
};

/** One platform: its name and what it confirmed, a dot bar against the target tick, and the percentage. Opens that platform's jobs. */
function ScoreRow({ row, target, below }: { row: SuccessReport["platforms"][number]; target: number; below: boolean }) {
  const { go } = useWorkspace();
  const share = Math.max(0, Math.min(100, row.final_pct ?? 0));
  return (
    <li>
      <button
        type="button"
        className="ov-score-row"
        onClick={() => go({ view: "jobs", tab: "pipeline", q: row.ats })}
        title={`See the applications confirmed on ${platformName(row.ats)}`}
      >
        <span className="ov-score-name">
          {platformName(row.ats)}
          <em>
            {row.confirmed.toLocaleString()} of {row.eligible.toLocaleString()}
          </em>
        </span>
        <span className={"ov-score-bar" + (below ? " is-below" : "")} aria-hidden="true">
          <i style={{ width: `${share}%` }} />
          <b style={{ left: `${target}%` }} />
        </span>
        <span className="ov-num">{pct(row.final_pct)}</span>
      </button>
    </li>
  );
}

/**
 * The number the owner asked for, where they will see it: of the jobs AXIOM could honestly apply to over the last week, how many
 * were confirmed, against the 90% target. Platforms below the target come first, custom company sites included; the ones on target
 * and the ones with too few jobs to judge are folded, and the counting rules are one fold away. `GET /api/success`, read-only.
 */
function SuccessPanel({ ledger }: { ledger: Success[] | null }) {
  const { data, error, refresh } = usePoll<SuccessReport>((signal) => api("/success?days=7", "GET", undefined, signal), 60000);
  const { go } = useWorkspace();
  if (!data) {
    return (
      <Panel title="Success, last 7 days">
        {error ? <ErrorLine error={error} retry={refresh} /> : <Skeleton rows={4} mono={false} />}
      </Panel>
    );
  }
  const all = data.overall;
  const judged = data.platforms.filter((p) => p.volume && p.final_pct != null);
  const below = judged.filter((p) => !p.meets_target);
  const on = judged.filter((p) => p.meets_target);
  const small = data.platforms.filter((p) => !p.volume);
  const week = data.days === 7 ? "the last 7 days" : `the last ${data.days} days`;
  const reasons = Object.entries(all.excluded)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const lesser = all.evidence.medium + all.evidence.weak;
  // The Applications list holds every confirmation by when it was sent: the same window there is these jobs and the ones first tried
  // before it, whose number belongs to an earlier week. Its other rows the employer already had, and no "sent" count takes them.
  const from = Date.parse(data.since);
  const sent = ledger?.filter((a) => !a.existing_application && Date.parse(a.applied_at) >= from).length ?? 0;
  const earlier = Math.max(0, sent - all.confirmed);
  const onFile = ledger?.filter((a) => a.existing_application).length ?? 0;
  if (!all.tried) {
    return (
      <Panel title="Success, last 7 days">
        <Empty compact title="No applications yet" hint={`Once AXIOM has tried jobs in ${week}, how many it confirmed shows here.`} />
      </Panel>
    );
  }
  return (
    <Panel title={data.days === 7 ? "Success, last 7 days" : `Success, last ${data.days} days`}>
      <div className="ov-score">
        <span className="ov-score-big" aria-label={pct(all.final_pct)}>
          {/* Whole points, rounded down: 89.7 never reads as 90. The decimal is in the sentence beside it. */}
          <span className="ax-read ov-score-read" aria-hidden="true">
            {all.final_pct == null ? "–" : Math.floor(all.final_pct)}
          </span>
          <span className="ov-score-unit" aria-hidden="true">
            %
          </span>
        </span>
        <div className="ov-score-say">
          <p>
            <button type="button" className="ax-quiet-link" onClick={() => go({ view: "ledger" })}>
              {all.confirmed.toLocaleString()} confirmed
            </button>{" "}
            of {all.eligible.toLocaleString()} jobs it could apply to in {week}: {pct(all.final_pct)}.
          </p>
          <p className={all.meets_target ? "ov-caption" : "ov-caption is-short"}>
            Target {data.target}%. {all.meets_target ? "On target overall" : "Below target overall"}
            {below.length ? `; ${plural(below.length, "platform")} below it.` : "."}
          </p>
        </div>
      </div>
      <p className="ov-caption">A job counts unless its posting closed, the employer already had an application, it waits on you, or it is still running.</p>
      {below.length > 0 && (
        <ul className="ov-score-rows" aria-label="Platforms below target">
          {below.map((p) => (
            <ScoreRow key={p.ats} row={p} target={data.target} below />
          ))}
        </ul>
      )}
      {on.length > 0 && (
        <details className="ax-fold ov-score-fold">
          <summary>
            On target <strong>{on.length}</strong>
          </summary>
          <ul className="ov-score-rows">
            {on.map((p) => (
              <ScoreRow key={p.ats} row={p} target={data.target} below={false} />
            ))}
          </ul>
        </details>
      )}
      <details className="ax-fold ov-score-fold">
        <summary>How this is counted</summary>
        <dl className="ov-score-facts">
          <dt>Jobs tried</dt>
          <dd className="mono">{all.attempted.toLocaleString()}</dd>
          <dt>Left out</dt>
          <dd className="mono">{(all.attempted - all.eligible).toLocaleString()}</dd>
          {reasons.map(([code, n]) => (
            <Fragment key={code}>
              <dt className="is-part">{LEFT_OUT[code] ?? code}</dt>
              <dd className="mono is-part">{n.toLocaleString()}</dd>
            </Fragment>
          ))}
          <dt>Jobs it could apply to</dt>
          <dd className="mono">{all.eligible.toLocaleString()}</dd>
        </dl>
        <dl className="ov-score-facts">
          <dt>Confirmed, of every job tried</dt>
          <dd className="mono">{pct(all.raw_pct)}</dd>
          <dt>Confirmed, of the jobs it could apply to</dt>
          <dd className="mono">{pct(all.final_pct)}</dd>
          <dt>Confirmed on the first try</dt>
          <dd className="mono">{pct(all.first_try_pct)}</dd>
          <dt>Proven by the employer's own page or email</dt>
          <dd className="mono">{pct(all.strong_pct)}</dd>
        </dl>
        <p className="ov-caption">
          The last two are of the jobs it could apply to.
          {lesser > 0 &&
            ` ${lesser.toLocaleString()} of the ${all.confirmed.toLocaleString()} confirmed rest on less: the platform's answer (${all.evidence.medium.toLocaleString()}), or a weaker record such as a note or a quoted page (${all.evidence.weak.toLocaleString()}).`}
        </p>
        {ledger && (
          <p className="ov-caption">
            Applications shows {sent.toLocaleString()} sent in {week}
            {earlier > 0 ? `: these ${all.confirmed.toLocaleString()}, and ${earlier.toLocaleString()} first tried before them.` : "."}
            {onFile > 0 && ` All time, it also keeps ${onFile.toLocaleString()} with no send of AXIOM's behind them, such as an application the employer already had on file.`}
          </p>
        )}
        {small.length > 0 && <p className="ov-caption">Not judged, with fewer than 8 jobs: {small.map((p) => platformName(p.ats)).join(", ")}.</p>}
      </details>
    </Panel>
  );
}

/** Overview's right column: how the whole thing has performed, polled gently because it changes slowly. */
export function Results({ ledger }: { ledger: Success[] | null }) {
  const funnel = usePoll<Funnel>((signal) => api("/funnel", "GET", undefined, signal), 60000);
  return (
    <>
      <FunnelPanel data={funnel.data} error={funnel.error} retry={funnel.refresh} confirmed={ledger?.length ?? null} />
      <SuccessPanel ledger={ledger} />
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
