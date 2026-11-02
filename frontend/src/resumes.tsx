// Resumes: what employers get right now, the guided way to change that, and the files behind it (DESIGN.md, Resumes).
// The address says what's open: ?view=resumes&artifact=<id> is that tailored version's preview.
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Download, FileText, PencilLine, Plus } from "lucide-react";
import { api, ApiError } from "./api";
import { useWorkspace } from "./context";
import {
  ARTIFACT_PILL,
  ARTIFACT_WORD,
  employerLabel,
  unmounted,
  useFeature,
  useMasterStatus,
  type Artifact,
  type Inventory,
  type MasterStatus,
  type MasterView,
  type Page,
} from "./features";
import { ago, day, plural, resumeFileRefusal, isDeveloperText } from "./format";
import { useAction, useDraft, usePoll } from "./hooks";
import { Pagination, type Paging } from "./pagination";
import { downloadPdf, PdfPages, PdfThumb, usePdf } from "./pdfpages";
import { SwitchRow } from "./setup-kit";
import type { Resume } from "./types";
import {
  Button,
  Check,
  Dialog,
  Drawer,
  Dropzone,
  Empty,
  IconButton,
  Input,
  Listbox,
  Monogram,
  Panel,
  Pill,
  Skeleton,
  Table,
  type Column,
} from "./ui/index";
import { Facts, JobCell, LoadError, Notice, RowMenu } from "./worktools";
import "./styles/resumes.css";

const fileUrl = (id: string) => `/api/resumes/${id}/download`;
const versionUrl = (id: string) => `/api/v2/resumes/artifacts/${id}/pdf`;

// One decimal below 100% so "2 words missing" never reads as a perfect match.
const pct = (n?: number | null) =>
  n == null ? "—" : (n < 1 && n > 0.99 ? (Math.floor(n * 1000) / 10).toFixed(1) : Math.round(n * 100)) + "%";

function Lamp({ state }: { state: "done" | "now" | "next" }) {
  return <i className={"ax-lamp " + (state === "done" ? "is-ok" : "is-off") + (state === "now" ? " rs-lamp-now" : "")} aria-hidden="true" />;
}

// ---------- The page ----------
export function ResumesView() {
  const { snapshot, route } = useWorkspace();
  const status = useMasterStatus();
  const resumes = snapshot.resumes || [];
  const policy = status.data?.policy;
  // What the queue actually sends while tailoring is off: the policy's resume, which the library must agree on.
  const sending =
    resumes.find((r) => r.id === (policy?.resume_id || policy?.source_resume_id)) ||
    resumes.find((r) => r.id === status.data?.source?.id) ||
    (status.data ? null : resumes[0]) ||
    null;
  const [file, setFile] = useState<Resume | null>(null);
  return (
    <div className="ax-page rs-page">
      <Sending
        data={status.unavailable ? null : status.data}
        loading={!status.data && !status.error && !status.unavailable}
        failed={status.unavailable ? null : status.error}
        retry={status.refresh}
        refresh={status.refresh}
        sending={sending}
        anyResume={resumes.length > 0}
        onPreview={setFile}
      />
      <div className="rs-cols">
        <div className="rs-side">
          <Library resumes={resumes} sendingId={sending?.id ?? null} mode={policy?.mode} onPreview={setFile} refreshStatus={status.refresh} />
          <Material />
        </div>
        <Versions tailoringOff={policy?.mode === "master"} open={route.artifact} master={status.data?.master ?? null} />
      </div>
      <FilePreview resume={file} sending={sending?.id === file?.id} onClose={() => setFile(null)} />
    </div>
  );
}

// ---------- What employers get, and the one control ----------
function Sending({
  data,
  loading,
  failed,
  retry,
  refresh,
  sending,
  anyResume,
  onPreview,
}: {
  data: MasterStatus | null;
  loading: boolean;
  failed: Error | null;
  retry: () => unknown;
  refresh: () => Promise<unknown> | void;
  sending: Resume | null;
  anyResume: boolean;
  onPreview: (r: Resume) => void;
}) {
  const { pending, run } = useAction();
  const [asked, setAsked] = useDraft("resume-setup-open", false);
  if (loading)
    return (
      <section className="ax-panel rs-state" aria-busy="true">
        <div className="rs-state-main">
          <span className="rs-thumb is-blank" />
          <div className="rs-state-text">
            <span className="ax-skeleton" style={{ width: "60%", height: 22 }} />
            <span className="ax-skeleton" style={{ width: "40%" }} />
          </div>
        </div>
      </section>
    );
  if (failed && !data) return <LoadError error={failed} retry={retry} />;
  const name = sending?.name || data?.source?.name;
  // The tailoring service isn't on this installation: all there is to say is the file.
  const off = data?.policy?.mode === "master";
  const approved = !!data?.approved_id;
  const on = !off && !!data && approved;
  const master = data?.master ?? null;
  const showPath = !!data && !on && (asked || !!master);
  const say = !anyResume
    ? { line: "Add your resume and AXIOM can start applying.", sub: "Applications can't start without one." }
    : !data || off || !on
      ? {
          line: (
            <>
              Employers get <b>{name}</b>, exactly as you uploaded it.
            </>
          ),
          // No approved copy means nothing can be reworded (features/resumes prepare_resume: use_master), whatever the policy says:
          // a first-run "Tailoring is on, but..." read as something broken that had to be fixed before applying.
          sub: !data
            ? ""
            : off && data.policy.changed_at
              ? `Rewording for each job has been off since ${day(data.policy.changed_at)}, so every application sends this same PDF.`
              : "Every application sends this same PDF. AXIOM can also reword it for each job, if you'd like. That's optional.",
        }
      : {
          line: "Employers get a version of your approved resume, reworded for each job.",
          sub: "It uses only what's in your approved copy, and the application waits until that job's version is ready.",
        };
  return (
    <section className="ax-panel rs-state" aria-label="What employers get">
      <div className="rs-state-main">
        {sending && !on ? (
          <button type="button" className="rs-thumb" aria-label={`Preview ${sending.name}`} onClick={() => onPreview(sending)}>
            <PdfThumb url={fileUrl(sending.id)} width={92} />
          </button>
        ) : (
          <span className="rs-thumb is-blank" aria-hidden="true">
            <FileText size={22} />
          </span>
        )}
        <div className="rs-state-text">
          <p className="rs-sentence">{say.line}</p>
          {say.sub && <p className="rs-sub">{say.sub}</p>}
          {sending && !on && (
            <div className="rs-state-links">
              <Button size="sm" variant="ghost" onClick={() => onPreview(sending)}>
                Preview
              </Button>
              <DownloadButton url={fileUrl(sending.id)} name={sending.name} />
            </div>
          )}
        </div>
        <div className="rs-state-control">
          {on ? (
            <SwitchRow
              label="Reword for each job"
              note="On"
              checked
              disabled={pending !== null}
              onChange={() =>
                run(
                  "off",
                  async () => {
                    await api("/v2/resumes/policy", "PUT", { mode: "master" });
                    await refresh();
                  },
                  `Rewording is off. Employers get ${name || "your uploaded resume"} as uploaded.`,
                )
              }
            />
          ) : (
            data &&
            anyResume &&
            !master &&
            (asked ? (
              <Button variant="ghost" onClick={() => setAsked(false)}>
                Hide steps
              </Button>
            ) : (
              <Button onClick={() => setAsked(true)}>
                Reword for each job
              </Button>
            ))
          )}
        </div>
      </div>
      {showPath && <SetupPath data={data!} name={name} refresh={refresh} />}
    </section>
  );
}

function DownloadButton({ url, name, variant = "ghost" }: { url: string; name: string; variant?: "ghost" | "secondary" }) {
  const { pending, run } = useAction();
  return (
    <Button
      size="sm"
      variant={variant}
      loading={pending === "get"}
      icon={<Download size={14} aria-hidden="true" />}
      onClick={() => void run("get", () => downloadPdf(url, name))}
    >
      Download
    </Button>
  );
}

// ---------- The guided path: import, compare, approve, turn on. Nothing reaches an employer until the last one, the owner's click. ----------
function SetupPath({ data, name, refresh }: { data: MasterStatus; name?: string; refresh: () => Promise<unknown> | void }) {
  const { go } = useWorkspace();
  const { pending, run } = useAction();
  const master = data.master;
  const v = master?.verification || {};
  const [compared, setCompared] = useDraft("resume-compared:" + (master?.id || ""), false);
  const [approving, setApproving] = useState(false);
  const [turningOn, setTurningOn] = useState(false);
  const isApproved = master?.status === "approved";
  const now = !master ? 0 : isApproved ? 3 : compared ? 2 : 1;
  const state = (i: number) => (i < now ? "done" : i === now ? "now" : "next");
  const importing = pending === "import";
  const doImport = () =>
    run(
      "import",
      async () => {
        const r = await api<MasterView>("/v2/resumes/master/import", "POST");
        await refresh();
        return r;
      },
      (r) => (r?.verification?.faithful ? "Copied. Compare it with your original next." : "Copied, with differences. Compare it with your original next."),
    );
  const compare = () => {
    setCompared(true);
    go({ view: "editor", artifact: "master" });
  };
  return (
    <div className="rs-path">
      <div className="rs-path-head">
        <h2>Reword your resume for each job</h2>
        <p>Using only what's already in it. Employers get your PDF until you finish the last step.</p>
      </div>
      <ol className="rs-steps">
        <li className={"rs-step is-" + state(0)}>
          <h3>
            <Lamp state={state(0)} /> Import
          </h3>
          <p>
            {master ? (
              <>
                {master.origin === "edited" ? "Edited by you " : "Copied "}
                {ago(master.created_at)}.
              </>
            ) : (
              <>Copies {name ? <b>{name}</b> : "your PDF"} word for word into a copy you can edit. It rewrites nothing.</>
            )}
          </p>
          {master ? (
            <Button size="sm" variant="ghost" loading={importing} disabled={pending !== null && !importing} onClick={() => void doImport()}>
              {importing ? "Copying…" : "Import again"}
            </Button>
          ) : (
            <Button variant="primary" loading={importing} disabled={!data.source} onClick={() => void doImport()}>
              {importing ? "Copying…" : "Import resume"}
            </Button>
          )}
        </li>
        <li className={"rs-step is-" + state(1)}>
          <h3>
            <Lamp state={state(1)} /> Compare
          </h3>
          {master ? (
            <p>
              <b>{pct(v.coverage)}</b> of {v.source_words ?? "its"} words match
              {v.added_ratio ? `, ${pct(v.added_ratio)} added` : ""}.
              {v.render_fill && v.source_fill && (
                <>
                  {" "}
                  Fills {pct(v.render_fill.fill)} of the page; your original fills {pct(v.source_fill.fill)}.
                </>
              )}
            </p>
          ) : (
            <p>See the copy beside your original, and fix anything that differs.</p>
          )}
          {master && <WordDiff missing={v.missing} added={v.added} />}
          {master && (
            <Button size="sm" variant={now === 1 ? "primary" : "secondary"} onClick={compare}>
              Compare side by side
            </Button>
          )}
        </li>
        <li className={"rs-step is-" + state(2)}>
          <h3>
            <Lamp state={state(2)} /> Approve
          </h3>
          <p>
            {isApproved
              ? `Approved ${ago(master?.approved_at)}.`
              : master && data.approved_id
                ? "Your earlier approved copy stays in use until you approve this one."
                : "Only an approved copy can be reworded."}
          </p>
          {master && !isApproved && (
            <Button size="sm" variant={now === 2 ? "primary" : "secondary"} disabled={now < 2} onClick={() => setApproving(true)}>
              Approve this copy
            </Button>
          )}
        </li>
        <li className={"rs-step is-" + state(3)}>
          <h3>
            <Lamp state={state(3)} /> Turn on
          </h3>
          <p>Each job then gets its own version, and employers receive that instead of your PDF.</p>
          {isApproved && (
            <Button size="sm" variant="primary" onClick={() => setTurningOn(true)}>
              Turn on rewording
            </Button>
          )}
        </li>
      </ol>
      {master && <ApproveMaster master={master} open={approving} onClose={() => setApproving(false)} onDone={refresh} />}
      <Dialog
        open={turningOn}
        onClose={() => setTurningOn(false)}
        title="Turn on rewording?"
        sub="This changes what employers receive."
        footer={
          <>
            <Button variant="ghost" onClick={() => setTurningOn(false)}>
              Keep it off
            </Button>
            <Button
              variant="primary"
              loading={pending === "on"}
              onClick={() =>
                void run(
                  "on",
                  async () => {
                    await api("/v2/resumes/policy", "PUT", { mode: "tailored" });
                    await refresh();
                    setTurningOn(false);
                  },
                  "Rewording is on. New applications wait for their own version.",
                )
              }
            >
              Turn on rewording
            </Button>
          </>
        }
      >
        <ul className="rs-points">
          <li>Employers get a version of your approved copy reworded for each job, not {name || "your PDF"} as uploaded.</li>
          <li>It only rewords what's in your approved copy. It never adds a tool, metric or project.</li>
          <li>Each version takes a moment and one model call, and the application waits for it.</li>
          <li>You can turn it off here at any time.</li>
        </ul>
      </Dialog>
    </div>
  );
}

function WordDiff({ missing, added }: { missing?: string[]; added?: string[] }) {
  if (!missing?.length && !added?.length) return null;
  return (
    <div className="rs-diff">
      {!!missing?.length && (
        <p>
          <b>Missing:</b> {missing.slice(0, 12).join(", ")}
        </p>
      )}
      {!!added?.length && (
        <p>
          <b>Not in your original:</b> {added.slice(0, 12).join(", ")}
        </p>
      )}
    </div>
  );
}

/** Approve the copy: the check it passed or the differences to accept, in one place. Also opened from the editor. */
export function ApproveMaster({
  master,
  open,
  onClose,
  onDone,
}: {
  master: MasterView;
  open: boolean;
  onClose: () => void;
  onDone: () => Promise<unknown> | void;
}) {
  const { pending, run } = useAction();
  const [accept, setAccept] = useState(false);
  const v = master.verification || {};
  const pages = v.render_fill?.pages ?? 1;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Approve this copy?"
      sub="Rewording starts from it. Employers keep getting your PDF until you turn rewording on."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Not yet
          </Button>
          <Button
            variant="primary"
            loading={pending === "approve"}
            disabled={pages > 1 || (!v.faithful && !accept)}
            onClick={() =>
              void run(
                "approve",
                async () => {
                  await api(`/v2/resumes/master/${master.id}/approve`, "POST", { accept_differences: accept });
                  await onDone();
                  onClose();
                },
                "Approved. Rewording can start from this copy.",
              )
            }
          >
            Approve this copy
          </Button>
        </>
      }
    >
      <div className="ax-stack">
        <p>
          <b>{pct(v.coverage)}</b> of your {v.source_words ?? ""} original words are in this copy
          {v.added_ratio ? `, and ${pct(v.added_ratio)} are new` : ""}.
        </p>
        <WordDiff missing={v.missing} added={v.added} />
        {pages > 1 && (
          <Notice tone="warn">
            It runs to {pages} pages and must fit on one. Tighten the spacing or margins in the editor first.
          </Notice>
        )}
        {!v.faithful && pages <= 1 && <Check checked={accept} onChange={setAccept} label="These differences are intended" />}
      </div>
    </Dialog>
  );
}

// ---------- Your resumes ----------
function Library({
  resumes,
  sendingId,
  mode,
  onPreview,
  refreshStatus,
}: {
  resumes: Resume[];
  sendingId: string | null;
  mode?: string;
  onPreview: (r: Resume) => void;
  refreshStatus: () => Promise<unknown> | void;
}) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [file, setFile] = useState<File | null>(null);
  const full = resumes.length >= 10;
  return (
    <Panel title="Your resumes" actions={<span className="mono">{resumes.length} of 10</span>} flush className="rs-library">
      {resumes.length > 0 && (
        <ul className="rs-files">
          {resumes.map((r) => {
            const inUse = r.id === sendingId;
            return (
              <li key={r.id} className="rs-file">
                <button type="button" className="rs-file-open" onClick={() => onPreview(r)}>
                  <span className="rs-thumb is-small">
                    <PdfThumb url={fileUrl(r.id)} width={46} />
                  </span>
                  <span className="rs-file-text">
                    <strong title={r.name}>{r.name}</strong>
                    {(r.roles || []).length > 0 && <small title={r.roles.join(", ")}>{r.roles.join(", ")}</small>}
                    <small>
                      Added {day(r.created_at)}
                      {inUse && mode === "master" ? " · Sent to employers" : ""}
                    </small>
                  </span>
                </button>
                <span className="rs-file-actions">
                  <IconButton
                    label={`Download ${r.name}`}
                    icon={<Download size={15} aria-hidden="true" />}
                    onClick={() => void run("get:" + r.id, () => downloadPdf(fileUrl(r.id), r.name))}
                  />
                  <RowMenu
                    label={`More for ${r.name}`}
                    busy={pending === r.id}
                    actions={[
                      ...(mode === "master" && !inUse
                        ? [
                            {
                              id: "use",
                              label: "Send this one to employers",
                              onSelect: () =>
                                void run(
                                  "use:" + r.id,
                                  async () => {
                                    await api("/v2/resumes/policy", "PUT", { mode: "master", resume_id: r.id });
                                    await refreshStatus();
                                  },
                                  `Employers now get ${r.name}.`,
                                ),
                            },
                          ]
                        : []),
                      {
                        id: "remove",
                        label: inUse && mode === "master" ? "In use, can't be removed" : "Remove",
                        danger: true,
                        // Removing the policy's resume would stop every application: the queue would have nothing to send.
                        disabled: inUse && mode === "master",
                        confirm: "Remove this resume? The file stays with past applications.",
                        confirmLabel: "Remove",
                        onSelect: () =>
                          void run(
                            r.id,
                            async () => {
                              await api("/resumes/" + r.id, "DELETE");
                              await refresh();
                            },
                            "Resume removed.",
                          ),
                      },
                    ]}
                  />
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="rs-add">
        {full ? (
          <p className="rs-line">You have 10 resumes, the most AXIOM keeps. Remove one to add another.</p>
        ) : (
          <Dropzone
            label="Add a resume"
            title="Drop a resume, or click to choose"
            hint="A PDF, up to 5 MB and 20 pages."
            refusal={resumeFileRefusal}
            accept="application/pdf,.pdf"
            file={null}
            onFile={setFile}
          />
        )}
      </div>
      <AddResume file={file} setFile={setFile} />
    </Panel>
  );
}

function AddResume({ file, setFile }: { file: File | null; setFile: (f: File | null) => void }) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [name, setName] = useState("");
  const [roles, setRoles] = useState("");
  useEffect(() => {
    if (file) {
      setName(file.name.replace(/\.pdf$/i, ""));
      setRoles("");
    }
  }, [file]);
  const send = () => {
    if (!file || !name.trim()) return;
    const form = new FormData();
    form.append("file", file);
    form.append("name", name.trim());
    form.append("roles", roles);
    void run(
      "upload",
      async () => {
        await api("/resumes", "POST", form);
        setFile(null);
        await refresh();
      },
      "Resume added.",
    );
  };
  return (
    <Dialog
      open={!!file}
      onClose={() => setFile(null)}
      title="Add this resume"
      footer={
        <>
          <Button variant="ghost" onClick={() => setFile(null)}>
            Cancel
          </Button>
          <Button variant="primary" loading={pending === "upload"} disabled={!name.trim()} onClick={send}>
            Add resume
          </Button>
        </>
      }
    >
      <form
        className="ax-stack"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <Dropzone label="Resume PDF" accept="application/pdf,.pdf" file={file} onFile={setFile} />
        <Input label="Name" required maxLength={120} data-autofocus value={name} onChange={(e) => setName(e.target.value)} />
        <Input
          label="Roles it suits"
          hint="Optional, separated by commas."
          placeholder="Backend, Data"
          value={roles}
          onChange={(e) => setRoles(e.target.value)}
        />
        <button type="submit" className="sr-only" tabIndex={-1}>
          Add resume
        </button>
      </form>
    </Dialog>
  );
}

function FilePreview({ resume, sending, onClose }: { resume: Resume | null; sending: boolean; onClose: () => void }) {
  const pdf = usePdf(resume ? fileUrl(resume.id) : null);
  return (
    <Drawer
      open={!!resume}
      onClose={onClose}
      width={760}
      lead={
        <span className="ax-mono is-lg" aria-hidden="true">
          <FileText size={18} />
        </span>
      }
      title={resume?.name || ""}
      sub={resume ? `Added ${day(resume.created_at)}${sending ? " · Sent to employers now" : ""}` : undefined}
      footer={resume && <DownloadButton url={fileUrl(resume.id)} name={resume.name} variant="secondary" />}
    >
      <div className="rs-preview">
        <PdfPages data={pdf.data} error={pdf.error} fit="width" label={resume?.name || "Resume"} />
      </div>
    </Drawer>
  );
}

// ---------- Tailored versions ----------
function sentWords(a: Artifact, tailoringOff: boolean): { word: string; tone: "ok" | "neutral" | "warn" | "info"; tip?: string } {
  if (a.sent?.state === "confirmed") return { word: `Sent ${day(a.sent.at)}`, tone: "ok", tip: "An application went out with this version." };
  if (a.sent?.state === "unknown") return { word: "Maybe sent", tone: "info", tip: "An application used it, but its outcome wasn't confirmed." };
  if (a.status !== "ready") return { word: ARTIFACT_WORD[a.status] || a.status, tone: (ARTIFACT_PILL[a.status] as "neutral") || "neutral" };
  if (!tailoringOff) return { word: "Ready", tone: "ok" };
  // An older server doesn't say what happened to a version. "Never sent" is a claim, so it needs the answer.
  if (a.sent === undefined) return { word: "Not in use", tone: "neutral" };
  return { word: "Never sent", tone: "neutral", tip: "No application used this version." };
}

const companyOf = (a: Artifact) => a.job?.company || (a.employer_id ? employerLabel(a.employer_id) : "Version for a job");
const reworded = (a: Artifact) => (a.selection || []).filter((x) => x.text !== x.source_text).length;

function Versions({ tailoringOff, open, master }: { tailoringOff: boolean; open?: string; master: MasterView | null }) {
  const { go } = useWorkspace();
  const [status, setStatus] = useDraft("artifact-status", "");
  const [page, setPage] = useState(1);
  const list = useFeature<Page<Artifact>>(
    "resumes",
    (signal) => api(`/v2/resumes/artifacts?page=${page}&size=25${status ? "&status=" + status : ""}`, "GET", undefined, signal),
    30000,
    [page, status],
  );
  if (list.unavailable) return null;
  const items = Array.isArray(list.data?.items) ? list.data!.items : [];
  const total = list.data?.total ?? items.length;
  const size = list.data?.size ?? 25;
  const paging: Paging = {
    items: [],
    total,
    page,
    pages: Math.max(1, Math.ceil(total / size)),
    size,
    initialSize: size,
    setPage: setPage as Paging["setPage"],
    setSize: () => undefined,
  };
  const openVersion = (a: Artifact) => go({ view: "resumes", artifact: a.artifact_id });
  // Say how old they are only when it's one day's batch, which is what makes them read as old drafts rather than recent work.
  const days = [...new Set(items.map((a) => day(a.created_at)))];
  const line = tailoringOff
    ? `${days.length === 1 && !status ? `Drafts from ${days[0]}. ` : "Drafts. "}New applications don't use them while rewording is off.`
    : "Made for each job before its application starts.";
  const columns: Column<Artifact>[] = [
    {
      key: "job",
      header: "Made for",
      width: 520,
      truncate: true,
      min: 220,
      cell: (a) => (
        <JobCell
          company={companyOf(a)}
          role={a.job?.title || (a.job_id ? "" : "Not for a specific job")}
          meta={sentWords(a, tailoringOff).word}
          onOpen={() => openVersion(a)}
        />
      ),
    },
    {
      key: "reworded",
      header: "Reworded",
      hideBelow: 600,
      width: "1%",
      cell: (a) =>
        (a.selection || []).length ? (
          <span>
            <span className="mono">{reworded(a)}</span> of <span className="mono">{a.selection.length}</span>
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "sent",
      header: "Status",
      hideBelow: 600,
      width: "1%",
      cell: (a) => {
        const s = sentWords(a, tailoringOff);
        return (
          <Pill tone={s.tone} dot title={s.tip}>
            {s.word}
          </Pill>
        );
      },
    },
  ];
  return (
    <>
      <Panel
        className="rs-versions"
        title={
          <>
            Versions for each job{list.data ? <span className="rs-count mono"> {total}</span> : null}
          </>
        }
        flush
        actions={
          <>
            <Listbox
              size="sm"
              align="end"
              label="Filter by status"
              value={status}
              options={[
                { value: "", label: "Any status" },
                { value: "ready", label: "Ready" },
                { value: "building", label: "Building" },
                { value: "failed", label: "Needs repair" },
                { value: "kept_master", label: "Master kept" },
              ]}
              onChange={(v) => {
                setStatus(v);
                setPage(1);
              }}
            />
            <Button
              size="sm"
              variant="ghost"
              icon={<Plus size={14} aria-hidden="true" />}
              onClick={() => go(master ? { view: "editor", artifact: "master" } : { view: "editor" })}
            >
              {master ? "Open your copy" : "New version"}
            </Button>
          </>
        }
      >
        {total > 0 && <p className="rs-line">{line}</p>}
        {list.error && !list.data ? (
          <div className="rs-pad">
            <LoadError error={list.error} retry={list.refresh} />
          </div>
        ) : !list.data ? (
          <div className="rs-pad">
            <Skeleton rows={5} />
          </div>
        ) : (
          <Table
            label="Versions for each job"
            rows={items}
            rowKey={(a) => a.artifact_id}
            columns={columns}
            onRowClick={openVersion}
            footer={total > size ? <Pagination paging={paging} label="Version pages" sizes={false} /> : undefined}
            empty={
              <Empty
                compact
                icon={FileText}
                title={status ? "No versions with this status" : "No versions yet"}
                hint={
                  status
                    ? "Choose another status to see the others."
                    : tailoringOff
                      ? "Each job gets one here once rewording is on. Until then every application sends the resume above."
                      : "Each job gets one here before its application starts."
                }
                action={
                  status ? (
                    <Button size="sm" onClick={() => setStatus("")}>
                      Show all
                    </Button>
                  ) : undefined
                }
              />
            }
          />
        )}
      </Panel>
      {open && open !== "master" && (
        <VersionPreview key={open} id={open} items={items} tailoringOff={tailoringOff} />
      )}
    </>
  );
}

function VersionPreview({ id, items, tailoringOff }: { id: string; items: Artifact[]; tailoringOff: boolean }) {
  const { go, openJob, route } = useWorkspace();
  const known = items.find((a) => a.artifact_id === id);
  // Opened by address, or from a job's drawer: it may not be on the page of the list that's showing.
  const one = usePoll<Page<Artifact>>(
    (signal) => api(`/v2/resumes/artifacts?artifact_id=${encodeURIComponent(id)}`, "GET", undefined, signal),
    3_600_000,
    [id],
    !known,
  );
  const a = known || one.data?.items?.find((x) => x.artifact_id === id) || null;
  const ready = !a || a.status === "ready";
  const pdf = usePdf(ready ? versionUrl(id) : null);
  const close = () => go({ view: "resumes" });
  const at = items.findIndex((x) => x.artifact_id === id);
  const step = (by: number) => {
    const next = items[at + by];
    if (next) go({ view: "resumes", artifact: next.artifact_id });
  };
  const company = a ? companyOf(a) : "Version for a job";
  const s = a ? sentWords(a, tailoringOff) : null;
  const changed = a ? reworded(a) : 0;
  const notFound = one.error instanceof ApiError || (one.data && !a);
  const jobOpen = !!route.job;
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (jobOpen || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      // Arrows that are moving something else keep doing that: text, a zoomed page.
      if ((e.target as HTMLElement).closest?.("input, textarea, select, [role='radiogroup'], .ax-pdf-host")) return;
      step(e.key === "ArrowLeft" ? -1 : 1);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  return (
    <Drawer
      open
      onClose={close}
      width={760}
      lead={<Monogram name={company} size="lg" employer />}
      title={company}
      sub={a?.job?.title}
      footer={
        <div className="rs-preview-foot">
          {at >= 0 && items.length > 1 && (
            <span className="rs-preview-steps">
              <IconButton label="Previous version" framed disabled={at === 0} icon={<ArrowLeft size={15} aria-hidden="true" />} onClick={() => step(-1)} />
              <IconButton label="Next version" framed disabled={at === items.length - 1} icon={<ArrowRight size={15} aria-hidden="true" />} onClick={() => step(1)} />
              <span className="mono">
                {at + 1} of {items.length}
              </span>
            </span>
          )}
          <span className="rs-preview-acts">
            {a?.job_id && (
              <Button variant="ghost" size="sm" onClick={() => openJob(a.job_id!)}>
                Open job
              </Button>
            )}
            {ready && <DownloadButton url={versionUrl(id)} name={`Resume for ${company}`} variant="secondary" />}
            {ready && (
              <Button size="sm" variant="primary" icon={<PencilLine size={14} aria-hidden="true" />} onClick={() => go({ view: "editor", artifact: id })}>
                Open in editor
              </Button>
            )}
          </span>
        </div>
      }
    >
      <div className="rs-preview">
        {notFound ? (
          <div className="rs-pad">
            <Empty compact icon={FileText} title="This version is no longer here" hint="It may have been replaced." action={<Button size="sm" onClick={close}>Back to Resumes</Button>} />
          </div>
        ) : (
          <>
            {a && s && (
              <div className="rs-block">
                <p className="rs-block-head">
                  <Pill tone={s.tone} dot>
                    {s.word}
                  </Pill>
                  <span>Built {day(a.finished_at || a.created_at)}</span>
                </p>
                <p className="rs-block-say">
                  {a.sent?.state === "confirmed"
                    ? `${company} received this version with your application. New applications don't use it.`
                    : a.sent?.state === "unknown"
                      ? `An application to ${company} used this version, but its outcome wasn't confirmed.`
                      : a.status === "failed"
                        ? (a.error?.detail && !isDeveloperText(a.error.detail) ? a.error.detail : "It couldn't be built.")
                        : a.status === "kept_master"
                          ? "The reworded version didn't read better than your resume, so your resume was kept."
                          : tailoringOff
                            ? a.sent === undefined
                              ? "Nothing new uses this version while rewording is off."
                              : "No application used this version, and nothing new will while rewording is off."
                            : "Used for this job's application while it still matches the posting and your profile."}
                </p>
                {(a.selection || []).length > 0 && (
                  <details className="ax-fold rs-changes">
                    <summary>
                      What it changed <span className="ax-note">{changed} of {plural(a.selection.length, "bullet")} reworded</span>
                    </summary>
                    <ul className="rs-change-list">
                      {a.selection
                        .filter((x) => x.text !== x.source_text)
                        .map((x) => (
                          <li key={x.unit_id}>
                            <span>{x.text}</span>
                            <small>Your words: {x.source_text}</small>
                          </li>
                        ))}
                    </ul>
                    {!!a.validation?.repairs?.length && (
                      <p className="ax-hint">
                        {plural(a.validation.repairs.length, "suggested change")} undone (unsupported claim or page overflow); your wording was kept there.
                      </p>
                    )}
                    {!!a.validation?.warnings?.length && <p className="ax-hint">{a.validation.warnings.join(" ")}</p>}
                  </details>
                )}
              </div>
            )}
            {ready ? (
              <PdfPages data={pdf.data} error={pdf.error} fit="width" label={`Resume for ${company}`} />
            ) : (
              <div className="rs-pad">
                <Empty compact icon={FileText} title="There's no PDF for this one" hint="Only a finished version has a PDF." />
              </div>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}

// ---------- What tailoring may draw on ----------
function Material() {
  const inv = usePoll<Inventory>((signal) => api("/v2/resumes/evidence", "GET", undefined, signal), 300000);
  if (!inv.data)
    return inv.error && !unmounted(inv.error) ? <LoadError error={inv.error} retry={inv.refresh} /> : null;
  const d = inv.data;
  const roles = d.roles || [];
  const education = d.education || [];
  const projects = d.projects || [];
  const units = d.units || [];
  const bullets = (row: string) => (
    <ul className="ax-rows">
      {units
        .filter((u) => u.source_row === row)
        .map((u) => (
          <li className="ax-row" key={u.id}>
            <span className="ax-row-main">{u.text}</span>
          </li>
        ))}
    </ul>
  );
  return (
    <details className="ax-panel ax-fold rs-material">
      <summary>
        <strong>What rewording can draw on</strong>
        <span className="ax-hint">
          {[
            roles.length && plural(roles.length, "role"),
            education.length && plural(education.length, "degree"),
            projects.length && plural(projects.length, "project"),
            units.length && plural(units.length, "bullet"),
          ]
            .filter(Boolean)
            .join(", ")}
        </span>
      </summary>
      <div className="ax-panel-body">
        <div className="ax-stack">
          <p className="ax-hint">Rewording never adds a metric, tool or project that isn't in your approved copy or here.</p>
          {roles.map((r) => (
            <div key={r.row}>
              <h3>
                {r.title}, {r.company}{" "}
                <small className="ax-hint">
                  {r.start}–{r.current ? "present" : r.end}
                </small>
              </h3>
              {bullets(r.row)}
            </div>
          ))}
          {projects.map((p) => (
            <div key={p.row}>
              <h3>{p.title}</h3>
              {bullets(p.row)}
            </div>
          ))}
          {education.length > 0 && <Facts items={education.map((e): [string, string] => [e.degree || "Degree", `${e.school}${e.end ? " · " + e.end : ""}`])} />}
        </div>
      </div>
    </details>
  );
}

