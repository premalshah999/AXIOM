// Settings forms keep what you typed until you press Save. Leaving the page (another Settings section, another page, closing the
// tab) would drop it without a word; this is the one place that knows something is unsaved, so the shell can ask first.
import { useEffect } from "react";

const open = new Set<symbol>();

/** Whether any form on screen holds changes that were not saved. */
export const hasUnsaved = () => open.size > 0;

/** A form reports whether it holds unsaved changes; unmounting (or saving) clears it. */
export function useUnsaved(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const id = Symbol("unsaved");
    open.add(id);
    return () => {
      open.delete(id);
    };
  }, [dirty]);
}

/** Closing or reloading the tab with unsaved changes asks the browser's own question. */
export function useLeaveWarning() {
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (!hasUnsaved()) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
}
