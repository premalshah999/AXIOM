// Setup › Discovery: what the job board looks for, and the employer boards it reads.
import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { Actions, FormGrid, SaveBar, SwitchRow } from "./setup-kit";
import type { Board, BoardSettings } from "./types";
import { ago, plural } from "./format";
import { SearchBox } from "./worktools";
import { Button, ConfirmButton, Empty, External, Input, Loading, Panel, Select, Table } from "./ui/index";

// The page needs the settings and the boards, not the listings: one listing keeps the answer from carrying them all.
const BOARD = "/job-board?include_sources=true&page=1&size=1";

export function DiscoverySettings() {
  const { refresh } = useWorkspace();
  const board = usePoll<Board>((signal) => api(BOARD, "GET", undefined, signal), 120000);
  const { pending, run } = useAction();
  const [form, setForm] = useState<BoardSettings | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (board.data?.settings && !dirty) setForm(board.data.settings);
  }, [board.data, dirty]);
  if (!board.data || !board.data.settings || !form)
    return (
      <Panel title="What to look for">
        {board.error ? (
          <Empty
            title="Could not load discovery settings"
            hint={board.error.message}
            action={
              <Button size="sm" onClick={board.refresh}>
                Try again
              </Button>
            }
          />
        ) : (
          <Loading label="Loading discovery settings…" />
        )}
      </Panel>
    );
  const b = board.data;
  const set = <K extends keyof BoardSettings>(key: K, value: BoardSettings[K]) => {
    setForm({ ...form, [key]: value });
    setDirty(true);
  };
  const fitError =
    !Number.isFinite(form.minimum_auto_fit) || form.minimum_auto_fit < 0 || form.minimum_auto_fit > 100
      ? "A number from 0 to 100."
      : undefined;
  const targetError =
    !Number.isInteger(form.batch_target) || form.batch_target < 1 || form.batch_target > 500
      ? "A whole number from 1 to 500."
      : undefined;
  const save = () =>
    run(
      "save",
      async () => {
        // The full validated model, merged over the server's current values.
        await api("/job-board/settings", "PUT", { ...b.settings, ...form });
        setDirty(false);
        await Promise.all([board.refresh(), refresh()]);
      },
      "Discovery settings saved.",
    );
  const search = b.search || { provider: "Web search", google_configured: false };
  return (
    <>
      <form
        className="ax-sections"
        onSubmit={(e) => {
          e.preventDefault();
          if (!fitError && !targetError) save();
        }}
      >
        <Panel title="What to look for">
          <SwitchRow
            label="Automatic discovery"
            note="Check employer boards and search on a schedule."
            checked={!!form.enabled}
            onChange={() => set("enabled", !form.enabled)}
          />
          <SwitchRow
            label="Early career only"
            note="Require new-grad or early-career evidence in the posting."
            checked={!!form.early_career_only}
            onChange={() => set("early_career_only", !form.early_career_only)}
          />
          <SwitchRow
            label="Search the web for new employers"
            note={`${search.provider}.${search.google_configured ? "" : " Add a Serper key in Settings › Connections for Google results; free search works now."}`}
            checked={!!form.web_search}
            onChange={() => set("web_search", !form.web_search)}
          />
          <FormGrid>
              <Select
                label="Check boards"
                value={form.interval_hours}
                onChange={(e) => set("interval_hours", Number(e.target.value) as BoardSettings["interval_hours"])}
              >
                {[1, 2, 3, 6].map((n) => (
                  <option key={n} value={n}>
                    Every {n === 1 ? "hour" : `${n} hours`}
                  </option>
                ))}
              </Select>
              <Select
                label="Posted within"
                value={form.max_age_hours}
                onChange={(e) => set("max_age_hours", Number(e.target.value) as BoardSettings["max_age_hours"])}
              >
                <option value={48}>48 hours</option>
                <option value={168}>7 days</option>
                <option value={720}>30 days</option>
              </Select>
            </FormGrid>
        </Panel>
        <Panel title="Automatic applications">
          <SwitchRow
            label="Apply to eligible matches automatically"
            note="Saving with this on starts a batch if none is active. An active batch is never restarted."
            checked={!!form.auto_apply}
            onChange={() => set("auto_apply", !form.auto_apply)}
          />
          <FormGrid>
              <Input
                label="Minimum fit to apply"
                hint="0–100. Jobs ranked below this are never applied to automatically."
                error={fitError}
                type="number"
                min={0}
                max={100}
                value={Number.isFinite(form.minimum_auto_fit) ? form.minimum_auto_fit : ""}
                onChange={(e) => set("minimum_auto_fit", Number(e.target.value))}
              />
              <Input
                label="Default batch target"
                hint="Used when a new batch starts. 1–500."
                error={targetError}
                type="number"
                min={1}
                max={500}
                value={Number.isFinite(form.batch_target) ? form.batch_target : ""}
                onChange={(e) => set("batch_target", Number(e.target.value))}
              />
            </FormGrid>
        </Panel>
        <SaveBar
          dirty={dirty}
          saving={pending === "save"}
          disabled={!!fitError || !!targetError}
          onSave={save}
          onDiscard={() => {
            setForm(b.settings);
            setDirty(false);
          }}
          label="Save discovery settings"
        />
      </form>
      <EmployerBoards board={b} reload={board.refresh} />
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

function EmployerBoards({ board: b, reload }: { board: Board; reload: () => Promise<void> }) {
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("board-add-name", "");
  const [url, setUrl] = useDraft("board-add-url", "");
  const [kind, setKind] = useDraft("board-add-kind", "greenhouse");
  const now = useNow(30000);
  const q = query.trim().toLowerCase();
  const [failingOnly, setFailingOnly] = useState(false);
  const sources = Array.isArray(b.sources) ? b.sources : [];
  const interval = b.settings?.interval_hours ?? 2;
  const errors = sources.filter((s) => s.error || s.scan?.state === "error");
  // Boards that failed their recent scans, worst first, so the dead ones can be turned off.
  const failing = (s: BoardSource) => s.enabled && (s.health?.failures ?? 0) > 0;
  const shown = sources
    .filter((s) => (!failingOnly || failing(s)) && (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)))
    .sort((a, c) => (failingOnly ? (c.health?.failures ?? 0) - (a.health?.failures ?? 0) : 0));
  const health = b.source_health;
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
  const paging = usePagination(shown, q + failingOnly, 10);
  return (
    <Panel
      title={`Employer boards · ${(b.source_count ?? sources.length).toLocaleString()}`}
      actions={
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
      }
    >
      <div className="ax-stack">
      <p className="ax-hint">
        Checked every {interval} {interval === 1 ? "hour" : "hours"}
        {health
          ? health.failing
            ? `, ${health.failing} failing${health.dead ? ` (${health.dead} for 3 or more checks in a row)` : ""}`
            : ""
          : errors.length
            ? `, ${errors.length} couldn't be read last time`
            : ""}
        .
      </p>
      <form
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
        <FormGrid>
          <Input
            label="Employer"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Company name"
          />
          <Select label="Platform" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "career_page"].map((k) => (
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
        </FormGrid>
        <Actions>
          <Button type="submit" loading={pending === "add"} disabled={pending !== null && pending !== "add"}>
            Add board
          </Button>
        </Actions>
      </form>
      <div className="ax-stack">
        <div className="ax-toolbar">
          <SearchBox label="Search employer boards" placeholder="Search boards" value={query} onChange={setQuery} />
          {!!health?.failing && (
            <Button
              size="sm"
              variant={failingOnly ? "primary" : "secondary"}
              aria-pressed={failingOnly}
              onClick={() => setFailingOnly(!failingOnly)}
            >
              Failing · {health.failing}
            </Button>
          )}
        </div>
        <Table
          label="Employer boards"
          rows={paging.items}
          rowKey={(s) => s.id}
          minWidth={720}
          empty={
            <Empty
              title={q ? "No boards match" : "No employer boards yet"}
              hint={q ? undefined : "Turn on discovery and save to load the built-in catalog, or add a board above."}
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
                  <strong>{s.name}</strong>{" "}
                  <External href={s.url}>
                    {(s.kind || "").replace("_", " ")}
                  </External>
                </>
              ),
            },
            { key: "last", header: "Last check", cell: (s) => ago(s.last_run, now) },
            {
              key: "result",
              header: "Result",
              truncate: true,
              width: 360,
              cell: (s) => {
                const failed = s.error || s.scan?.state === "error";
                const checking = s.scan?.state === "queued" || s.scan?.state === "running";
                const text = checking
                  ? "Checking…"
                  : !s.enabled
                    ? "Turned off"
                    : failed
                      ? `${(s.scan?.error || shortError(s.error) || "Couldn't read this board").replace(/\s*\(\w*Error\)/, "")}${
                          (s.health?.failures ?? 0) > 1
                            ? ` · ${s.health!.failures} failed checks in a row, next try ${until(s.next_run, now)}`
                            : ""
                        }`
                      : `${plural(s.scan?.matched ?? 0, "match", "matches")} · ${plural(s.scan?.checked ?? 0, "posting")} checked`;
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
        <Pagination paging={paging} label="Board pages" />
      </div>
      </div>
    </Panel>
  );
}
