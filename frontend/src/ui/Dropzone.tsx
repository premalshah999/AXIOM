import { File as FileIcon, Upload, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { IconButton } from "./Button";

const size = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function accepted(file: File, accept?: string) {
  if (!accept) return true;
  return accept.split(",").some((rule) => {
    const r = rule.trim().toLowerCase();
    if (r.startsWith(".")) return file.name.toLowerCase().endsWith(r);
    if (r.endsWith("/*")) return file.type.startsWith(r.slice(0, -1));
    return file.type === r;
  });
}

/**
 * The only way a file comes in: drag one here, or click or press Enter to choose. With `file` set it shows the file and a way to
 * remove it. A file of the wrong kind is refused here, in words, before anything is sent.
 */
export function Dropzone({
  file,
  onFile,
  accept,
  title = "Drop a file here, or click to choose",
  hint,
  label,
  disabled,
  autoFocus,
  refusal,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  accept?: string;
  /** The line inside the zone. Keep it short: "Drop your resume, or click to choose". */
  title?: ReactNode;
  /** What it takes: "PDF, up to 5 MB". */
  hint?: ReactNode;
  /** The accessible name of the file control. */
  label: string;
  disabled?: boolean;
  autoFocus?: boolean;
  /** What to say about a file of the wrong kind, in place of the general sentence. */
  refusal?: (file: File) => string;
}) {
  const [over, setOver] = useState(false);
  const [refused, setRefused] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const take = (f: File | null | undefined) => {
    if (!f) return;
    if (!accepted(f, accept)) {
      setRefused(refusal ? refusal(f) : `${f.name} isn't a file this can take.`);
      return;
    }
    setRefused("");
    onFile(f);
  };
  if (file)
    return (
      <div className="ax-drop is-filled">
        <span className="ax-drop-icon">
          <FileIcon size={16} aria-hidden="true" />
        </span>
        <span className="ax-drop-text">
          <strong title={file.name}>{file.name}</strong>
          <small className="mono">{size(file.size)}</small>
        </span>
        <IconButton label={`Remove ${file.name}`} icon={<X size={16} aria-hidden="true" />} disabled={disabled} onClick={() => onFile(null)} />
      </div>
    );
  return (
    <label
      className={"ax-drop" + (over ? " is-over" : "") + (disabled ? " is-disabled" : "")}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) take(e.dataTransfer.files?.[0]);
      }}
    >
      <input
        ref={input}
        className="sr-only"
        type="file"
        data-answer
        accept={accept}
        aria-label={label}
        disabled={disabled}
        autoFocus={autoFocus}
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <span className="ax-drop-icon">
        <Upload size={16} aria-hidden="true" />
      </span>
      <span className="ax-drop-text">
        <strong>{title}</strong>
        {refused ? (
          <small className="ax-error" role="alert">
            {refused}
          </small>
        ) : (
          hint && <small>{hint}</small>
        )}
      </span>
    </label>
  );
}
