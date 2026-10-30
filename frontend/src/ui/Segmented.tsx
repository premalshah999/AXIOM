import type { KeyboardEvent } from "react";

/** Two to four buttons that act as one radio group. Arrow keys move the choice. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  block,
}: {
  value: T | "" | undefined;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  block?: boolean;
}) {
  const move = (e: KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = (i + step + options.length) % options.length;
    onChange(options[at].value);
    (e.currentTarget.parentElement?.children[at] as HTMLElement)?.focus();
  };
  const tab = options.findIndex((o) => o.value === value);
  return (
    <div className={"ax-seg" + (block ? " is-block" : "")} role="radiogroup" aria-label={label}>
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          tabIndex={tab === i || (tab < 0 && i === 0) ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={(e) => move(e, i)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
