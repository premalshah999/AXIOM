import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** One line of what is missing and the one action that fixes it. */
export function Empty({
  title,
  hint,
  action,
  icon: Icon,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="ax-empty">
      {Icon && <Icon size={20} aria-hidden="true" />}
      <p className="ax-empty-title">{title}</p>
      {hint && <p className="ax-empty-hint">{hint}</p>}
      {action && <div className="ax-empty-action">{action}</div>}
    </div>
  );
}
