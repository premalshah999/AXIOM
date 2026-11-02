// PDFs inside the app, drawn with PDF.js onto canvases: the same everywhere, including the native window, where a link that opens a
// new tab goes nowhere and the server's "attachment" answer would only download. The library loads when the first one is shown. Each
// update renders off-screen and swaps in when complete, so the page never flickers while typing.
import { useEffect, useRef, useState } from "react";
import { plainError } from "./format";
import { FileText, Maximize2, Minus, Plus, StretchHorizontal } from "lucide-react";
import { signedOut } from "./api";
import { IconButton } from "./ui/index";
import "./styles/pdf.css";

type PdfJs = typeof import("pdfjs-dist");
let loader: Promise<PdfJs> | null = null;
function pdfjs() {
  // The legacy build, because the modern one needs Map.getOrInsertComputed, which WebKit on macOS 15 does not have: every
  // preview in the installed app said "could not be drawn" (2026-10-31). Safari on that version fails the same way.
  loader ??= Promise.all([
    import("pdfjs-dist/legacy/build/pdf.mjs"),
    import("pdfjs-dist/legacy/build/pdf.worker.min.mjs?url"),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = (worker as { default: string }).default;
    return lib as unknown as PdfJs;
  });
  return loader;
}

// A stored PDF never changes under its address (an edit is a new version), so one read per session: the thumbnail and the preview share
// it, and coming back to the page draws at once.
const files = new Map<string, Promise<ArrayBuffer>>();
function readPdf(url: string): Promise<ArrayBuffer> {
  let file = files.get(url);
  if (!file) {
    file = fetch(url, { credentials: "same-origin" }).then((r) => {
      if (r.status === 401) signedOut("Your session ended. Sign in again.");
      if (!r.ok) throw new Error(r.status === 404 ? "This PDF is no longer on file." : "This PDF could not be opened.");
      return r.arrayBuffer();
    });
    files.set(url, file);
    file.catch(() => files.delete(url));
  }
  return file;
}

/** The bytes of a stored PDF, or why not. `url` null reads nothing yet. */
export function usePdf(url: string | null) {
  const [state, setState] = useState<{ url: string | null; data: ArrayBuffer | null; error: string }>({ url: null, data: null, error: "" });
  useEffect(() => {
    if (!url) return;
    let current = true;
    readPdf(url).then(
      (data) => current && setState({ url, data, error: "" }),
      (e: Error) => current && setState({ url, data: null, error: plainError(e.message) }),
    );
    return () => {
      current = false;
    };
  }, [url]);
  // The previous address's answer is never shown under this one.
  return state.url === url ? state : { url, data: null, error: "" };
}

/** Save bytes under a real name. The server would call every one of them resume.pdf. */
export function savePdf(data: ArrayBuffer | Blob, name: string) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = /\.pdf$/i.test(name) ? name : name + ".pdf";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export const pdfName = (name: string) => (name || "Resume").replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim() + ".pdf";

/** Download a stored PDF under a real name. */
export async function downloadPdf(url: string, name: string) {
  savePdf(await readPdf(url), pdfName(name));
}

function drawPage(
  viewport: { width: number; height: number },
  render: (canvas: HTMLCanvasElement) => Promise<unknown>,
  ratio: number,
  label: string,
) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = Math.floor(viewport.width / ratio) + "px";
  canvas.style.height = Math.floor(viewport.height / ratio) + "px";
  canvas.className = "ax-pdf-page";
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", label);
  return render(canvas).then(() => canvas);
}

type Zoom = "page" | "width" | number;

/**
 * The pages of a PDF with zoom. `fit`: "page" shows a whole page in the space there is, "width" fills the width and scrolls. Null data
 * with no error draws the blank sheet while it's coming.
 */
export function PdfPages({
  data,
  fit = "page",
  error: outerError,
  label = "Resume",
}: {
  data: ArrayBuffer | null;
  fit?: "page" | "width";
  error?: string;
  label?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<Zoom>(fit);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [error, setError] = useState("");
  const [pages, setPages] = useState(0);
  const [shown, setShown] = useState(1);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let timer = 0;
    const read = () => {
      const style = getComputedStyle(el);
      setSize({
        w: el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
        h: el.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
      });
    };
    read();
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(read, 60);
    });
    observer.observe(el);
    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const el = host.current;
    if (!data || !el || size.w <= 0) return;
    let cancelled = false;
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      try {
        const lib = await pdfjs();
        const loading = lib.getDocument({ data: data.slice(0) });
        task = loading;
        const doc = await loading.promise;
        const frame = document.createElement("div");
        frame.className = "ax-pdf-stack";
        const ratio = window.devicePixelRatio || 1;
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const scale =
            zoom === "width"
              ? Math.min(2.5, size.w / base.width)
              : zoom === "page"
                ? Math.min(2.5, size.w / base.width, Math.max(size.h, 200) / base.height)
                : zoom;
          const viewport = page.getViewport({ scale: scale * ratio });
          const canvas = await drawPage(viewport, (c) => page.render({ canvas: c, viewport }).promise, ratio, `${label}, page ${n} of ${doc.numPages}`);
          if (cancelled) return;
          frame.append(canvas);
        }
        if (cancelled) return;
        const old = el.querySelector(".ax-pdf-stack");
        if (old) old.replaceWith(frame);
        else el.append(frame);
        setPages(doc.numPages);
        setError("");
      } catch (e) {
        if (!cancelled) setError("This preview could not be drawn. The PDF itself is fine; use Download to open it.");
        console.warn(e);
      } finally {
        void task?.destroy();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, size.w, size.h, zoom, label]);

  const problem = outerError || error;
  const scaled = typeof zoom === "number";
  const level = zoom === "page" ? "Whole page" : zoom === "width" ? "Full width" : Math.round((zoom as number) * 100) + "%";
  const step = (by: number) => setZoom((z) => Math.max(0.5, Math.min(2.5, (scaled ? (z as number) : 1) + by)));
  return (
    <div className="ax-pdf">
      <div className="ax-pdf-tools" role="group" aria-label="Zoom">
        {pages > 1 && (
          <span className="ax-pdf-count" role="status">
            {shown} of {pages}
          </span>
        )}
        <IconButton label="Zoom out" size="sm" icon={<Minus size={14} aria-hidden="true" />} onClick={() => step(-0.15)} />
        <span className="ax-pdf-level">{level}</span>
        <IconButton label="Zoom in" size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => step(0.15)} />
        <IconButton
          label={zoom === "page" ? "Fit to width" : "Fit whole page"}
          size="sm"
          icon={zoom === "page" ? <StretchHorizontal size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
          onClick={() => setZoom((z) => (z === "page" ? "width" : "page"))}
        />
      </div>
      <div
        className="ax-pdf-host"
        ref={host}
        tabIndex={0}
        aria-label={label + " preview"}
        onScroll={(e) => {
          if (pages < 2) return;
          const el = e.currentTarget;
          setShown(Math.min(pages, Math.floor((el.scrollTop + el.clientHeight / 2) / Math.max(1, el.scrollHeight / pages)) + 1));
        }}
      >
        {!data && !problem && (
          <div className="ax-pdf-stack">
            <div className="ax-pdf-blank" role="status" aria-label="Opening the PDF" style={{ height: Math.max(200, size.h) }} />
          </div>
        )}
      </div>
      {problem && (
        <p className="ax-pdf-error" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/** The first page, small: a card's or row's picture of the real document. A failure quietly falls back to a file icon. */
export function PdfThumb({ url, width }: { url: string; width: number }) {
  const { data, error } = usePdf(url);
  const host = useRef<HTMLSpanElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const el = host.current;
    if (!data || !el) return;
    let cancelled = false;
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      try {
        const lib = await pdfjs();
        const loading = lib.getDocument({ data: data.slice(0) });
        task = loading;
        const page = await (await loading.promise).getPage(1);
        const ratio = window.devicePixelRatio || 1;
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: (width / base.width) * ratio });
        const canvas = await drawPage(viewport, (c) => page.render({ canvas: c, viewport }).promise, ratio, "");
        canvas.removeAttribute("role");
        canvas.removeAttribute("aria-label");
        canvas.setAttribute("aria-hidden", "true");
        if (!cancelled) el.replaceChildren(canvas);
      } catch (e) {
        if (!cancelled) setFailed(true);
        console.warn(e);
      } finally {
        void task?.destroy();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, width]);
  return (
    <span className="ax-pdf-thumb" ref={host} style={{ width, height: Math.round(width * 1.294) }} aria-hidden="true">
      {(error || failed) && <FileText size={Math.round(width / 4)} aria-hidden="true" />}
    </span>
  );
}
