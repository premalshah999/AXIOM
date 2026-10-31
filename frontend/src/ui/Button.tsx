import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-solid";

/**
 * primary (at most one on a view), secondary, ghost, danger (a destructive action that will ask first), danger-solid (the button
 * that finally does it). sm 28px or md 34px. `icon` leads the label; `loading` disables it and swaps the icon for a spinner.
 */
export function Button({
  variant = "secondary",
  size = "md",
  loading,
  block,
  icon,
  children,
  className = "",
  type = "button",
  disabled,
  ...rest
}: {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
  block?: boolean;
  icon?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = [
    "ax-btn",
    variant === "secondary" ? "" : "is-" + variant,
    size === "sm" ? "is-sm" : "",
    block ? "is-block" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={cls} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <span className="ax-spinner" aria-hidden="true" /> : icon}
      {children}
    </button>
  );
}

/** An icon alone. `label` is required: it is the accessible name. `framed` gives it a secondary button's edge; md matches a Button. */
export function IconButton({
  label,
  icon,
  size = "sm",
  framed,
  className = "",
  ...rest
}: {
  label: string;
  icon: ReactNode;
  size?: "sm" | "md";
  framed?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children">) {
  return (
    <button
      type="button"
      className={"ax-icon-btn" + (size === "md" ? " is-md" : "") + (framed ? " is-framed" : "") + (className ? " " + className : "")}
      aria-label={label}
      {...rest}
    >
      {icon}
    </button>
  );
}
