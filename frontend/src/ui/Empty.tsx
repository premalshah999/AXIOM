import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { FanMark } from "../identity/FanMark";

const UNLIT = { stage: 0, tone: "idle", label: "" } as const;

/** What is missing and the one action that fixes it, beside a fanlight with nothing lit. The look, including whether `icon` shows, is in components.css. */
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
      <FanMark fan={UNLIT} size={24} label={false} />
      <p className="ax-empty-title">{title}</p>
      {hint && <p className="ax-empty-hint">{hint}</p>}
      {action && <div className="ax-empty-action">{action}</div>}
    </div>
  );
}
