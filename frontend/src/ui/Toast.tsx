import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import type { Toast } from "../hooks";
import { IconButton } from "./Button";

const ICON = { ok: CheckCircle2, info: Info, error: CircleAlert };

/**
 * The toast stack. State lives in the app (main.tsx owns the list and `notify`; pages call `useNotify()` from hooks).
 * Outcomes announce politely and disappear; errors announce as alerts and stay until dismissed.
 */
export function ToastStack({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  const item = (t: Toast) => {
    const Icon = ICON[t.tone];
    return (
      <div key={t.id} className={"ax-toast is-" + t.tone}>
        <Icon size={16} aria-hidden="true" />
        <span>{t.text}</span>
        {t.tone === "error" && (
          <IconButton label="Dismiss message" icon={<X size={14} aria-hidden="true" />} onClick={() => onDismiss(t.id)} />
        )}
      </div>
    );
  };
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
