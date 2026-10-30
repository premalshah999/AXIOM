import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** One line: what is missing, and the one action that fixes it (drawn as a link). `icon` is accepted and not drawn. */
export function Empty({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="ax-empty">
      <p className="ax-empty-title">{title}</p>
      {hint && <p className="ax-empty-hint">{hint}</p>}
      {action && <div className="ax-empty-action">{action}</div>}
    </div>
  );
}
