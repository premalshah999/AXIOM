// Small pieces the Profile page is made from: a titled group of fields on a 6-column grid, a tag input, a slider, a pick list
// and the date words. Nothing here knows the profile; profile.tsx and its section files compose them.
import { X } from "lucide-react";
import { useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Listbox, type ListboxOption } from "./ui/Listbox";

/** A titled group inside a section's panel. Its fields sit on a 6-column grid: give each `pf-s2` (a third), `pf-s3` (a half) or nothing (the row). */
export function Group({
  title,
  hint,
  children,
  actions,
}: {
  title: string;
  hint?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="pf-group">
      <header className="pf-group-head">
        <div>
          <h3>{title}</h3>
          {hint && <p className="ax-hint">{hint}</p>}
        </div>
        {actions}
      </header>
      <div className="pf-fields">{children}</div>
    </section>
  );
}

/** A section's own title with one line of what it is for. Actions sit at the right. */
export function SectionHead({ title, line, actions }: { title: string; line?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="pf-section-head">
      <div>
        <h2>{title}</h2>
        {line && <p>{line}</p>}
      </div>
      {actions && <div className="pf-section-actions">{actions}</div>}
    </header>
  );
}

/**
 * A list as tags: type and press Enter or a comma to add (a pasted list is split), Backspace on an empty box takes the last
 * one back, the cross on a tag removes it. The value stays an array, in the order added.
 */
export function Tags({
  label,
  hint,
  value,
  onChange,
  placeholder,
  className = "",
}: {
  label: string;
  hint?: ReactNode;
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  className?: string;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const add = (raw: string) => {
    const parts = raw
      .split(/[,\n]/)
      .map((x) => x.trim())
      .filter(Boolean);
    setText("");
    if (!parts.length) return;
    const next = [...value];
    for (const part of parts) if (!next.some((x) => x.toLowerCase() === part.toLowerCase())) next.push(part);
    if (next.length !== value.length) onChange(next);
  };
  return (
    <div className={"ax-field " + className}>
      <label className="ax-label" htmlFor={id}>
        {label}
      </label>
      <div className="pf-tags" onClick={() => input.current?.focus()}>
        {value.map((tag) => (
          <span className="pf-tag" key={tag}>
            <span title={tag}>{tag}</span>
            <button type="button" aria-label={`Remove ${tag}`} onClick={() => onChange(value.filter((x) => x !== tag))}>
              <X size={12} aria-hidden="true" />
            </button>
          </span>
        ))}
        <input
          ref={input}
          id={id}
          className="pf-tags-input"
          autoComplete="off"
          placeholder={value.length ? "" : placeholder || "Type and press Enter"}
          value={text}
          onChange={(e) => (e.target.value.includes(",") ? add(e.target.value) : setText(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(text);
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          onBlur={() => add(text)}
        />
      </div>
      {hint && <p className="ax-hint">{hint}</p>}
    </div>
  );
}

/** A number on a slider, with its value beside it. */
export function Range({
  label,
  hint,
  value,
  onChange,
  min = 0,
  max = 100,
  className = "",
}: {
  label: string;
  hint?: ReactNode;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  className?: string;
}) {
  const id = useId();
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <div className={"ax-field pf-range " + className}>
      <label className="ax-label" htmlFor={id}>
        {label}
      </label>
      <div className="pf-range-row">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={1}
          value={value}
          style={{ "--pct": pct + "%" } as CSSProperties}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <output className="mono" htmlFor={id}>
          {value}
        </output>
      </div>
      {hint && <p className="ax-hint">{hint}</p>}
    </div>
  );
}

/** A labelled designed menu for a choice in a form (Listbox, filled to the field's width). */
export function Pick<T extends string>({
  label,
  value,
  options,
  onChange,
  className = "",
}: {
  label: string;
  value: T;
  options: ListboxOption<T>[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={"ax-field pf-pick " + className}>
      <span className="ax-label">{label}</span>
      <Listbox label={label} value={value} options={options} onChange={onChange} />
    </div>
  );
}

// ---------- dates, in words ----------
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2025-12" as "Dec 2025". Anything else is shown as typed. */
function monthYear(value: string) {
  const m = /^(\d{4})-(\d{1,2})$/.exec(value.trim());
  if (!m || +m[2]! < 1 || +m[2]! > 12) return value.trim();
  return `${MONTHS[+m[2]! - 1]} ${m[1]}`;
}

/** The span of a role: "Dec 2025 – Present", "Jul 2025 – May 2026", or just the start. */
export function span(start: string, end: string, current: boolean) {
  const from = monthYear(start);
  const to = current ? "Present" : monthYear(end);
  return [from, to].filter(Boolean).join(" – ");
}

/** A degree's end: a date still ahead reads "Expected May 2027". */
export function finish(end: string) {
  const when = monthYear(end);
  if (!when) return "";
  const m = /^(\d{4})-(\d{1,2})$/.exec(end.trim());
  const now = new Date();
  const ahead = m && (+m[1]! > now.getFullYear() || (+m[1]! === now.getFullYear() && +m[2]! > now.getMonth() + 1));
  return ahead ? "Expected " + when : when;
}
