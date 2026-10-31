import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** What will appear here and the one action that gets there. `compact` for inside a small panel. */
export function Empty({
  title,
  hint,
  action,
  icon: Icon,
  compact,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
  compact?: boolean;
}) {
  return (
    <div className={"ax-empty" + (compact ? " is-compact" : "")}>
      {Icon && (
        <span className="ax-empty-icon">
          <Icon size={18} aria-hidden="true" />
        </span>
      )}
      <p className="ax-empty-title">{title}</p>
      {hint && <p className="ax-empty-hint">{hint}</p>}
      {action && <div className="ax-empty-action">{action}</div>}
    </div>
  );
}
