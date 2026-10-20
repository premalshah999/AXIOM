// Resume editor: structured content + layout controls with a live PDF preview,
// and a source view for direct LaTeX edits. Every keystroke recompiles after a
// short pause; the last good preview stays visible while you type.
import { useEffect, useMemo, useRef, useState } from "react";
import { PdfPages } from "./pdfpages";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Download,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { api, ApiError, SIGNED_OUT } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import { Confirm, ErrorLine, Loading } from "./ui";

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
  master?: {
    id: string;
    status: string;
    source_resume_id: string;
    verification: { coverage?: number; faithful?: boolean };
  };
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
  url: string;
  bytes: ArrayBuffer;
  pages: number;
  ms: number;
} | null;

const TEMPLATE_NOTES: Record<string, string> = {
  classic:
    "Serif, centered name, ruled headings. The safest choice for most applications.",
  modern: "Clean sans-serif, left-aligned header. Reads well on screens.",
  compact: "Tighter spacing and plain headings. Fits more on one page.",
};

export function ResumeEditor() {
  const { route, go } = useWorkspace();
  const artifact = route.artifact || "";
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
  const unavailable =
    loaded.error instanceof ApiError &&
    loaded.error.status === 404 &&
    !artifact;
  if (
    isMaster &&
    loaded.error instanceof ApiError &&
    loaded.error.status === 404
  )
    return (
      <div className="page">
        <p className="muted">
          There is no master to edit yet. Import it from your resume in Setup ›
          Resumes first.
        </p>
        <button
          className="btn sm"
          onClick={() => go({ view: "setup", tab: "resumes" })}
        >
          Go to Resumes
        </button>
      </div>
    );
  if (unavailable)
    return (
      <div className="page">
        <p className="muted">
          The resume editor becomes available once the resume service is enabled
          on this installation.
        </p>
      </div>
    );
  if (!loaded.data || !meta.data)
    return loaded.error || meta.error ? (
      <div className="page">
        <ErrorLine error={loaded.error || meta.error} retry={loaded.refresh} />
      </div>
    ) : (
      <Loading label="Opening the editor…" />
    );
  return (
    <Workbench
      key={isMaster ? "master:" + loaded.data.master?.id : artifact || "new"}
      initial={loaded.data}
      meta={meta.data}
      back={() => go({ view: "setup", tab: "resumes" })}
      reload={loaded.refresh}
    />
  );
}

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
  const notify = useNotify();
  const { go, openJob } = useWorkspace();
  const master = initial.master;
  const [showOriginal, setShowOriginal] = useState(false);
  const [original, setOriginal] = useState<ArrayBuffer | null>(null);
  useEffect(() => {
    if (!showOriginal || original || !master) return;
    const controller = new AbortController();
    fetch(
      `/api/resumes/${encodeURIComponent(master.source_resume_id)}/download`,
      { credentials: "same-origin", signal: controller.signal },
    )
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(r.status)))
      .then(setOriginal)
      .catch((e) => {
        if ((e as Error)?.name !== "AbortError")
          notify("error", "Your original resume could not be opened.");
      });
    return () => controller.abort();
  }, [showOriginal, original, master, notify]);
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
  const [preview, setPreview] = useState<Preview>(null);
  const [errors, setErrors] = useState<CompileError[]>([]);
  const [compiling, setCompiling] = useState(false);
  const [problem, setProblem] = useState("");
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
        if (res.status === 401) window.dispatchEvent(new Event(SIGNED_OUT));
        if (res.ok) {
          const blob = await res.blob();
          const bytes = await blob.arrayBuffer();
          const url = URL.createObjectURL(blob);
          setPreview((old) => {
            if (old) URL.revokeObjectURL(old.url);
            return {
              url,
              bytes,
              pages: Number(res.headers.get("X-Pages") || 1),
              ms: Number(res.headers.get("X-Compile-Ms") || 0),
            };
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
          setProblem("Can't reach ApplyPilot to build the preview.");
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
  const status = problem
    ? {
        tone: "fix",
        text: errors.length
          ? `${errors.length} error${errors.length === 1 ? "" : "s"}; showing the last good preview`
          : problem,
      }
    : compiling
      ? { tone: "run", text: "Updating preview…" }
      : preview
        ? {
            tone: preview.pages > 1 ? "ask" : "ok",
            text: `${preview.pages} page${preview.pages === 1 ? "" : "s"} · built in ${preview.ms} ms`,
          }
        : { tone: "idle", text: "Building preview…" };
  const fileName =
    (draft.doc.name || "Resume").replace(/\s+/g, "_") + "_Resume.pdf";

  return (
    <div className="editor">
      <header className="editor-bar">
        <button className="btn sm quiet" onClick={back}>
          <ArrowLeft size={15} aria-hidden="true" /> Resumes
        </button>
        <div className="editor-title">
          <h1>{master ? "Master resume" : "Resume editor"}</h1>
          {master && (
            <span className="muted sm">
              {master.status === "approved" ? "Approved" : "Draft"} ·{" "}
              {(
                Math.floor((master.verification.coverage ?? 0) * 1000) / 10
              ).toString()}
              % of your original words
            </span>
          )}
          {draft.job && (
            <button className="link sm" onClick={() => openJob(draft.job!)}>
              For a specific job
            </button>
          )}
        </div>
        <span
          className={"editor-status tone-" + status.tone}
          role="status"
          aria-live="polite"
        >
          <i aria-hidden="true" />
          {status.text}
        </span>
        <div className="editor-actions">
          {preview && (
            <a className="btn sm" href={preview.url} download={fileName}>
              <Download size={14} aria-hidden="true" /> PDF
            </a>
          )}
          {master ? (
            <button
              className="btn sm primary"
              disabled={pending !== null || !!problem || draft.latex !== null}
              title={
                draft.latex !== null
                  ? "A hand-edited source can't become the master. Reset the source to save."
                  : undefined
              }
              onClick={() =>
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
                      ? "Saved as a draft master. Approve it in Resumes."
                      : `Saved as a draft master. ${Math.round((r.verification.coverage ?? 0) * 100)}% of your original words are present; review the differences before approving.`),
                )
              }
            >
              {pending ? "Saving…" : "Save draft"}
            </button>
          ) : (
            <button
              className="btn sm primary"
              disabled={pending !== null || !!problem}
              onClick={() =>
                run(
                  "save",
                  async () => {
                    const r = await api<{
                      artifact_id: string;
                      pages: number;
                      problems: string[];
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
                      : draft.job
                        ? "Saved as a new version. Applications for this job will use it."
                        : "Saved as a new version."),
                )
              }
            >
              {pending ? "Saving…" : "Save version"}
            </button>
          )}
        </div>
      </header>
      <div className="editor-grid">
        <aside className="editor-panel" aria-label="Edit resume">
          <nav className="tabs sub" aria-label="Editor views">
            {[
              ["content", "Content"],
              ["layout", "Layout"],
              ["source", "Source"],
            ].map(([id, label]) => (
              <a
                key={id}
                href={"#" + id}
                aria-current={tab === id ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  setTab(id);
                }}
              >
                {label}
                {id === "source" && draft.latex !== null && (
                  <span className="tab-flag" title="Edited by hand">
                    •
                  </span>
                )}
              </a>
            ))}
          </nav>
          <div className="editor-scroll">
            {draft.latex !== null && tab !== "source" ? (
              <div className="inline-warn">
                You are editing the source by hand, so content and layout
                controls are paused.{" "}
                <button className="link" onClick={() => setTab("source")}>
                  Open source
                </button>
              </div>
            ) : null}
            {tab === "content" && (
              <ContentPanel
                doc={draft.doc}
                setDoc={setDoc}
                disabled={draft.latex !== null}
              />
            )}
            {tab === "layout" && (
              <LayoutPanel
                style={draft.style}
                setStyle={setStyle}
                meta={meta}
                disabled={draft.latex !== null}
              />
            )}
            {tab === "source" && (
              <SourcePanel
                draft={draft}
                errors={errors}
                generated={initial.latex}
                edit={(latex) => setDraft((d) => ({ ...d, latex }))}
                reset={() => setDraft((d) => ({ ...d, latex: null }))}
                notify={notify}
              />
            )}
            {dirty && (
              <p className="muted sm editor-note">
                Unsaved changes are kept while you move around ApplyPilot.
                Saving creates a new version; the original stays.
              </p>
            )}
          </div>
        </aside>
        <section
          className={"editor-preview" + (problem ? " stale" : "")}
          aria-label="Preview"
        >
          {master && (
            <div className="preview-switch" role="group" aria-label="Show">
              <button
                className="chip"
                aria-pressed={!showOriginal}
                onClick={() => setShowOriginal(false)}
              >
                This copy
              </button>
              <button
                className="chip"
                aria-pressed={showOriginal}
                onClick={() => setShowOriginal(true)}
              >
                Your original
              </button>
            </div>
          )}
          {showOriginal ? (
            original ? (
              <PdfPages data={original} />
            ) : (
              <Loading label="Opening your original…" />
            )
          ) : preview ? (
            <PdfPages data={preview.bytes} />
          ) : (
            <Loading label="Building the first preview…" />
          )}
          {problem && !showOriginal && (
            <div className="editor-errors" role="alert">
              <strong>The preview could not be updated.</strong>
              <ul>
                {(errors.length ? errors : [{ line: null, message: problem }])
                  .slice(0, 5)
                  .map((e, i) => (
                    <li key={i}>
                      {e.line ? (
                        <button
                          className="link"
                          onClick={() => setTab("source")}
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
      </div>
    </div>
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

function ContentPanel({
  doc,
  setDoc,
  disabled,
}: {
  doc: Doc;
  setDoc: (fn: (d: Doc) => Doc) => void;
  disabled: boolean;
}) {
  return (
    <fieldset className="editor-fields" disabled={disabled}>
      <section className="editor-group">
        <h2>Header</h2>
        <label className="field">
          <span className="field-label">Name</span>
          <input
            value={doc.name}
            onChange={(e) => setDoc((d) => ({ ...d, name: e.target.value }))}
          />
        </label>
        <label className="field">
          <span className="field-label">Contact line</span>
          <input
            value={doc.contact.join(" | ")}
            onChange={(e) =>
              setDoc((d) => ({
                ...d,
                contact: e.target.value.split("|").map((x) => x.trim()),
              }))
            }
          />
          <small className="field-hint">
            Separate items with a vertical bar.
          </small>
        </label>
      </section>
      {doc.sections.map((section, si) => (
        <section
          key={section.id}
          className={"editor-group" + (section.include ? "" : " off")}
        >
          <div className="editor-group-head">
            <input
              className="editor-section-title"
              aria-label="Section title"
              value={section.title}
              onChange={(e) =>
                setDoc((d) => ((d.sections[si].title = e.target.value), d))
              }
            />
            <label className="check">
              <input
                type="checkbox"
                checked={section.include}
                onChange={(e) =>
                  setDoc(
                    (d) => ((d.sections[si].include = e.target.checked), d),
                  )
                }
              />
              <span>Show</span>
            </label>
            <span className="row-actions">
              <button
                type="button"
                className="icon-btn"
                aria-label={`Move ${section.title} up`}
                onClick={() =>
                  setDoc((d) => ({ ...d, sections: move(d.sections, si, -1) }))
                }
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Move ${section.title} down`}
                onClick={() =>
                  setDoc((d) => ({ ...d, sections: move(d.sections, si, 1) }))
                }
              >
                <ArrowDown size={14} />
              </button>
            </span>
          </div>
          {section.kind === "skills" || section.kind === "text" ? (
            <label className="field">
              <span className="field-label">Lines</span>
              <textarea
                rows={3}
                value={section.text}
                onChange={(e) =>
                  setDoc((d) => ((d.sections[si].text = e.target.value), d))
                }
              />
              <small className="field-hint">
                “Label: items” lines get a bold label.
              </small>
            </label>
          ) : (
            section.entries.map((entry, ei) => (
              <details
                key={entry.id}
                className={"editor-entry" + (entry.include ? "" : " off")}
                open={ei === 0}
              >
                <summary>
                  <span>
                    <strong>{entry.heading || "Untitled"}</strong>
                    {entry.subheading && (
                      <span className="muted"> · {entry.subheading}</span>
                    )}
                  </span>
                  <span className="muted sm">
                    {entry.bullets.filter((b) => b.include).length}/
                    {entry.bullets.length} bullets
                  </span>
                </summary>
                <div className="editor-entry-body">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={entry.include}
                      onChange={(e) =>
                        setDoc(
                          (d) => (
                            (d.sections[si].entries[ei].include =
                              e.target.checked),
                            d
                          ),
                        )
                      }
                    />
                    <span>Show this entry</span>
                  </label>
                  <div className="form-grid">
                    {(
                      [
                        ["heading", "Heading"],
                        ["subheading", "Subheading"],
                        ["dates", "Dates"],
                        ["location", "Location"],
                      ] as const
                    ).map(([key, label]) => (
                      <label className="field" key={key}>
                        <span className="field-label">{label}</span>
                        <input
                          value={entry[key]}
                          onChange={(e) =>
                            setDoc(
                              (d) => (
                                (d.sections[si].entries[ei][key] =
                                  e.target.value),
                                d
                              ),
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                  {entry.bullets.map((b, bi) => (
                    <div
                      key={b.id}
                      className={"editor-bullet" + (b.include ? "" : " off")}
                    >
                      <input
                        type="checkbox"
                        aria-label="Include bullet"
                        checked={b.include}
                        onChange={(e) =>
                          setDoc(
                            (d) => (
                              (d.sections[si].entries[ei].bullets[bi].include =
                                e.target.checked),
                              d
                            ),
                          )
                        }
                      />
                      <textarea
                        aria-label="Bullet text"
                        rows={2}
                        value={b.text}
                        onChange={(e) =>
                          setDoc(
                            (d) => (
                              (d.sections[si].entries[ei].bullets[bi].text =
                                e.target.value),
                              d
                            ),
                          )
                        }
                      />
                      <span className="editor-bullet-tools">
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label="Move bullet up"
                          onClick={() =>
                            setDoc(
                              (d) => (
                                (d.sections[si].entries[ei].bullets = move(
                                  d.sections[si].entries[ei].bullets,
                                  bi,
                                  -1,
                                )),
                                d
                              ),
                            )
                          }
                        >
                          <ArrowUp size={13} />
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label="Move bullet down"
                          onClick={() =>
                            setDoc(
                              (d) => (
                                (d.sections[si].entries[ei].bullets = move(
                                  d.sections[si].entries[ei].bullets,
                                  bi,
                                  1,
                                )),
                                d
                              ),
                            )
                          }
                        >
                          <ArrowDown size={13} />
                        </button>
                        {b.source_text && b.text !== b.source_text ? (
                          <button
                            type="button"
                            className="icon-btn"
                            aria-label="Restore your original wording"
                            title="Restore your original wording"
                            onClick={() =>
                              setDoc(
                                (d) => (
                                  (d.sections[si].entries[ei].bullets[bi].text =
                                    b.source_text),
                                  d
                                ),
                              )
                            }
                          >
                            <RotateCcw size={13} />
                          </button>
                        ) : null}
                        {!b.unit_id && (
                          <button
                            type="button"
                            className="icon-btn"
                            aria-label="Delete bullet"
                            onClick={() =>
                              setDoc(
                                (d) => (
                                  (d.sections[si].entries[ei].bullets =
                                    d.sections[si].entries[ei].bullets.filter(
                                      (_, k) => k !== bi,
                                    )),
                                  d
                                ),
                              )
                            }
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </span>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="btn sm quiet"
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
                    <Plus size={14} aria-hidden="true" /> Add bullet
                  </button>
                </div>
              </details>
            ))
          )}
        </section>
      ))}
      <p className="muted sm">
        Your edits are saved as your own wording. Bullets that came from your
        history keep a link to it, so you can restore the original.
      </p>
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
    <div className="slider">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <output htmlFor={id}>
        {value}
        {unit}
      </output>
    </div>
  );
}

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
    style.margin_left === style.margin_right &&
      style.margin_top === style.margin_bottom,
  );
  return (
    <fieldset className="editor-fields" disabled={disabled}>
      <section className="editor-group">
        <h2>Template</h2>
        <div className="template-pick" role="radiogroup" aria-label="Template">
          {meta.templates.map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={style.template === t.id}
              className="template-option"
              onClick={() =>
                setStyle({
                  template: t.id,
                  font: meta.fonts.find((f) => f.id === t.font)?.available
                    ? t.font
                    : style.font,
                  header: t.header,
                  headings: t.headings,
                  ...(t.id === "compact"
                    ? {
                        section_gap: 4,
                        entry_gap: 2,
                        bullet_gap: 0.5,
                        line_spacing: 0.95,
                      }
                    : {}),
                })
              }
            >
              <strong>{t.label}</strong>
              <small>{TEMPLATE_NOTES[t.id]}</small>
            </button>
          ))}
        </div>
      </section>
      <section className="editor-group">
        <h2>Type</h2>
        <label className="field">
          <span className="field-label">Font</span>
          <select
            value={style.font}
            onChange={(e) => setStyle({ font: e.target.value })}
          >
            {meta.fonts.map((f) => (
              <option key={f.id} value={f.id} disabled={!f.available}>
                {f.label}
                {f.available ? "" : " (not installed)"}
              </option>
            ))}
          </select>
        </label>
        <Slider
          label="Body size"
          value={style.font_size}
          min={9}
          max={12.5}
          step={0.5}
          unit=" pt"
          onChange={(v) => setStyle({ font_size: v })}
        />
        <Slider
          label="Name size"
          value={style.name_size}
          min={12}
          max={26}
          step={1}
          unit=" pt"
          onChange={(v) => setStyle({ name_size: v })}
        />
        <Slider
          label="Line spacing"
          value={style.line_spacing}
          min={0.85}
          max={1.4}
          step={0.05}
          unit="×"
          onChange={(v) => setStyle({ line_spacing: v })}
        />
        <div className="seg" role="group" aria-label="Header alignment">
          <span>Header</span>
          {(["center", "left"] as const).map((h) => (
            <button
              key={h}
              type="button"
              aria-pressed={style.header === h}
              onClick={() => setStyle({ header: h })}
            >
              {h === "center" ? "Centered" : "Left"}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Section headings">
          <span>Headings</span>
          {(["rule", "plain"] as const).map((h) => (
            <button
              key={h}
              type="button"
              aria-pressed={style.headings === h}
              onClick={() => setStyle({ headings: h })}
            >
              {h === "rule" ? "With rule" : "Plain"}
            </button>
          ))}
        </div>
      </section>
      <section className="editor-group">
        <h2>Margins</h2>
        <label className="check">
          <input
            type="checkbox"
            checked={linked}
            onChange={(e) => setLinked(e.target.checked)}
          />
          <span>Keep opposite sides equal</span>
        </label>
        <Slider
          label="Top"
          value={style.margin_top}
          min={0.25}
          max={1.25}
          step={0.05}
          unit=" in"
          onChange={(v) =>
            setStyle(
              linked ? { margin_top: v, margin_bottom: v } : { margin_top: v },
            )
          }
        />
        <Slider
          label="Bottom"
          value={style.margin_bottom}
          min={0.25}
          max={1.25}
          step={0.05}
          unit=" in"
          onChange={(v) =>
            setStyle(
              linked
                ? { margin_top: v, margin_bottom: v }
                : { margin_bottom: v },
            )
          }
        />
        <Slider
          label="Left"
          value={style.margin_left}
          min={0.25}
          max={1.25}
          step={0.05}
          unit=" in"
          onChange={(v) =>
            setStyle(
              linked ? { margin_left: v, margin_right: v } : { margin_left: v },
            )
          }
        />
        <Slider
          label="Right"
          value={style.margin_right}
          min={0.25}
          max={1.25}
          step={0.05}
          unit=" in"
          onChange={(v) =>
            setStyle(
              linked
                ? { margin_left: v, margin_right: v }
                : { margin_right: v },
            )
          }
        />
      </section>
      <section className="editor-group">
        <h2>Spacing</h2>
        <Slider
          label="Before sections"
          value={style.section_gap}
          min={0}
          max={20}
          step={1}
          unit=" pt"
          onChange={(v) => setStyle({ section_gap: v })}
        />
        <Slider
          label="Between entries"
          value={style.entry_gap}
          min={0}
          max={14}
          step={1}
          unit=" pt"
          onChange={(v) => setStyle({ entry_gap: v })}
        />
        <Slider
          label="Between bullets"
          value={style.bullet_gap}
          min={0}
          max={8}
          step={0.5}
          unit=" pt"
          onChange={(v) => setStyle({ bullet_gap: v })}
        />
        <button
          type="button"
          className="btn sm quiet"
          onClick={() => setStyle({ ...meta.defaults })}
        >
          <RotateCcw size={14} aria-hidden="true" /> Reset layout
        </button>
      </section>
    </fieldset>
  );
}

// ---------- Source ----------
function SourcePanel({
  draft,
  errors,
  generated,
  edit,
  reset,
}: {
  draft: Draft;
  errors: CompileError[];
  generated: string;
  edit: (latex: string) => void;
  reset: () => void;
  notify: (tone: "ok" | "error" | "info", text: string) => void;
}) {
  const [text, setText] = useState(draft.latex ?? "");
  const gutter = useRef<HTMLDivElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (draft.latex !== null) setText(draft.latex);
  }, [draft.latex]);
  const bad = new Set(errors.map((e) => e.line).filter(Boolean));
  if (draft.latex === null)
    return (
      <div className="editor-group">
        <h2>LaTeX source</h2>
        <p className="muted sm">
          The source is generated from your content and layout. Edit it by hand
          only for something the controls can't do; content and layout controls
          pause while you do.
        </p>
        <pre className="source-view">{generated.slice(0, 4000)}</pre>
        <button
          type="button"
          className="btn sm"
          onClick={async () => {
            try {
              const r = await api<{ latex: string }>(
                "/v2/resumes/editor/render",
                "POST",
                {
                  doc: draft.doc,
                  style: draft.style,
                },
              );
              edit(r.latex);
            } catch {
              edit(generated);
            }
          }}
        >
          Edit source by hand
        </button>
      </div>
    );
  const lines = text.split("\n").length;
  return (
    <div className="editor-group">
      <div className="editor-group-head">
        <h2>LaTeX source</h2>
        <Confirm
          label="Back to visual editing"
          confirm="Discard source edits"
          note="Your hand edits to the source are discarded."
          onConfirm={reset}
        />
      </div>
      <div className="code">
        <div className="code-gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => (
            <span key={i} className={bad.has(i + 1) ? "bad" : ""}>
              {i + 1}
            </span>
          ))}
        </div>
        <textarea
          ref={area}
          aria-label="LaTeX source"
          spellCheck={false}
          wrap="off"
          value={text}
          onScroll={(e) => {
            if (gutter.current)
              gutter.current.scrollTop = (
                e.target as HTMLTextAreaElement
              ).scrollTop;
          }}
          onKeyDown={(e) => {
            if (e.key === "Tab") {
              e.preventDefault();
              const t = e.currentTarget;
              const at = t.selectionStart;
              const next =
                text.slice(0, at) + "  " + text.slice(t.selectionEnd);
              setText(next);
              edit(next);
              requestAnimationFrame(() => t.setSelectionRange(at + 2, at + 2));
            }
          }}
          onChange={(e) => {
            setText(e.target.value);
            edit(e.target.value);
          }}
        />
      </div>
      <p className="muted sm">
        Runs in a locked-down compiler: no shell commands and no files outside
        the document.
      </p>
    </div>
  );
}
