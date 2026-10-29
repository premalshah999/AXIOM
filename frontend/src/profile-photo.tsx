// Profile photo control: upload, replace and remove with a preview.
//   GET    /api/profile/photo       -> {saved: boolean, name?, content_type?, size?, sha256?, saved_at?}
//   GET    /api/profile/photo/file  -> the image bytes (404 when none)
//   POST   /api/profile/photo       -> multipart "file" (JPEG, PNG, GIF or WebP, up to 5 MB)
//   DELETE /api/profile/photo       -> {deleted}
// Isolated: nothing else on the Profile page depends on it, and a failed read leaves initials in place.
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useNotify, usePoll } from "./hooks";
import { ConfirmButton, Lede } from "./setup-kit";
import { Button, Panel } from "./ui/index";

type Info = { saved: boolean; sha256?: string; name?: string };
const TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
const MAX_BYTES = 5 * 1024 * 1024;

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";

export function ProfilePhoto({ name }: { name: string }) {
  const notify = useNotify();
  const info = usePoll<Info>((signal) => api("/profile/photo", "GET", undefined, signal), 120000);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "remove">("");
  const input = useRef<HTMLInputElement>(null);
  const has = !!info.data?.saved;

  useEffect(() => {
    if (!file) return setPreview("");
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const choose = (f: File | null) => {
    setError("");
    if (!f) return setFile(null);
    if (!TYPES.includes(f.type)) return setError("Use a JPEG, PNG, GIF or WebP image.");
    if (f.size > MAX_BYTES) return setError("That image is over 5 MB. Choose a smaller one.");
    setFile(f);
  };
  const upload = async () => {
    if (!file) return;
    setBusy("upload");
    setError("");
    try {
      const body = new FormData();
      body.append("file", file);
      await api("/profile/photo", "POST", body);
      setFile(null);
      await info.refresh();
      if (input.current) input.current.value = "";
      notify("ok", "Profile photo saved.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const remove = async () => {
    setBusy("remove");
    try {
      await api("/profile/photo", "DELETE");
      await info.refresh();
      notify("ok", "Profile photo removed.");
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const src = preview || (has ? `/api/profile/photo/file?v=${info.data?.sha256 || ""}` : "");

  return (
    <Panel title="Photo">
      <div className="ax-photo">
        <span className="ax-photo-frame">
          {src ? (
            <img key={src} src={src} alt={preview ? "Preview of the new photo" : "Your profile photo"} />
          ) : (
            <span aria-hidden="true">{initials(name)}</span>
          )}
        </span>
        <div className="ax-stack" style={{ flex: "1 1 260px", gap: "var(--space-2)" }}>
          <Lede>Used where an application asks for a photo. Optional; JPEG, PNG, GIF or WebP, up to 5 MB.</Lede>
          <input
            ref={input}
            type="file"
            accept={TYPES.join(",")}
            className="sr-only"
            aria-label="Choose a profile photo"
            tabIndex={-1}
            onChange={(e) => choose(e.target.files?.[0] || null)}
          />
          <div className="ax-row-end">
            {file ? (
              <>
                <Button variant="primary" size="sm" loading={busy === "upload"} onClick={upload}>
                  Save photo
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy !== ""}
                  onClick={() => {
                    setFile(null);
                    if (input.current) input.current.value = "";
                  }}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" disabled={busy !== ""} onClick={() => input.current?.click()}>
                  {has ? "Replace photo" : "Upload photo"}
                </Button>
                {has && (
                  <ConfirmButton label="Remove" confirm="Remove photo" pending={busy === "remove"} onConfirm={remove} />
                )}
              </>
            )}
          </div>
          {error && (
            <p className="ax-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}
