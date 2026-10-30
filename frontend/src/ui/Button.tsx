import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

/** primary (at most one on a screen), secondary (hairline), ghost, danger; sm 28px or md 32px. `loading` disables and shows a spinner. */
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
