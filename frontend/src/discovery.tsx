// Settings › Job search: what AXIOM looks for and the employer boards it reads. The rules for applying to what it finds are under
// Applying (AutoApplySettings below).
import { useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { Actions, SaveBar, SwitchRow } from "./setup-kit";
import type { Board, BoardSettings, Snapshot, Source } from "./types";
import { ago, plural } from "./format";
import { Chips, SearchBox } from "./worktools";
import "./styles/settings.css";
import {
  Button,
  Check,
  ConfirmButton,
  Dialog,
  Empty,
  External,
  Input,
  Loading,
  Panel,
  Segmented,
  Select,
  Skeleton,
  Table,
} from "./ui/index";

// The page needs the settings and the boards, not the listings: one listing keeps the answer from carrying them all.
const BOARD = "/job-board?include_sources=true&page=1&size=1";
const KINDS = ["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "career_page"];

/** One form over /api/job-board/settings. The search rules and the apply rules are saved apart but are one object on the server. */
function useBoardForm(saved: string) {
  const { refresh } = useWorkspace();
  const board = usePoll<Board>((signal) => api(BOARD, "GET", undefined, signal), 120000);
  const { pending, run } = useAction();
  const [form, setForm] = useState<BoardSettings | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (board.data?.settings && !dirty) setForm(board.data.settings);
  }, [board.data, dirty]);
  const set = <K extends keyof BoardSettings>(key: K, value: BoardSettings[K]) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setDirty(true);
  };
  const save = () =>
    run(
      "save",
      async () => {
        // The full validated model, merged over the server's current values.
        await api("/job-board/settings", "PUT", { ...board.data!.settings, ...form });
        setDirty(false);
        await Promise.all([board.refresh(), refresh()]);
      },
      saved,
    );
  const discard = () => {
    setForm(board.data?.settings ?? null);
    setDirty(false);
  };
  return { board, form, set, dirty, save, discard, saving: pending === "save" };
}

function Gate({ board, form, children }: { board: ReturnType<typeof useBoardForm>["board"]; form: unknown; children: () => React.ReactNode }) {
  if (!board.data || !board.data.settings || !form)
    return board.error ? (
      <Empty
        title="Could not load these settings"
        hint={board.error.message}
        action={
          <Button size="sm" onClick={board.refresh}>
            Try again
          </Button>
        }
      />
    ) : (
      <Loading label="Loading…" />
    );
  return <>{children()}</>;
}

/** A setting whose name and effect are on the left and whose choice is on the right. */
function ChoiceRow<T extends string>({
  name,
  note,
  value,
  options,
  onChange,
}: {
  name: string;
  note: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="ax-switch-row st-choice">
      <span className="ax-row-main">
        <strong>{name}</strong>
        <small>{note}</small>
      </span>
      <Segmented label={name} value={value} options={options} onChange={onChange} />
    </div>
  );
}

export function DiscoverySettings() {
  const f = useBoardForm("Job search settings saved.");
  const { board, form } = f;
  return (
    <>
      <Gate board={board} form={form}>
        {() => {
          const b = board.data!;
          const search = b.search || { provider: "Web search", google_configured: false };
          return (
            <>
              <section className="st-group" aria-labelledby="st-look">
                <div className="st-head">
                  <div>
                    <h2 id="st-look">What AXIOM looks for</h2>
                    <p>It checks employers' job pages and searches the web on a schedule, then scores what it finds against your profile.</p>
                  </div>
                </div>
                <Panel>
                  <SwitchRow
                    label="Search automatically"
                    note="Check employer boards and search on a schedule."
                    checked={!!form!.enabled}
                    onChange={() => f.set("enabled", !form!.enabled)}
                  />
                  <SwitchRow
                    label="Early-career roles only"
                    note="Skip postings that show no new-grad or early-career signal."
                    checked={!!form!.early_career_only}
                    onChange={() => f.set("early_career_only", !form!.early_career_only)}
                  />
                  <SwitchRow
                    label="Search the web for new employers"
                    note={`${search.provider}.${search.google_configured ? "" : " Free search works now; add a Serper key under Connections for Google results."}`}
                    checked={!!form!.web_search}
                    onChange={() => f.set("web_search", !form!.web_search)}
                  />
                  <ChoiceRow
                    name="Check boards"
                    note="How often each employer board is read."
                    value={String(form!.interval_hours)}
                    options={[
                      { value: "1", label: "Hourly" },
                      { value: "2", label: "2 hours" },
                      { value: "3", label: "3 hours" },
                      { value: "6", label: "6 hours" },
                    ]}
                    onChange={(v) => f.set("interval_hours", Number(v) as BoardSettings["interval_hours"])}
                  />
                  <ChoiceRow
                    name="Posted within"
                    note="Older postings are left alone."
                    value={String(form!.max_age_hours)}
                    options={[
                      { value: "48", label: "48 hours" },
                      { value: "168", label: "7 days" },
                      { value: "720", label: "30 days" },
                    ]}
                    onChange={(v) => f.set("max_age_hours", Number(v) as BoardSettings["max_age_hours"])}
                  />
                  <ChoiceRow
                    name="Employer boards: posted within"
                    note="Greenhouse, Ashby, Lever and SmartRecruiters list only open postings, so older ones are usually still open."
                    value={String(form!.live_board_age_hours ?? "auto")}
                    options={[
                      { value: "auto", label: "Automatic" },
                      { value: "720", label: "30 days" },
                      { value: "1080", label: "45 days" },
                      { value: "1440", label: "60 days" },
                      { value: "2160", label: "90 days" },
                    ]}
                    onChange={(v) =>
                      f.set(
                        "live_board_age_hours",
                        v === "auto" ? null : (Number(v) as NonNullable<BoardSettings["live_board_age_hours"]>),
                      )
                    }
                  />
                </Panel>
              </section>
              <EmployerBoards board={b} reload={board.refresh} />
              <CustomSources />
            </>
          );
        }}
      </Gate>
      <SaveBar dirty={f.dirty} saving={f.saving} onSave={f.save} onDiscard={f.discard} label="Save job search settings" />
    </>
  );
}

/** Settings › Applying: what is applied to without asking. Saved with the same settings as the search (one object on the server). */
export function AutoApplySettings() {
  const f = useBoardForm("Automatic application settings saved.");
  const { board, form } = f;
  return (
    <>
      <Gate board={board} form={form}>
        {() => {
          const fitError =
            !Number.isFinite(form!.minimum_auto_fit) || form!.minimum_auto_fit < 0 || form!.minimum_auto_fit > 100
              ? "A number from 0 to 100."
              : undefined;
          const sizeError =
            !Number.isInteger(form!.review_size) || form!.review_size < 5 || form!.review_size > 100 ? "A whole number from 5 to 100." : undefined;
          return (
            <>
              <Panel>
                <div className="st-modes" role="radiogroup" aria-label="How jobs get queued">
                  {[
                    {
                      auto: false,
                      title: "Review a batch first",
                      line: "AXIOM gathers its best new matches into a batch. You look them over, untick any, and accept: only what you accept is queued. Nothing else starts by itself.",
                    },
                    {
                      auto: true,
                      title: "Apply automatically",
                      line: "AXIOM queues every good match by itself and sends each application once every required answer is checked, without asking you first, until it reaches your target.",
                    },
                  ].map((c) => (
                    <button
                      key={String(c.auto)}
                      type="button"
                      role="radio"
                      aria-checked={!!form!.auto_apply === c.auto}
                      className={"st-mode" + (!!form!.auto_apply === c.auto ? " is-on" : "")}
                      onClick={() => f.set("auto_apply", c.auto)}
                    >
                      <i aria-hidden="true" />
                      <span>
                        <strong>{c.title}</strong>
                        <small>{c.line}</small>
                      </span>
                    </button>
                  ))}
                </div>
                <div className="st-fields" style={{ marginTop: "var(--space-4)" }}>
                  {!form!.auto_apply && (
                    <Input
                      label="Jobs in a batch"
                      hint="5 to 100."
                      error={sizeError}
                      type="number"
                      min={5}
                      max={100}
                      value={Number.isFinite(form!.review_size) ? form!.review_size : ""}
                      onChange={(e) => f.set("review_size", Number(e.target.value))}
                    />
                  )}
                  <Input
                    label="How close a match"
                    hint={form!.auto_apply ? "0 to 100. AXIOM won't apply on its own to a job that scores lower." : "0 to 100. A job that scores lower isn't offered in a batch. You can still queue it yourself."}
                    error={fitError}
                    type="number"
                    min={0}
                    max={100}
                    value={Number.isFinite(form!.minimum_auto_fit) ? form!.minimum_auto_fit : ""}
                    onChange={(e) => f.set("minimum_auto_fit", Number(e.target.value))}
                  />
                </div>
              </Panel>
              <SaveBar
                dirty={f.dirty}
                saving={f.saving}
                disabled={!!fitError || !!sizeError}
                onSave={f.save}
                onDiscard={f.discard}
                label="Save"
              />
            </>
          );
        }}
      </Gate>
    </>
  );
}

// "Client error '404 Not Found' for url ..." is "404 Not Found"; the rest is cut to one line.
function shortError(error: string) {
  const text = (error || "").replace(/^Source failed: /, "");
  const status = text.match(/'(\d{3}[^']*)'/);
  if (status) return status[1];
  if (/could not be resolved/i.test(text)) return "Address not found";
  if (/CERTIFICATE_VERIFY_FAILED/.test(text)) return "Certificate error";
  return text.length > 90 ? text.slice(0, 90) + "…" : text;
}

function until(value: string | null | undefined, now: number) {
  const s = value ? (new Date(value).getTime() - now) / 1000 : NaN;
  if (Number.isNaN(s) || s <= 0) return "soon";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `in ${Math.round(s / 3600)}h`;
  return `in ${Math.round(s / 86400)}d`;
}

type BoardSource = Board["sources"][number];

// What the last check found, or why it could not read the board.
function resultOf(s: BoardSource, now: number) {
  const failed = s.error || s.scan?.state === "error";
  const checking = s.scan?.state === "queued" || s.scan?.state === "running";
  if (checking) return "Checking…";
  if (!s.enabled) return "Turned off";
  if (failed)
    return `${(s.scan?.error || shortError(s.error) || "Couldn't read this board").replace(/\s*\(\w*Error\)/, "")}${
      (s.health?.failures ?? 0) > 1 ? ` · ${s.health!.failures} failed checks in a row, next try ${until(s.next_run, now)}` : ""
    }`;
  return `${plural(s.scan?.matched ?? 0, "match", "matches")} · ${plural(s.scan?.checked ?? 0, "posting")} checked`;
}

function EmployerBoards({ board: b, reload }: { board: Board; reload: () => Promise<void> }) {
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [only, setOnly] = useState("all");
  const [adding, setAdding] = useState(false);
  const now = useNow(30000);
  const q = query.trim().toLowerCase();
  const sources = useMemo(() => (Array.isArray(b.sources) ? b.sources : []), [b.sources]);
  const interval = b.settings?.interval_hours ?? 2;
  const health = b.source_health;
  const errors = sources.filter((s) => s.error || s.scan?.state === "error");
  // Boards that failed their recent scans, worst first, so the dead ones can be turned off.
  const failing = (s: BoardSource) => s.enabled && (s.health?.failures ?? 0) > 0;
  const shown = sources
    .filter((s) => (only !== "failing" || failing(s)) && (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)))
    .sort((a, c) => (only === "failing" ? (c.health?.failures ?? 0) - (a.health?.failures ?? 0) : 0));
  const paging = usePagination(shown, q + only, 10);
  const off = (s: BoardSource, enabled: boolean) =>
    run(
      "t" + s.id,
      async () => {
        await api("/sources/" + s.id, "PUT", {
          name: s.name,
          kind: s.kind,
          url: s.url,
          cron: s.cron,
          enabled,
          auto_queue: s.auto_queue,
        });
        await reload();
      },
      enabled ? "Board turned on." : "Board turned off.",
    );
  return (
    <section className="st-group" aria-labelledby="st-boards">
      <div className="st-head">
        <div>
          <h2 id="st-boards">Employer boards · {(b.source_count ?? sources.length).toLocaleString()}</h2>
          <p>
            Checked every {interval} {interval === 1 ? "hour" : "hours"}
            {health?.failing
              ? `. ${health.failing.toLocaleString()} could not be read lately${health.dead ? `, ${health.dead.toLocaleString()} for three checks in a row` : ""}.`
              : errors.length
                ? `. ${errors.length} could not be read last time.`
                : "."}
          </p>
        </div>
        <div className="st-head-end">
          <Button size="sm" icon={<Plus size={15} aria-hidden="true" />} onClick={() => setAdding(true)}>
            Add a board
          </Button>
          <ConfirmButton
            label="Check every board now"
            confirm="Start search"
            variant="secondary"
            note="Queues an immediate check of every enabled board."
            pending={pending === "scan"}
            onConfirm={() =>
              run(
                "scan",
                async () => {
                  const r = await api<{ queued_sources: number }>("/job-board/scrape", "POST");
                  await reload();
                  return r;
                },
                (r) => (r ? `Checking ${plural(r.queued_sources ?? 0, "board")}.` : undefined),
              )
            }
          />
        </div>
      </div>
      <div className="st-tools">
        <SearchBox label="Search employer boards" placeholder="Search boards" value={query} onChange={setQuery} />
        {!!health?.failing && (
          <Chips
            label="Show boards"
            value={only}
            onChange={setOnly}
            items={[
              { id: "all", label: "All", count: b.source_count ?? sources.length },
              { id: "failing", label: "Failing", count: health.failing },
            ]}
          />
        )}
      </div>
      <Table
        label="Employer boards"
        rows={paging.items}
        rowKey={(s) => s.id}
        minWidth={340}
        footer={shown.length > 10 ? <Pagination paging={paging} label="Board pages" sizes={false} /> : undefined}
        empty={
          <Empty
            title={q ? "No boards match" : "No employer boards yet"}
            hint={q ? "Try a shorter search." : "Turn on automatic search and save to load the built-in catalog, or add a board."}
            action={
              q ? undefined : (
                <Button size="sm" onClick={() => setAdding(true)}>
                  Add a board
                </Button>
              )
            }
          />
        }
        columns={[
          {
            key: "board",
            header: "Board",
            truncate: true,
            width: 260,
            cell: (s) => (
              <>
                <strong>{s.name}</strong> <External href={s.url}>{(s.kind || "").replace("_", " ")}</External>
                <small className="st-sub">{resultOf(s, now)}</small>
              </>
            ),
          },
          { key: "last", header: "Last check", hideBelow: 860, cell: (s) => ago(s.last_run, now) },
          {
            key: "result",
            header: "Result",
            truncate: true,
            width: 360,
            hideBelow: 600,
            cell: (s) => {
              const text = resultOf(s, now);
              return <span title={text}>{text}</span>;
            },
          },
          {
            key: "act",
            header: <span className="sr-only">Actions</span>,
            pin: true,
            cell: (s) =>
              (failing(s) || !s.enabled) && s.kind !== "web_search" ? (
                <Button size="sm" disabled={pending !== null} onClick={() => off(s, !s.enabled)}>
                  {s.enabled ? "Turn off" : "Turn on"}
                </Button>
              ) : null,
          },
        ]}
      />
      <AddBoard
        open={adding}
        onClose={() => setAdding(false)}
        pending={pending === "add"}
        onAdd={async (body) => {
          let added = false;
          await run(
            "add",
            async () => {
              await api("/job-board/sources", "POST", { ...body, enabled: true, auto_queue: false, cron: "0 */2 * * *" });
              added = true;
              setAdding(false);
              await reload();
            },
            "Employer board added.",
          );
          return added;
        }}
      />
    </section>
  );
}

function AddBoard({
  open,
  onClose,
  pending,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  pending: boolean;
  onAdd: (body: { name: string; url: string; kind: string }) => Promise<boolean>;
}) {
  const [name, setName] = useDraft("board-add-name", "");
  const [url, setUrl] = useDraft("board-add-url", "");
  const [kind, setKind] = useDraft("board-add-kind", "greenhouse");
  const form = "add-board";
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add an employer board"
      sub="A company's own list of open roles. AXIOM reads it on the schedule above."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form={form} loading={pending}>
            Add board
          </Button>
        </>
      }
    >
      <form
        id={form}
        className="st-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await onAdd({ name: name.trim(), url: url.trim(), kind })) {
            setName("");
            setUrl("");
          }
        }}
      >
        <Input label="Employer" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Company name" data-autofocus />
        <Select label="Platform" value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k.replace("_", " ")}
            </option>
          ))}
        </Select>
        <Input
          label="Board URL"
          hint="The company's board, not a single job."
          required
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://jobs.ashbyhq.com/company"
        />
      </form>
    </Dialog>
  );
}

// Advanced: hand-configured sources outside the managed employer catalog.
function CustomSources() {
  const [open, setOpen] = useState(false);
  return (
    <details className="ax-fold st-adv" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <strong>Custom sources</strong>
        <span>Your own sources, each with its own schedule</span>
      </summary>
      <div className="st-adv-body">{open && <CustomSourceList />}</div>
    </details>
  );
}

function CustomSourceList() {
  const all = usePoll<Snapshot>((signal) => api("/snapshot?include_sources=true", "GET", undefined, signal), 120000);
  const managed = usePoll<{ sources: Source[] }>((signal) => api(BOARD, "GET", undefined, signal), 120000);
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("src-name", "");
  const [url, setUrl] = useDraft("src-url", "");
  const [kind, setKind] = useDraft("src-kind", "greenhouse");
  const [cron, setCron] = useDraft("src-cron", "0 */3 * * *");
  const [auto, setAuto] = useDraft("src-auto", false);
  const managedIds = useMemo(() => new Set((managed.data?.sources || []).map((s) => s.id)), [managed.data]);
  const q = query.trim().toLowerCase();
  const sources = (all.data?.sources || []).filter(
    (s) => !managedIds.has(s.id) && (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)),
  );
  const paging = usePagination(sources, q, 10);
  const reload = async () => {
    await Promise.all([all.refresh(), managed.refresh()]);
  };
  if (!all.data || !managed.data)
    return all.error || managed.error ? (
      <Empty
        title="Could not load the sources"
        hint={(all.error || managed.error)?.message}
        action={
          <Button size="sm" onClick={reload}>
            Try again
          </Button>
        }
      />
    ) : (
      <Skeleton rows={3} mono={false} />
    );
  const put = (s: Source, patch: Partial<Source>) =>
    api("/sources/" + s.id, "PUT", {
      name: s.name,
      kind: s.kind,
      url: s.url,
      cron: s.cron,
      enabled: s.enabled,
      auto_queue: s.auto_queue,
      ...patch,
    });
  return (
    <div className="ax-stack">
      <form
        className="st-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "add",
            async () => {
              await api("/sources", "POST", {
                name: name.trim(),
                url: url.trim(),
                kind,
                cron: cron.trim(),
                enabled: true,
                auto_queue: auto,
              });
              setName("");
              setUrl("");
              await reload();
            },
            "Source added.",
          );
        }}
      >
        <div className="st-fields">
          <Input label="Name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          <Select label="Platform" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </Select>
          <Input label="Board URL" hint="The company's board, not a single job." required type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Input label="Schedule" hint="A cron time. 0 */3 * * * is every three hours." required value={cron} onChange={(e) => setCron(e.target.value)} />
        </div>
        <Check checked={auto} onChange={setAuto} label="Rank new jobs and queue the eligible ones" />
        <Actions start>
          <Button type="submit" loading={pending === "add"} disabled={pending !== null && pending !== "add"}>
            Add source
          </Button>
        </Actions>
      </form>
      <div className="st-tools">
        <SearchBox label="Search custom sources" placeholder="Search" value={query} onChange={setQuery} />
      </div>
      {sources.length ? (
        <>
          <ul className="ax-rows">
            {paging.items.map((s) => (
              <li className="ax-row" key={s.id}>
                <span className="ax-row-main">
                  <strong>{s.name}</strong>
                  <small>
                    <External href={s.url}>{(s.kind || "").replace("_", " ")}</External> · <span className="mono">{s.cron}</span> · last
                    run {ago(s.last_run)} · {s.found ?? 0} new{s.error ? ` · ${s.error}` : ""}
                  </small>
                </span>
                <span className="ax-row-end">
                  <Button
                    size="sm"
                    disabled={pending !== null}
                    onClick={() =>
                      run(
                        "t" + s.id,
                        async () => {
                          await put(s, { enabled: !s.enabled });
                          await reload();
                        },
                        s.enabled ? "Source paused." : "Source enabled.",
                      )
                    }
                  >
                    {s.enabled ? "Pause" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    disabled={pending !== null || !s.enabled}
                    onClick={() => run("d" + s.id, () => api(`/sources/${s.id}/discover`, "POST"), "Check queued.")}
                  >
                    Check now
                  </Button>
                  <ConfirmButton
                    label="Remove"
                    confirm="Remove source"
                    pending={pending === "r" + s.id}
                    onConfirm={() =>
                      run(
                        "r" + s.id,
                        async () => {
                          await api("/sources/" + s.id, "DELETE");
                          await reload();
                        },
                        "Source removed.",
                      )
                    }
                  />
                </span>
              </li>
            ))}
          </ul>
          <Pagination paging={paging} label="Source pages" sizes={false} />
        </>
      ) : (
        <p className="ax-hint">{q ? "No custom sources match." : "No custom sources. The employer boards above cover the search."}</p>
      )}
    </div>
  );
}
