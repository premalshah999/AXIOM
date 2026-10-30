// Renders a PDF with PDF.js onto canvases, the same in every browser.
// The library loads only when the editor opens. Each update renders off-screen
// and swaps in when complete, so the preview never flickers while typing.
import { useEffect, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { Button } from "./ui/index";

type PdfJs = typeof import("pdfjs-dist");
let loader: Promise<PdfJs> | null = null;
function pdfjs() {
  loader ??= Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]).then(([lib, worker]) => {
    lib.GlobalWorkerOptions.workerSrc = (worker as { default: string }).default;
    return lib;
  });
  return loader;
}

export function PdfPages({ data }: { data: ArrayBuffer | null }) {
  const host = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  const [width, setWidth] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = host.current;
    if (!data || !el || !width) return;
    let cancelled = false;
    (async () => {
      try {
        const lib = await pdfjs();
        const task = lib.getDocument({ data: data.slice(0) });
        const doc = await task.promise;
        const frame = document.createElement("div");
        frame.className = "ax-pdf-stack";
        const ratio = window.devicePixelRatio || 1;
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const scale =
            zoom === "fit" ? Math.min(2.5, (width - 48) / base.width) : zoom;
          const viewport = page.getViewport({ scale: scale * ratio });
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);
          canvas.style.width = Math.floor(viewport.width / ratio) + "px";
          canvas.style.height = Math.floor(viewport.height / ratio) + "px";
          canvas.className = "ax-pdf-page";
          canvas.setAttribute("role", "img");
          canvas.setAttribute("aria-label", `Page ${n} of ${doc.numPages}`);
          await page.render({ canvas, viewport }).promise;
          if (cancelled) return;
          frame.append(canvas);
        }
        if (!cancelled) {
          el.querySelector(".ax-pdf-stack")?.replaceWith(frame) ??
            el.append(frame);
          if (!el.contains(frame)) el.append(frame);
          setError("");
        }
        void task.destroy();
      } catch (e) {
        if (!cancelled)
          setError(
            "This preview could not be drawn. The PDF itself is fine; use Download to open it.",
          );
        console.warn(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, width, zoom]);

  const level = zoom === "fit" ? "Fit" : Math.round(zoom * 100) + "%";
  return (
    <div className="ax-pdf">
      <div className="ax-pdf-tools" role="group" aria-label="Zoom">
        <button
          type="button"
          className="ax-icon-btn"
          aria-label="Zoom out"
          onClick={() => setZoom((z) => Math.max(0.5, (z === "fit" ? 1 : z) - 0.15))}
        >
          <Minus size={14} />
        </button>
        <Button variant="ghost" size="sm" title="Toggle fit to width" onClick={() => setZoom((z) => (z === "fit" ? 1 : "fit"))}>
          {level}
        </Button>
        <button
          type="button"
          className="ax-icon-btn"
          aria-label="Zoom in"
          onClick={() => setZoom((z) => Math.min(2.5, (z === "fit" ? 1 : z) + 0.15))}
        >
          <Plus size={14} />
        </button>
      </div>
      <div className="ax-pdf-host" ref={host} />
      {error && (
        <p className="ax-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
