import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

/** primary (at most one on a screen), secondary (hairline), ghost, danger; sm 28px or md 32px. `loading` disables and shows a spinner. */
export function Button({
  variant = "secondary",
  size = "md",
  loading,
  block,
  round,
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
  /** The round commit key, for one short word ("Send", "Add"). */
  round?: boolean;
  icon?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = [
    "ax-btn",
    variant === "secondary" ? "" : "is-" + variant,
    size === "sm" ? "is-sm" : "",
    block ? "is-block" : "",
    round ? "is-round" : "",
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
