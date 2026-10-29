import { X } from "lucide-react";
import type { Toast } from "../hooks";

/**
 * The toast stack. State lives in the app (main.tsx owns the list and `notify`; pages call `useNotify()` from hooks).
 * Outcomes announce politely and disappear; errors announce as alerts and stay until dismissed.
 */
export function ToastStack({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  const item = (t: Toast) => (
    <div key={t.id} className={"ax-toast is-" + t.tone}>
      <span>{t.text}</span>
      {t.tone === "error" && (
        <button type="button" className="ax-icon-btn" aria-label="Dismiss message" onClick={() => onDismiss(t.id)}>
          <X size={14} />
        </button>
      )}
    </div>
  );
  return (
    <div className="ax-toasts">
      <div role="status" aria-live="polite" className="ax-toast-group">
        {toasts.filter((t) => t.tone !== "error").map(item)}
      </div>
      <div role="alert" className="ax-toast-group">
        {toasts.filter((t) => t.tone === "error").map(item)}
      </div>
    </div>
  );
}
