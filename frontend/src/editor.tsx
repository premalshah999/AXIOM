// Resume editor: structured content + layout controls with a live PDF preview,
// and a source view for direct LaTeX edits. Every keystroke recompiles after a
// short pause; the last good preview stays visible while you type.
// The address says which document is open: ?view=editor&artifact=<version>, artifact=master for your copy, neither for a new one.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type TextareaHTMLAttributes } from "react";
import { PdfPages, savePdf, usePdf, pdfName } from "./pdfpages";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  PanelLeft,
  PanelLeftClose,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { api, ApiError, signedOut } from "./api";
import { useWorkspace } from "./context";
import { useMasterStatus, type MasterView } from "./features";
import { day } from "./format";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import { ApproveMaster } from "./resumes";
import { useMedia } from "./shell/prefs";
import { Button, Check, ConfirmButton, Empty, IconButton, Input, Listbox, Monogram, Pill, Segmented, Tabs, Tooltip, type PillTone } from "./ui/index";
import { LoadError, Notice } from "./worktools";
import "./styles/editor.css";

type Bullet = {
  id: string;
  text: string;
  unit_id: string | null;
  source_text: string;
  include: boolean;
};
type Entry = {
  id: string;
  heading: string;
  subheading: string;
  dates: string;
  location: string;
  bullets: Bullet[];
  include: boolean;
};
type Section = {
  id: string;
  title: string;
  kind: "experience" | "education" | "projects" | "skills" | "text";
  entries: Entry[];
  text: string;
  include: boolean;
};
type Doc = { name: string; contact: string[]; sections: Section[] };
type Style = {
  template: "classic" | "modern" | "compact";
  font: string;
  font_size: number;
  line_spacing: number;
  margin_top: number;
  margin_bottom: number;
  margin_left: number;
  margin_right: number;
  section_gap: number;
  entry_gap: number;
  bullet_gap: number;
  header: "center" | "left";
  headings: "rule" | "plain";
  name_size: number;
};
type Loaded = {
  doc: Doc;
  style: Style;
  latex: string;
  job_id: string | null;
  base_artifact_id: string | null;
  source_edited: boolean;
  master?: MasterView;
  /** Which version this is: the job it was made for and whether it was sent. */
  version?: {
    job: { company: string; title: string; location: string } | null;
    sent: { state: "confirmed" | "unknown"; at: string | null } | null;
    /** Set when this is the version a job set to a tailored resume holds. */
    choice?: { mode: string; state: string } | null;
    built_at: string;
  } | null;
};
type Meta = {
  templates: {
    id: Style["template"];
    label: string;
    font: string;
    header: Style["header"];
    headings: Style["headings"];
  }[];
  fonts: { id: string; label: string; available: boolean }[];
  defaults: Style;
};
type Draft = {
  doc: Doc;
  style: Style;
  latex: string | null;
  base: string | null;
  job: string | null;
};
type CompileError = { line: number | null; message: string };
type Preview = {
  bytes: ArrayBuffer;
  pages: number;
  ms: number;
} | null;

const TEMPLATE_NOTES: Record<string, string> = {
  classic: "Serif, centered name, ruled headings. The safest choice for most applications.",
  modern: "Clean sans-serif, left-aligned header. Reads well on screens.",
  compact: "Tighter spacing and plain headings. Fits more on one page.",
};

export function ResumeEditor() {
  const { route } = useWorkspace();
  const artifact = route.artifact || "";
  // Each address is its own load. On one component, opening another version kept the last answer until the new one came, so the form
  // mounted with the old document and stayed on it: after Save version the URL named the new version over the words from before the edit.
  return <EditorLoader key={artifact || "new"} artifact={artifact} />;
}

function EditorLoader({ artifact }: { artifact: string }) {
  const { go } = useWorkspace();
  const back = () => go({ view: "resumes" });
  // artifact=master opens the master resume itself; saving creates a new draft master.
  const isMaster = artifact === "master";
  const loaded = usePoll<Loaded>(
    (signal) =>
      api(
        `/v2/resumes/editor/document${isMaster ? "?master=true" : artifact ? "?artifact_id=" + encodeURIComponent(artifact) : ""}`,
        "GET",
        undefined,
        signal,
      ),
    3_600_000,
    [artifact],
  );
  const meta = usePoll<Meta>(
    (signal) => api("/v2/resumes/editor/templates", "GET", undefined, signal),
    3_600_000,
  );
  const missing = loaded.error instanceof ApiError && loaded.error.status === 404;
  const backButton = (
    <Button size="sm" onClick={back}>
      Back to Resumes
    </Button>
  );
  if (missing)
    return (
      <EditorState>
        <Empty
          title={isMaster ? "There's no copy to edit yet" : artifact ? "This version is no longer here" : "The resume editor isn't on this installation"}
          hint={isMaster ? "Import your resume in Resumes first." : artifact ? "It may have been replaced." : "It becomes available once the resume service is enabled."}
          action={backButton}
        />
      </EditorState>
    );
  if (!loaded.data || !meta.data)
    return loaded.error || meta.error ? (
      <EditorState>
        <LoadError error={(loaded.error || meta.error)!} retry={() => (loaded.error ? loaded.refresh() : meta.refresh())} />
        {backButton}
      </EditorState>
    ) : (
      <EditorSkeleton back={back} />
    );
  if (!Array.isArray(loaded.data.doc?.sections) || !loaded.data.style || !Array.isArray(meta.data.templates))
    return (
      <EditorState>
        <LoadError
          error={new Error("The editor got an answer it could not read.")}
          retry={() => {
            loaded.refresh();
            meta.refresh();
          }}
        />
        {backButton}
      </EditorState>
    );
  return (
    <Workbench
      key={isMaster ? "master:" + loaded.data.master?.id : artifact || "new"}
      initial={loaded.data}
      meta={meta.data}
      back={back}
      reload={loaded.refresh}
    />
  );
}

/** A state before the editor can open. The shell's header is hidden here, so it needs its own heading. */
function EditorState({ children }: { children: React.ReactNode }) {
  return (
    <div className="ax-ed-state">
      <h1 className="sr-only">Resume editor</h1>
      {children}
    </div>
  );
}

function EditorSkeleton({ back }: { back: () => void }) {
  return (
    <div className="ax-ed" aria-busy="true">
      <header className="ax-ed-bar">
        <IconButton label="Back to Resumes" size="md" icon={<ArrowLeft size={16} aria-hidden="true" />} onClick={back} />
        <h1 className="sr-only">Opening the resume</h1>
        <span className="ax-skeleton" style={{ width: 200 }} />
      </header>
      <div className="ax-ed-grid">
        <div className="ax-ed-panel">
          <div className="ax-ed-scroll">
            {[70, 90, 60, 85].map((w, i) => (
              <span key={i} className="ax-skeleton" style={{ width: w + "%", height: 34 }} />
            ))}
          </div>
        </div>
        <section className="ax-ed-preview" aria-label="Preview">
          <div className="ax-pdf-host">
            <div className="ax-pdf-stack">
              <div className="ax-pdf-blank" style={{ height: "100%" }} />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

type CopyView = "copy" | "both" | "original";

function Workbench({
  initial,
  meta,
  back,
  reload,
}: {
  initial: Loaded;
  meta: Meta;
  back: () => void;
  reload: () => Promise<unknown> | void;
}) {
  const { go, openJob } = useWorkspace();
  const status = useMasterStatus();
  const tailoringOff = status.data?.policy?.mode === "master";
  const master = initial.master;
  const narrow = useMedia("(max-width: 959px)");
  // A draft of your copy opens on the job to do with it: the two documents, large, side by side. The form is one click away.
  const [view, setView] = useDraft<CopyView>("editor-copy-view", "both");
  const shownView: CopyView = !master || (narrow && view === "both") ? "copy" : view;
  const [formChoice, setFormOpen] = useDraft<boolean | null>("editor-form-open", null);
  const formOpen = formChoice ?? master?.status !== "draft";
  const original = usePdf(master && shownView !== "copy" ? `/api/resumes/${encodeURIComponent(master.source_resume_id)}/download` : null);
  const [draft, setDraft, clearDraft] = useDraft<Draft>(
    "editor:" +
      (master ? "master:" + master.id : initial.base_artifact_id || "new"),
    {
      doc: initial.doc,
      style: initial.style,
      latex: initial.source_edited ? initial.latex : null,
      base: initial.base_artifact_id,
      job: initial.job_id,
    },
  );
  const [tab, setTab] = useDraft("editor-tab", "content");
  // On a phone the form and the page take turns.
  const [phonePane, setPhonePane] = useState<"form" | "preview">("form");
  const [preview, setPreview] = useState<Preview>(null);
  const [errors, setErrors] = useState<CompileError[]>([]);
  const [compiling, setCompiling] = useState(false);
  const [problem, setProblem] = useState("");
  const [approving, setApproving] = useState(false);
  // The first job opens with its bullets in view; the header is one row until it's wanted.
  const [openEntries, setOpenEntries] = useState<Set<string>>(() => {
    const first = initial.doc.sections.find((s) => s.entries.length)?.entries[0];
    return new Set(first ? [first.id] : []);
  });
  const { pending, run } = useAction();
  const inflight = useRef<AbortController | null>(null);
  const dirty =
    JSON.stringify([draft.doc, draft.style, draft.latex]) !==
    JSON.stringify([
      initial.doc,
      initial.style,
      initial.source_edited ? initial.latex : null,
    ]);

  // Live compile: debounce, cancel the previous request, keep the last good PDF.
  const payload = useMemo(
    () =>
      draft.latex !== null
        ? { latex: draft.latex }
        : { doc: draft.doc, style: draft.style },
    [draft.doc, draft.style, draft.latex],
  );
  useEffect(() => {
    const timer = window.setTimeout(async () => {
      inflight.current?.abort();
      const controller = new AbortController();
      inflight.current = controller;
      setCompiling(true);
      try {
        const res = await fetch("/api/v2/resumes/editor/compile", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        if (res.status === 401) signedOut("Your session ended. Sign in again.");
        if (res.ok) {
          const bytes = await res.arrayBuffer();
          setPreview({
            bytes,
            pages: Number(res.headers.get("X-Pages") || 1),
            ms: Number(res.headers.get("X-Compile-Ms") || 0),
          });
          setErrors([]);
          setProblem("");
        } else {
          const body = await res
            .json()
            .catch(() => ({ detail: "The preview could not be built." }));
          setErrors(body.errors || []);
          setProblem(
            typeof body.detail === "string"
              ? body.detail
              : "The preview could not be built.",
          );
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError")
          setProblem("Can't reach AXIOM to build the preview.");
      } finally {
        if (inflight.current === controller) {
          inflight.current = null;
          setCompiling(false);
        }
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [payload]);
  useEffect(() => () => inflight.current?.abort(), []);

  const setDoc = (fn: (d: Doc) => Doc) =>
    setDraft((d) => ({ ...d, doc: fn(structuredClone(d.doc)) }));
  const setStyle = (patch: Partial<Style>) =>
    setDraft((d) => ({ ...d, style: { ...d.style, ...patch } }));
  const status_: { tone: PillTone; text: string; tip?: string } = problem
    ? {
        tone: "bad",
        text: errors.length ? `${errors.length} error${errors.length === 1 ? "" : "s"}, showing the last good page` : "Preview not updated",
        tip: problem,
      }
    : compiling
      ? { tone: "neutral", text: "Updating preview…" }
      : preview
        ? {
            tone: preview.pages > 1 ? "warn" : "neutral",
            text: `${preview.pages} page${preview.pages === 1 ? "" : "s"} · built in ${(preview.ms / 1000).toFixed(1)} s`,
            tip: preview.pages > 1 ? "Most employers expect one page. Tighten the layout, or hide an entry." : undefined,
          }
        : { tone: "neutral", text: "Building preview…" };
  const fileName = pdfName((draft.doc.name || "Resume") + (initial.version?.job ? " for " + initial.version.job.company : " resume"));
  const job = initial.version?.job;
  const title = master ? "Your copy" : job ? job.company : initial.base_artifact_id ? "Version for a job" : "New version";
  const subtitle = master
    ? `${master.status === "approved" ? "Approved" : "Draft"} · ${(Math.floor((master.verification.coverage ?? 0) * 1000) / 10).toString()}% of your original words`
    : initial.base_artifact_id
      ? [job?.title, tailoringOff && !initial.version?.choice ? `Draft from ${day(initial.version?.built_at)}, not in use` : day(initial.version?.built_at)].filter((x) => x && !x.endsWith("—")).join(" · ")
      : "From your profile";
  const copyIsDraft = master?.status === "draft";
  const save = () =>
    run(
      "save",
      async () => {
        const r = await api<{
          artifact_id: string;
          pages: number;
          problems: string[];
          choice?: { mode: string; state: string } | null;
        }>("/v2/resumes/editor/save", "POST", {
          ...payload,
          style: draft.style,
          doc: draft.doc,
          job_id: draft.job,
          base_artifact_id: draft.base,
        });
        clearDraft();
        go({ view: "editor", artifact: r.artifact_id });
        return r;
      },
      (r) =>
        r &&
        (r.problems.length
          ? `Saved as a new version. Check: ${r.problems[0]}.`
          : r.choice
            ? r.choice.state === "approved"
              ? "Saved. This is the job's tailored resume now."
              : "Saved. This is the job's tailored resume now, and it needs your approval."
          : tailoringOff
            ? "Saved as a new version. Only a job set to a tailored resume sends one."
            : draft.job
              ? "Saved as a new version. Applications for this job will use it."
              : "Saved as a new version."),
    );
  const saveMaster = () =>
    run(
      "save",
      async () => {
        const r = await api<{
          verification: { coverage?: number; faithful?: boolean };
        }>("/v2/resumes/master", "PUT", {
          doc: draft.doc,
          style: draft.style,
        });
        clearDraft();
        await reload();
        return r;
      },
      (r) =>
        r &&
        (r.verification.faithful
          ? "Saved as a draft of your copy. Approve it when it looks right."
          : `Saved as a draft of your copy. ${Math.round((r.verification.coverage ?? 0) * 100)}% of your original words are present; review the differences before approving.`),
    );
  const showForm = narrow ? phonePane === "form" : formOpen;
  const showPreview = !narrow || phonePane === "preview";

  return (
    <div className="ax-ed">
      <header className="ax-ed-bar">
        <IconButton label="Back to Resumes" size="md" icon={<ArrowLeft size={16} aria-hidden="true" />} onClick={back} />
        {job && !master && <Monogram name={job.company} employer />}
        <div className="ax-ed-name">
          <h1 title={title}>{title}</h1>
          {subtitle && <span title={subtitle}>{subtitle}</span>}
        </div>
        {draft.job && !master && (
          <Button size="sm" variant="ghost" className="ax-ed-job" onClick={() => openJob(draft.job!)}>
            Open job
          </Button>
        )}
        {draft.job && !master && initial.version?.choice && (
          <Button size="sm" variant="ghost" className="ax-ed-job" onClick={() => go({ view: "tailor", tailor: draft.job!, artifact: undefined })}>
            Review and approve
          </Button>
        )}
        <span className="ax-ed-status" role="status" aria-live="polite">
          <Tooltip label={status_.tip}>
            <Pill tone={status_.tone} dot={status_.tone !== "neutral"}>
              {status_.text}
            </Pill>
          </Tooltip>
        </span>
        <div className="ax-ed-actions">
          {dirty && (
            <Button
              size="sm"
              variant="ghost"
              icon={<X size={14} aria-hidden="true" />}
              onClick={() => clearDraft()}
              title="Back to the saved document"
            >
              Discard
            </Button>
          )}
          <Button
            size="sm"
            icon={<Download size={14} aria-hidden="true" />}
            disabled={!preview || !!problem}
            title={problem ? "Fix what stops the preview first: this would download an older page." : undefined}
            onClick={() => preview && savePdf(preview.bytes, fileName)}
          >
            Download
          </Button>
          {master ? (
            copyIsDraft && !dirty ? (
              <Button variant="primary" size="sm" onClick={() => setApproving(true)}>
                Approve
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                loading={pending !== null}
                disabled={!!problem || draft.latex !== null || !dirty}
                title={draft.latex !== null ? "A hand-edited source can't become your copy. Go back to visual editing to save." : !dirty ? "No changes to save." : undefined}
                onClick={() => void saveMaster()}
              >
                Save draft
              </Button>
            )
          ) : (
            <Button
              variant="primary"
              size="sm"
              loading={pending !== null}
              disabled={!!problem || (!dirty && !!initial.base_artifact_id)}
              title={problem ? "Fix what stops the preview first." : !dirty && initial.base_artifact_id ? "No changes to save." : undefined}
              onClick={() => void save()}
            >
              Save version
            </Button>
          )}
        </div>
      </header>
      {master && <ApproveMaster master={master} open={approving} onClose={() => setApproving(false)} onDone={async () => { await reload(); back(); }} />}
      <div className={"ax-ed-grid" + (showForm ? "" : " is-form-closed")} data-pane={phonePane}>
        {showForm && (
          <aside className="ax-ed-panel" aria-label="Edit resume">
            <div className="ax-ed-tabs">
              <Tabs
                label="Editor views"
                value={tab}
                onChange={setTab}
                tabs={[
                  { id: "content", label: "Content" },
                  { id: "layout", label: "Layout" },
                  { id: "source", label: draft.latex !== null ? "Source (edited)" : "Source" },
                ]}
              />
              {narrow && (
                <Button size="sm" onClick={() => setPhonePane("preview")}>
                  See the page
                </Button>
              )}
            </div>
            <div className={"ax-ed-scroll" + (tab === "source" ? " is-source" : "")}>
              {draft.latex !== null && tab !== "source" ? (
                <Notice
                  tone="warn"
                  action={
                    <Button size="sm" variant="ghost" onClick={() => setTab("source")}>
                      Open source
                    </Button>
                  }
                >
                  You're editing the source by hand, so these controls are paused.
                </Notice>
              ) : null}
              {tab === "content" && (
                <ContentPanel doc={draft.doc} setDoc={setDoc} disabled={draft.latex !== null} open={openEntries} setOpen={setOpenEntries} />
              )}
              {tab === "layout" && (
                <LayoutPanel style={draft.style} setStyle={setStyle} meta={meta} disabled={draft.latex !== null} />
              )}
              {tab === "source" && (
                <SourcePanel
                  draft={draft}
                  errors={errors}
                  fallback={initial.latex}
                  edit={(latex) => setDraft((d) => ({ ...d, latex }))}
                  reset={() => setDraft((d) => ({ ...d, latex: null }))}
                />
              )}
              {dirty && tab !== "source" && (
                <p className="ax-hint">Your changes are kept while you move around. Saving makes a new version; the one you opened stays as it is.</p>
              )}
            </div>
          </aside>
        )}
        {showPreview && (
          <section className={"ax-ed-preview" + (problem ? " is-stale" : "")} aria-label="Preview">
            <div className="ax-ed-views">
              {narrow ? (
                <Button size="sm" variant="ghost" icon={<ArrowLeft size={14} aria-hidden="true" />} onClick={() => setPhonePane("form")}>
                  Back to editing
                </Button>
              ) : (
                <IconButton
                  label={formOpen ? "Hide the form" : "Show the form"}
                  icon={formOpen ? <PanelLeftClose size={16} aria-hidden="true" /> : <PanelLeft size={16} aria-hidden="true" />}
                  onClick={() => setFormOpen(!formOpen)}
                />
              )}
              {master && (
                <Segmented
                  label="Show"
                  value={shownView}
                  onChange={setView}
                  options={[
                    { value: "copy", label: "This copy" },
                    ...(narrow ? [] : [{ value: "both" as CopyView, label: "Side by side" }]),
                    { value: "original", label: "Your original" },
                  ]}
                />
              )}
              {master && <WordCheck master={master} />}
            </div>
            {shownView === "both" ? (
              <div className="ax-ed-compare">
                <figure>
                  <figcaption>This copy</figcaption>
                  <PdfPages data={preview?.bytes ?? null} label="This copy" />
                </figure>
                <figure>
                  <figcaption>Your original</figcaption>
                  <PdfPages data={original.data} error={original.error} label="Your original" />
                </figure>
              </div>
            ) : shownView === "original" ? (
              <PdfPages data={original.data} error={original.error} label="Your original" />
            ) : (
              <PdfPages data={preview?.bytes ?? null} label="Resume" />
            )}
            {problem && shownView !== "original" && (
              <div className="ax-notice is-bad ax-ed-errors" role="alert">
                <strong>The preview could not be updated.</strong>
                <ul>
                  {(errors.length ? errors : [{ line: null, message: problem }]).slice(0, 5).map((e, i) => (
                    <li key={i}>
                      {e.line ? (
                        <button
                          type="button"
                          className="ax-link"
                          onClick={() => {
                            setFormOpen(true);
                            setPhonePane("form");
                            setTab("source");
                          }}
                        >
                          Line {e.line}
                        </button>
                      ) : null}{" "}
                      {e.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

/** The word check beside the two documents: all you need to decide, and it's the server's. */
function WordCheck({ master }: { master: MasterView }) {
  const v = master.verification || {};
  const missing = v.missing?.length ?? 0;
  const added = v.added?.length ?? 0;
  if (!missing && !added) return <span className="ax-ed-check">Every word matches your original.</span>;
  return (
    <Tooltip
      label={
        <>
          {!!missing && <>Missing: {v.missing!.slice(0, 12).join(", ")}. </>}
          {!!added && <>Not in your original: {v.added!.slice(0, 12).join(", ")}.</>}
        </>
      }
    >
      <span className="ax-ed-check">
        {[missing ? `${missing} missing` : "", added ? `${added} added` : ""].filter(Boolean).join(", ")} {missing + added === 1 ? "word" : "words"}
      </span>
    </Tooltip>
  );
}

// ---------- Content ----------
function move<T>(list: T[], i: number, by: number) {
  const j = i + by;
  if (j < 0 || j >= list.length) return list;
  const copy = list.slice();
  [copy[i], copy[j]] = [copy[j], copy[i]];
  return copy;
}

/** A textarea as tall as what's in it, so a bullet is never a clipped slit with a scrollbar. */
function GrowingText(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const area = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = el.scrollHeight + 2 + "px";
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(el.parentElement ?? el);
    return () => observer.disconnect();
  }, [props.value]);
  return <textarea ref={area} rows={1} {...props} className={"ax-control ax-ed-grow " + (props.className || "")} />;
}

function ShowToggle({ on, name, onChange }: { on: boolean; name: string; onChange: (on: boolean) => void }) {
  return (
    <IconButton
      label={on ? `Hide ${name} from the resume` : `Show ${name} on the resume`}
      aria-pressed={!on}
      icon={on ? <Eye size={15} aria-hidden="true" /> : <EyeOff size={15} aria-hidden="true" />}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!on);
      }}
    />
  );
}

function Mover({ name, onMove, first, last }: { name: string; onMove: (by: number) => void; first: boolean; last: boolean }) {
  return (
    <>
      <IconButton
        label={`Move ${name} up`}
        disabled={first}
        icon={<ArrowUp size={14} aria-hidden="true" />}
        onClick={(e) => {
          e.stopPropagation();
          onMove(-1);
        }}
      />
      <IconButton
        label={`Move ${name} down`}
        disabled={last}
        icon={<ArrowDown size={14} aria-hidden="true" />}
        onClick={(e) => {
          e.stopPropagation();
          onMove(1);
        }}
      />
    </>
  );
}

function ContentPanel({
  doc,
  setDoc,
  disabled,
  open,
  setOpen,
}: {
  doc: Doc;
  setDoc: (fn: (d: Doc) => Doc) => void;
  disabled: boolean;
  open: Set<string>;
  setOpen: (next: Set<string>) => void;
}) {
  const toggle = (id: string) => {
    const next = new Set(open);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setOpen(next);
  };
  return (
    <fieldset className="ax-ed-fields" disabled={disabled}>
      <section className="ax-ed-group">
        <h2>Header</h2>
        <div className={"ax-ed-entry" + (open.has("@header") ? " is-open" : "")}>
          <div className="ax-ed-entry-head">
            <button type="button" className="ax-ed-entry-open" aria-expanded={open.has("@header")} onClick={() => toggle("@header")}>
              <ChevronRight size={15} aria-hidden="true" />
              <span className="ax-ed-entry-text">
                <strong>{doc.name || "No name yet"}</strong>
                <small>{doc.contact.filter(Boolean).join(" · ") || "No contact details yet"}</small>
              </span>
            </button>
          </div>
          {open.has("@header") && (
            <div className="ax-ed-entry-body">
              <Input label="Name" value={doc.name} onChange={(e) => setDoc((d) => ({ ...d, name: e.target.value }))} />
              <div className="ax-field">
                <span className="ax-label" id="ed-contact">
                  Contact line
                </span>
                {/* One field per item. A single field split on "|" trimmed each keystroke, so a space could never be typed. */}
                <ul className="ax-ed-contacts" aria-labelledby="ed-contact">
                  {doc.contact.map((item, i) => (
                    <li key={i}>
                      <input
                        className="ax-control"
                        aria-label={`Contact item ${i + 1}`}
                        value={item}
                        onChange={(e) =>
                          setDoc((d) => {
                            d.contact[i] = e.target.value;
                            return d;
                          })
                        }
                      />
                      <IconButton
                        label={`Remove ${item || "this item"}`}
                        icon={<X size={14} aria-hidden="true" />}
                        onClick={() =>
                          setDoc((d) => {
                            d.contact.splice(i, 1);
                            return d;
                          })
                        }
                      />
                    </li>
                  ))}
                </ul>
                <div>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<Plus size={14} aria-hidden="true" />}
                    onClick={() =>
                      setDoc((d) => {
                        d.contact.push("");
                        return d;
                      })
                    }
                  >
                    Add to contact line
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>
      {doc.sections.map((section, si) => (
        <section key={section.id} className={"ax-ed-group" + (section.include ? "" : " is-hidden")}>
          <div className="ax-ed-group-head">
            <input
              className="ax-ed-title"
              aria-label="Section title"
              value={section.title}
              onChange={(e) => setDoc((d) => ((d.sections[si].title = e.target.value), d))}
            />
            <span className="ax-ed-tools">
              <Mover
                name={section.title}
                first={si === 0}
                last={si === doc.sections.length - 1}
                onMove={(by) => setDoc((d) => ({ ...d, sections: move(d.sections, si, by) }))}
              />
              <ShowToggle on={section.include} name={section.title} onChange={(on) => setDoc((d) => ((d.sections[si].include = on), d))} />
            </span>
          </div>
          {section.kind === "skills" || section.kind === "text" ? (
            <div className="ax-field">
              <GrowingText
                aria-label={section.title}
                value={section.text}
                onChange={(e) => setDoc((d) => ((d.sections[si].text = e.target.value), d))}
              />
              <p className="ax-hint">One item per line. “Label: items” gets a bold label.</p>
            </div>
          ) : (
            section.entries.map((entry, ei) => {
              const isOpen = open.has(entry.id);
              const shown = entry.bullets.filter((b) => b.include).length;
              return (
                <div key={entry.id} className={"ax-ed-entry" + (isOpen ? " is-open" : "") + (entry.include ? "" : " is-hidden")}>
                  <div className="ax-ed-entry-head">
                    <button type="button" className="ax-ed-entry-open" aria-expanded={isOpen} onClick={() => toggle(entry.id)}>
                      <ChevronRight size={15} aria-hidden="true" />
                      <span className="ax-ed-entry-text">
                        <strong>{entry.heading || "Untitled"}</strong>
                        <small>{[entry.subheading, entry.dates].filter(Boolean).join(" · ") || "No details yet"}</small>
                      </span>
                      {entry.bullets.length > 0 && (
                        <span className="ax-ed-entry-count" title={`${shown} of ${entry.bullets.length} bullets on the resume`}>
                          {shown}/{entry.bullets.length}
                        </span>
                      )}
                    </button>
                    <span className="ax-ed-tools">
                      <Mover
                        name={entry.heading || "this entry"}
                        first={ei === 0}
                        last={ei === section.entries.length - 1}
                        onMove={(by) => setDoc((d) => ((d.sections[si].entries = move(d.sections[si].entries, ei, by)), d))}
                      />
                      <ShowToggle
                        on={entry.include}
                        name={entry.heading || "this entry"}
                        onChange={(on) => setDoc((d) => ((d.sections[si].entries[ei].include = on), d))}
                      />
                    </span>
                  </div>
                  {isOpen && (
                    <div className="ax-ed-entry-body">
                      <div className="ax-ed-pair">
                        {(
                          [
                            ["heading", "Heading"],
                            ["subheading", "Subheading"],
                            ["dates", "Dates"],
                            ["location", "Location"],
                          ] as const
                        ).map(([key, label]) => (
                          <Input
                            key={key}
                            label={label}
                            value={entry[key]}
                            onChange={(e) => setDoc((d) => ((d.sections[si].entries[ei][key] = e.target.value), d))}
                          />
                        ))}
                      </div>
                      {entry.bullets.length > 0 && <h3 className="ax-ed-bullets-head">Bullets</h3>}
                      {entry.bullets.map((b, bi) => {
                        const bullets = (d: Doc) => d.sections[si].entries[ei].bullets;
                        const changed = !!b.source_text && b.text !== b.source_text;
                        return (
                          <div key={b.id} className={"ax-ed-bullet" + (b.include ? "" : " is-hidden")}>
                            <input
                              type="checkbox"
                              aria-label="On the resume"
                              title="On the resume"
                              checked={b.include}
                              onChange={(e) => setDoc((d) => ((bullets(d)[bi].include = e.target.checked), d))}
                            />
                            <div className="ax-ed-bullet-text">
                              <GrowingText
                                aria-label="Bullet text"
                                value={b.text}
                                onChange={(e) => setDoc((d) => ((bullets(d)[bi].text = e.target.value), d))}
                              />
                              {changed && (
                                <small title={b.source_text}>
                                  Reworded from yours.{" "}
                                  <button type="button" className="ax-link" onClick={() => setDoc((d) => ((bullets(d)[bi].text = b.source_text), d))}>
                                    Restore your wording
                                  </button>
                                </small>
                              )}
                            </div>
                            <span className="ax-ed-tools is-column">
                              <Mover
                                name="this bullet"
                                first={bi === 0}
                                last={bi === entry.bullets.length - 1}
                                onMove={(by) => setDoc((d) => ((d.sections[si].entries[ei].bullets = move(bullets(d), bi, by)), d))}
                              />
                              {!b.unit_id && (
                                <IconButton
                                  label="Delete this bullet"
                                  icon={<Trash2 size={14} aria-hidden="true" />}
                                  onClick={() => setDoc((d) => ((d.sections[si].entries[ei].bullets = bullets(d).filter((_, k) => k !== bi)), d))}
                                />
                              )}
                            </span>
                          </div>
                        );
                      })}
                      <div>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<Plus size={14} aria-hidden="true" />}
                          onClick={() =>
                            setDoc((d) => {
                              d.sections[si].entries[ei].bullets.push({
                                id: "new-" + Date.now(),
                                text: "",
                                unit_id: null,
                                source_text: "",
                                include: true,
                              });
                              return d;
                            })
                          }
                        >
                          Add bullet
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </section>
      ))}
    </fieldset>
  );
}

// ---------- Layout ----------
function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  const id = "s-" + label.replace(/\W+/g, "-").toLowerCase();
  return (
    <div className="ax-ed-slider">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        style={{ "--fill": ((value - min) / (max - min)) * 100 + "%" } as React.CSSProperties}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <output htmlFor={id} className="mono">
        {value}
        {unit}
      </output>
    </div>
  );
}

const MARGINS = [
  ["Top", "margin_top", "margin_bottom"],
  ["Bottom", "margin_bottom", "margin_top"],
  ["Left", "margin_left", "margin_right"],
  ["Right", "margin_right", "margin_left"],
] as const;

function LayoutPanel({
  style,
  setStyle,
  meta,
  disabled,
}: {
  style: Style;
  setStyle: (p: Partial<Style>) => void;
  meta: Meta;
  disabled: boolean;
}) {
  const [linked, setLinked] = useState(
    style.margin_left === style.margin_right && style.margin_top === style.margin_bottom,
  );
  const d = meta.defaults;
  return (
    <fieldset className="ax-ed-fields" disabled={disabled}>
      <section className="ax-ed-group">
        <h2>Template</h2>
        <div className="ax-ed-templates" role="radiogroup" aria-label="Template">
          {meta.templates.map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={style.template === t.id}
              title={TEMPLATE_NOTES[t.id]}
              onClick={() =>
                setStyle({
                  template: t.id,
                  font: meta.fonts.find((f) => f.id === t.font)?.available ? t.font : style.font,
                  header: t.header,
                  headings: t.headings,
                  // Compact's tighter spacing used to stay behind after choosing another template.
                  ...(t.id === "compact"
                    ? { section_gap: 4, entry_gap: 2, bullet_gap: 0.5, line_spacing: 0.95 }
                    : { section_gap: d.section_gap, entry_gap: d.entry_gap, bullet_gap: d.bullet_gap, line_spacing: d.line_spacing }),
                })
              }
            >
              <span className={"ax-ed-tpl is-" + t.id} aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
                <i />
              </span>
              <strong>{t.label}</strong>
            </button>
          ))}
        </div>
        <p className="ax-hint">{TEMPLATE_NOTES[style.template]}</p>
      </section>
      <section className="ax-ed-group">
        <h2>Type</h2>
        <div className="ax-ed-choice">
          <span>Font</span>
          <Listbox
            label="Font"
            disabled={disabled}
            value={style.font}
            options={meta.fonts.filter((f) => f.available || f.id === style.font).map((f) => ({ value: f.id, label: f.label }))}
            onChange={(font) => setStyle({ font })}
          />
        </div>
        <Slider label="Body size" value={style.font_size} min={9} max={12.5} step={0.5} unit=" pt" onChange={(v) => setStyle({ font_size: v })} />
        <Slider label="Name size" value={style.name_size} min={12} max={26} step={1} unit=" pt" onChange={(v) => setStyle({ name_size: v })} />
        <Slider label="Line spacing" value={style.line_spacing} min={0.85} max={1.4} step={0.05} unit="×" onChange={(v) => setStyle({ line_spacing: v })} />
        <div className="ax-ed-choice">
          <span>Header</span>
          <Segmented
            label="Header alignment"
            value={style.header}
            options={[
              { value: "center", label: "Centered" },
              { value: "left", label: "Left" },
            ]}
            onChange={(h) => setStyle({ header: h })}
          />
        </div>
        <div className="ax-ed-choice">
          <span>Headings</span>
          <Segmented
            label="Section headings"
            value={style.headings}
            options={[
              { value: "rule", label: "With rule" },
              { value: "plain", label: "Plain" },
            ]}
            onChange={(h) => setStyle({ headings: h })}
          />
        </div>
      </section>
      <section className="ax-ed-group">
        <h2>Margins</h2>
        <Check label="Keep opposite sides equal" checked={linked} onChange={setLinked} />
        {MARGINS.map(([label, key, opposite]) => (
          <Slider
            key={key}
            label={label}
            value={style[key]}
            min={0.25}
            max={1.25}
            step={0.05}
            unit=" in"
            onChange={(v) => setStyle(linked ? { [key]: v, [opposite]: v } : { [key]: v })}
          />
        ))}
      </section>
      <section className="ax-ed-group">
        <h2>Spacing</h2>
        <Slider label="Before sections" value={style.section_gap} min={0} max={20} step={1} unit=" pt" onChange={(v) => setStyle({ section_gap: v })} />
        <Slider label="Between entries" value={style.entry_gap} min={0} max={14} step={1} unit=" pt" onChange={(v) => setStyle({ entry_gap: v })} />
        <Slider label="Between bullets" value={style.bullet_gap} min={0} max={8} step={0.5} unit=" pt" onChange={(v) => setStyle({ bullet_gap: v })} />
        <div>
          <Button variant="ghost" size="sm" icon={<RotateCcw size={14} aria-hidden="true" />} onClick={() => setStyle({ ...d })}>
            Reset layout
          </Button>
        </div>
      </section>
    </fieldset>
  );
}

// ---------- Source ----------
function CodeView({
  text,
  bad,
  readOnly,
  onText,
}: {
  text: string;
  bad?: Set<number | null>;
  readOnly?: boolean;
  onText?: (text: string) => void;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const lines = text.split("\n").length;
  return (
    <div className="ax-ed-code">
      <div className="ax-ed-gutter" ref={gutter} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className={bad?.has(i + 1) ? "is-bad" : ""}>
            {i + 1}
          </span>
        ))}
      </div>
      <textarea
        aria-label="LaTeX source"
        spellCheck={false}
        wrap="off"
        readOnly={readOnly}
        value={text}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop;
        }}
        onKeyDown={(e) => {
          if (e.key === "Tab" && !readOnly) {
            e.preventDefault();
            const t = e.currentTarget;
            const at = t.selectionStart;
            onText?.(text.slice(0, at) + "  " + text.slice(t.selectionEnd));
            requestAnimationFrame(() => t.setSelectionRange(at + 2, at + 2));
          }
        }}
        onChange={(e) => onText?.(e.target.value)}
      />
    </div>
  );
}

function SourcePanel({
  draft,
  errors,
  fallback,
  edit,
  reset,
}: {
  draft: Draft;
  errors: CompileError[];
  fallback: string;
  edit: (latex: string) => void;
  reset: () => void;
}) {
  const notify = useNotify();
  const [generated, setGenerated] = useState(fallback);
  const [stale, setStale] = useState(false);
  // What the controls produce now, in full. This showed the source from when the document opened, cut at 4,000 characters.
  useEffect(() => {
    if (draft.latex !== null) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<{ latex: string }>("/v2/resumes/editor/render", "POST", { doc: draft.doc, style: draft.style }, controller.signal)
        .then((r) => {
          setGenerated(r.latex);
          setStale(false);
        })
        .catch((e) => {
          if ((e as Error).name !== "AbortError") setStale(true);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [draft.doc, draft.style, draft.latex]);
  if (draft.latex === null)
    return (
      <section className="ax-ed-source">
        <div className="ax-ed-group-head">
          <p className="ax-hint">Made from your content and layout. Edit it by hand only for what the controls can't do; they pause while you do.</p>
          <Button
            size="sm"
            disabled={stale}
            title={stale ? "The source couldn't be refreshed, so editing it now would lose your latest changes." : undefined}
            onClick={async () => {
              try {
                const r = await api<{ latex: string }>("/v2/resumes/editor/render", "POST", {
                  doc: draft.doc,
                  style: draft.style,
                });
                edit(r.latex);
              } catch (e) {
                // Falling back to the older source here would have dropped the edits without a word.
                notify("error", (e as Error).message);
              }
            }}
          >
            Edit by hand
          </Button>
        </div>
        {stale && <Notice tone="warn">This may not show your latest changes: AXIOM couldn't refresh it.</Notice>}
        <CodeView text={generated} readOnly />
      </section>
    );
  return (
    <section className="ax-ed-source">
      <div className="ax-ed-group-head">
        <p className="ax-hint">You're editing by hand. The preview follows this text.</p>
        <ConfirmButton
          label="Back to visual editing"
          confirm="Discard source edits"
          note="Your hand edits to the source are discarded."
          variant="secondary"
          onConfirm={reset}
        />
      </div>
      <CodeView text={draft.latex} bad={new Set(errors.map((e) => e.line).filter(Boolean))} onText={edit} />
    </section>
  );
}
