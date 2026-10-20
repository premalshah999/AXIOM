import { useEffect, useMemo, useState } from "react";
import { NewGradSweep } from "./features";
import { RefreshCw, Search } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import type { Board, BoardSettings, Listing } from "./types";
import {
  ago,
  Confirm,
  Empty,
  ErrorLine,
  External,
  Field,
  Loading,
  plural,
  StatePill,
  stamp,
} from "./ui";

// A listing in any of these states is already owned or protected.
const LOCKED = new Set([
  "paused",
  "queued",
  "running",
  "submitting",
  "confirmed",
  "submission_unknown",
  "already_applied",
  "archived",
]);

function fitClass(score: number | null, minimum: number) {
  if (score == null) return "fit none";
  return score >= minimum ? "fit good" : "fit low";
}

export function DiscoveredJobs() {
  const {
    snapshot,
    route,
    go,
    refresh: refreshSnapshot,
    openJob,
  } = useWorkspace();
  const board = usePoll<Board>(
    (signal) =>
      api("/job-board?include_sources=false", "GET", undefined, signal),
    20000,
  );
  const { pending, run } = useAction();
  const now = useNow(30000);
  const [query, setQuery] = useDraft("board-query", "");
  const [ats, setAts] = useDraft("board-ats", "all");
  const [level, setLevel] = useDraft("board-level", "all");
  const [selected, setSelected] = useState<string[]>([]);
  const fit = route.filter || "all";
  const settings = board.data?.settings;
  const minimum = settings?.minimum_auto_fit ?? 70;
  const q = query.trim().toLowerCase();
  const jobs = useMemo(
    () =>
      (board.data?.jobs || []).filter((j) => {
        if (
          q &&
          !`${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q)
        )
          return false;
        if (ats !== "all" && j.ats !== ats) return false;
        if (level !== "all" && j.career_level !== level) return false;
        if (fit === "eligible")
          return j.status === "ready" && (j.score ?? 0) >= minimum;
        if (fit === "unranked") return j.score == null;
        if (fit === "below")
          return (
            j.status === "skipped" || (j.score != null && j.score < minimum)
          );
        return true;
      }),
    [board.data, q, ats, level, fit, minimum],
  );
  const paging = usePagination(jobs, [q, ats, level, fit].join("|"), 25);
  const selectable = paging.items.filter((j) => !LOCKED.has(j.status));
  const chosen = selected.filter((id) =>
    (board.data?.jobs || []).some((j) => j.id === id && !LOCKED.has(j.status)),
  );
  const blockers = [
    !snapshot.config.workers_enabled && "application workers are disabled",
    !snapshot.control.auto_submit &&
      "automatic submission is off (Setup › System)",
    snapshot.control.paused &&
      "new applications are paused (resume them on Batch)",
  ].filter(Boolean) as string[];
  const canApply = blockers.length === 0;

  const apply = (ids: string[]) =>
    run(
      "apply",
      async () => {
        const result = await api<{
          queued: { job_id: string }[];
          skipped: { job_id: string; reason: string }[];
        }>("/job-board/apply", "POST", { job_ids: ids.slice(0, 50) });
        setSelected([]);
        await Promise.all([board.refresh(), refreshSnapshot()]);
        return result;
      },
      (r) =>
        r &&
        `${plural(r.queued.length, "application")} queued.` +
          (r.skipped.length
            ? ` ${r.skipped.length} skipped: ${r.skipped[0].reason}${r.skipped.length > 1 ? " (and others)" : ""}.`
            : ""),
    );

  const scan = () =>
    run(
      "scan",
      async () => {
        const r = await api<{ queued_sources: number; total_sources: number }>(
          "/job-board/scrape",
          "POST",
        );
        await board.refresh();
        return r;
      },
      (r) =>
        r &&
        `Checking ${plural(r.queued_sources, "board")}. New listings appear here automatically.`,
    );

  if (!board.data)
    return board.error ? (
      <ErrorLine error={board.error} retry={board.refresh} />
    ) : (
      <Loading label="Loading fresh listings…" />
    );
  const b = board.data;
  const platforms = [...new Set(b.jobs.map((j) => j.ats))].sort();
  const fits = [
    ["all", "Any fit"],
    ["eligible", "Eligible"],
    ["unranked", "Not ranked"],
    ["below", "Below fit"],
  ];
  return (
    <section aria-label="Discovered jobs">
      <div className="board-status">
        <p>
          <strong>{plural(b.jobs.length, "fresh US listing")}</strong> posted in
          the last {b.max_age_hours === 720 ? "30 days" : b.max_age_hours === 168 ? "7 days" : "48 hours"}
          {b.scanning > 0 ? (
            <> · checking {plural(b.scanning, "board")} now</>
          ) : (
            b.last_requested_at && (
              <> · last search {ago(b.last_requested_at, now)}</>
            )
          )}
          {settings?.auto_apply
            ? ". The batch applies to eligible matches automatically."
            : ". Automatic applications are off; apply manually or start a batch."}
        </p>
        <div className="board-actions">
          <button
            className="btn sm"
            disabled={pending !== null || b.scanning > 0 || !settings?.enabled}
            onClick={scan}
          >
            <RefreshCw
              size={14}
              aria-hidden="true"
              className={b.scanning ? "spin" : ""}
            />
            {b.scanning ? "Discovery running" : "Search now"}
          </button>
          <button
            className="link sm"
            onClick={() => go({ view: "setup", tab: "discovery" })}
          >
            Discovery settings
          </button>
        </div>
      </div>
      <NewGradSweep onFinished={board.refresh} />
      {!settings?.enabled && (
        <p className="inline-warn">
          Discovery is off, so no new listings arrive.{" "}
          <button
            className="link"
            onClick={() => go({ view: "setup", tab: "discovery" })}
          >
            Turn it on in Setup
          </button>
        </p>
      )}
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search fresh jobs"
            placeholder="Role, company or city"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="Platform"
          value={ats}
          onChange={(e) => setAts(e.target.value)}
        >
          <option value="all">All platforms</option>
          {platforms.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          aria-label="Career level"
          value={level}
          onChange={(e) => setLevel(e.target.value)}
        >
          <option value="all">All levels</option>
          <option>New grad</option>
          <option>Early career</option>
          <option>Experience unspecified</option>
        </select>
        <div className="chips" role="group" aria-label="Filter by fit">
          {fits.map(([id, label]) => (
            <button
              key={id}
              className="chip"
              aria-pressed={fit === id}
              onClick={() =>
                go({
                  view: "jobs",
                  tab: "discovered",
                  filter: id === "all" ? undefined : id,
                })
              }
            >
              {label}
            </button>
          ))}
        </div>
        <button
          className="btn primary sm toolbar-end"
          disabled={!chosen.length || !canApply || pending !== null}
          onClick={() => apply(chosen)}
        >
          {pending === "apply"
            ? "Queuing…"
            : `Apply to selected (${chosen.length})`}
        </button>
      </div>
      {!canApply && (
        <p className="muted sm apply-why">
          Manual apply is unavailable: {blockers.join("; ")}.
        </p>
      )}
      {jobs.length ? (
        <>
          <div className="table-wrap">
            <table className="table board-table">
              <thead>
                <tr>
                  <th scope="col" className="cell-check">
                    <input
                      type="checkbox"
                      aria-label="Select eligible jobs on this page"
                      disabled={!selectable.length}
                      checked={
                        !!selectable.length &&
                        selectable.every((j) => selected.includes(j.id))
                      }
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [
                                ...new Set([
                                  ...selected,
                                  ...selectable.map((j) => j.id),
                                ]),
                              ].slice(0, 50)
                            : selected.filter(
                                (id) => !selectable.some((j) => j.id === id),
                              ),
                        )
                      }
                    />
                  </th>
                  <th scope="col">Job</th>
                  <th scope="col">Posted</th>
                  <th scope="col" className="num">
                    Fit
                  </th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {paging.items.map((j) => (
                  <BoardRow
                    key={j.id}
                    job={j}
                    minimum={minimum}
                    now={now}
                    checked={chosen.includes(j.id)}
                    limitReached={chosen.length >= 50}
                    toggle={(on) =>
                      setSelected(
                        on
                          ? [...selected, j.id]
                          : selected.filter((id) => id !== j.id),
                      )
                    }
                    canApply={canApply}
                    applying={pending === "apply"}
                    apply={() => apply([j.id])}
                    open={() => openJob(j.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Listing pages" />
        </>
      ) : (
        <Empty
          title={
            b.jobs.length
              ? "No listings match these filters"
              : b.scanning
                ? "Looking for fresh listings"
                : "No fresh listings right now"
          }
        >
          {b.jobs.length
            ? "Clear a filter to see more."
            : b.scanning
              ? "Employer boards are being checked. Listings appear as they arrive."
              : `Only US roles posted in the last ${b.max_age_hours === 720 ? "30 days" : b.max_age_hours === 168 ? "7 days" : "48 hours"} with a known date qualify. The next scheduled search checks again.`}
        </Empty>
      )}
    </section>
  );
}

function BoardRow({
  job: j,
  minimum,
  now,
  checked,
  limitReached,
  toggle,
  canApply,
  applying,
  apply,
  open,
}: {
  job: Listing;
  minimum: number;
  now: number;
  checked: boolean;
  limitReached: boolean;
  toggle: (on: boolean) => void;
  canApply: boolean;
  applying: boolean;
  apply: () => void;
  open: () => void;
}) {
  const locked = LOCKED.has(j.status);
  const runState = [
    "queued",
    "running",
    "submitting",
    "confirmed",
    "submission_unknown",
    "already_applied",
  ].includes(j.status);
  return (
    <tr>
      <td className="cell-check">
        <input
          type="checkbox"
          aria-label={`Select ${j.title} at ${j.company}`}
          disabled={locked || (!checked && limitReached)}
          checked={checked}
          onChange={(e) => toggle(e.target.checked)}
        />
      </td>
      <td className="cell-job">
        <button className="link job-open" onClick={open}>
          <strong>{j.company}</strong>
          <span>{j.title}</span>
        </button>
        <small className="muted">
          {[j.location || "United States", j.career_level, j.ats]
            .filter(Boolean)
            .join(" · ")}
        </small>
      </td>
      <td data-label="Posted">
        <time
          dateTime={j.posted_at}
          title={`${j.date_basis || "posted"} ${stamp(j.posted_at)}`}
        >
          {ago(j.posted_at, now)}
        </time>
      </td>
      <td className="num" data-label="Fit">
        <span
          className={fitClass(j.score, minimum)}
          title={j.reason || j.match_reason}
        >
          {j.score == null ? "—" : j.score}
        </span>
      </td>
      <td data-label="Status">
        <StatePill state={runState ? j.status : "job:" + j.status} />
      </td>
      <td className="cell-actions">
        {!locked && (
          <div className="row-actions">
            <button
              className="btn sm"
              disabled={!canApply || applying}
              onClick={apply}
            >
              Apply
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

// ---------- Setup › Discovery ----------
export function DiscoverySettings() {
  const { refresh } = useWorkspace();
  const board = usePoll<Board>(
    (signal) =>
      api("/job-board?include_sources=true", "GET", undefined, signal),
    60000,
  );
  const { pending, run } = useAction();
  const [form, setForm] = useState<BoardSettings | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (board.data && !dirty) setForm(board.data.settings);
  }, [board.data, dirty]);
  if (!board.data || !form)
    return board.error ? (
      <ErrorLine error={board.error} retry={board.refresh} />
    ) : (
      <Loading label="Loading discovery settings…" />
    );
  const b = board.data;
  const set = <K extends keyof BoardSettings>(
    key: K,
    value: BoardSettings[K],
  ) => {
    setForm({ ...form, [key]: value });
    setDirty(true);
  };
  return (
    <>
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "save",
            async () => {
              // The full validated model, merged over the server's current values.
              await api("/job-board/settings", "PUT", {
                ...b.settings,
                ...form,
              });
              setDirty(false);
              await Promise.all([board.refresh(), refresh()]);
            },
            "Discovery settings saved.",
          );
        }}
      >
        <header className="panel-head">
          <h2>What to look for</h2>
          <p className="muted">
            Discovery finds recent US roles; ranking scores each against your
            profile and resumes in batches.
          </p>
        </header>
        <div className="form-grid">
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            <span>
              <strong>Automatic discovery</strong>
              <small>Check employer boards and search on a schedule.</small>
            </span>
          </label>
          <Field label="Check boards">
            <select
              value={form.interval_hours}
              onChange={(e) =>
                set(
                  "interval_hours",
                  Number(e.target.value) as BoardSettings["interval_hours"],
                )
              }
            >
              {[1, 2, 3, 6].map((n) => (
                <option key={n} value={n}>
                  Every {n === 1 ? "hour" : `${n} hours`}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Posted within">
            <select
              value={form.max_age_hours}
              onChange={(e) =>
                set(
                  "max_age_hours",
                  Number(e.target.value) as BoardSettings["max_age_hours"],
                )
              }
            >
              <option value={48}>48 hours</option>
              <option value={168}>7 days</option>
              <option value={720}>30 days</option>
            </select>
          </Field>
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.early_career_only}
              onChange={(e) => set("early_career_only", e.target.checked)}
            />
            <span>
              <strong>Early career only</strong>
              <small>
                Require new-grad or early-career evidence in the posting.
              </small>
            </span>
          </label>
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.web_search}
              onChange={(e) => set("web_search", e.target.checked)}
            />
            <span>
              <strong>Search the web for new employers</strong>
              <small>
                {b.search.provider}.{" "}
                {!b.search.google_configured &&
                  "Add SERPER_API_KEY on the server for Google results; free search works now."}
              </small>
            </span>
          </label>
        </div>
        <header className="panel-head sub">
          <h2>Automatic applications</h2>
        </header>
        <div className="form-grid">
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.auto_apply}
              onChange={(e) => set("auto_apply", e.target.checked)}
            />
            <span>
              <strong>Apply to eligible matches automatically</strong>
              <small>
                Saving with this on starts a batch if none is active. An active
                batch is never restarted.
              </small>
            </span>
          </label>
          <Field
            label="Minimum fit to apply"
            hint="0–100. Jobs ranked below this are never applied to automatically."
          >
            <input
              type="number"
              min={0}
              max={100}
              value={form.minimum_auto_fit}
              onChange={(e) => set("minimum_auto_fit", Number(e.target.value))}
            />
          </Field>
          <Field
            label="Default batch target"
            hint="Used when a new batch starts. 1–500."
          >
            <input
              type="number"
              min={1}
              max={500}
              value={form.batch_target}
              onChange={(e) => set("batch_target", Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="form-actions">
          {dirty && <span className="muted sm">Unsaved changes</span>}
          {dirty && (
            <button
              type="button"
              className="btn quiet"
              onClick={() => {
                setForm(b.settings);
                setDirty(false);
              }}
            >
              Discard
            </button>
          )}
          <button className="btn primary" disabled={!dirty || pending !== null}>
            {pending === "save" ? "Saving…" : "Save discovery settings"}
          </button>
        </div>
      </form>
      <EmployerBoards board={b} reload={board.refresh} />
    </>
  );
}

function EmployerBoards({
  board: b,
  reload,
}: {
  board: Board;
  reload: () => Promise<void>;
}) {
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("board-add-name", "");
  const [url, setUrl] = useDraft("board-add-url", "");
  const [kind, setKind] = useDraft("board-add-kind", "greenhouse");
  const now = useNow(30000);
  const q = query.trim().toLowerCase();
  const errors = b.sources.filter((s) => s.error || s.scan?.state === "error");
  const shown = b.sources.filter(
    (s) => !q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q),
  );
  const paging = usePagination(shown, q, 10);
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Employer boards</h2>
        <p className="muted">
          {plural(b.source_count, "board")} checked every{" "}
          {b.settings.interval_hours}{" "}
          {b.settings.interval_hours === 1 ? "hour" : "hours"}
          {errors.length
            ? ` · ${errors.length} couldn't be read last time`
            : ""}
          .
        </p>
      </header>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "add",
            async () => {
              await api("/job-board/sources", "POST", {
                name: name.trim(),
                url: url.trim(),
                kind,
                enabled: true,
                auto_queue: false,
                cron: "0 */2 * * *",
              });
              setName("");
              setUrl("");
              await reload();
            },
            "Employer board added.",
          );
        }}
      >
        <Field label="Employer">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Company name"
          />
        </Field>
        <Field label="Platform">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {[
              "greenhouse",
              "lever",
              "ashby",
              "smartrecruiters",
              "workday",
              "career_page",
            ].map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Board URL">
          <input
            required
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://jobs.ashbyhq.com/company"
          />
        </Field>
        <button className="btn" disabled={pending !== null}>
          {pending === "add" ? "Adding…" : "Add board"}
        </button>
      </form>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search employer boards"
            placeholder="Search boards"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      {shown.length ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Board</th>
                  <th scope="col">Last check</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {paging.items.map((s) => {
                  const failed = s.error || s.scan?.state === "error";
                  const checking =
                    s.scan?.state === "queued" || s.scan?.state === "running";
                  return (
                    <tr key={s.id}>
                      <td className="cell-job">
                        <strong>{s.name}</strong>
                        <External href={s.url} className="muted sm">
                          {s.kind.replace("_", " ")}
                        </External>
                      </td>
                      <td data-label="Last check">{ago(s.last_run, now)}</td>
                      <td data-label="Result">
                        {checking ? (
                          <span className="muted">Checking…</span>
                        ) : failed ? (
                          <span className="warn-text">
                            {s.scan?.error ||
                              s.error ||
                              "Couldn't read this board"}
                          </span>
                        ) : (
                          <span>
                            {s.scan?.matched ?? 0} matches ·{" "}
                            {s.scan?.checked ?? 0} postings checked
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Board pages" />
        </>
      ) : (
        <Empty title={q ? "No boards match" : "No employer boards yet"}>
          {q
            ? null
            : "Turn on discovery and save to load the built-in catalog, or add a board above."}
        </Empty>
      )}
      {errors.length > 0 && (
        <p className="muted sm">
          Boards that fail keep their retry cooldown; they don't block discovery
          from other sources.
        </p>
      )}
      <Confirm
        label="Check every board now"
        confirm="Start search"
        danger={false}
        pending={pending === "scan"}
        note="Queues an immediate check of every enabled board."
        onConfirm={() =>
          run(
            "scan",
            async () => {
              const r = await api<{ queued_sources: number }>(
                "/job-board/scrape",
                "POST",
              );
              await reload();
              return r;
            },
            (r) => r && `Checking ${plural(r.queued_sources, "board")}.`,
          )
        }
      />
    </section>
  );
}
