// Profile photo control: upload, replace and remove with a preview.
//
// Isolated on purpose. The backend route is being added separately; this is written to the shape we expect, and it
// degrades without it (a 404/405 on upload reads "not available on this server yet", the picture falls back to
// initials), so nothing else on the Profile page depends on it:
//   GET    /api/profile/photo   -> the image bytes (404 when none is set); same-origin, cookie-authenticated
//   POST   /api/profile/photo   -> multipart/form-data, field "file" (PNG, JPEG or WebP, up to 5 MB)
//   DELETE /api/profile/photo   -> removes it
// If the profile object carries `photo_url` (string) or `has_photo` (boolean) it is used instead of probing the GET.
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { useWorkspace } from "./context";
import { useNotify } from "./hooks";
import { ConfirmButton, Lede } from "./setup-kit";
import { Button, Panel } from "./ui/index";

const TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_BYTES = 5 * 1024 * 1024;

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";

export function ProfilePhoto({ name }: { name: string }) {
  const { snapshot } = useWorkspace();
  const notify = useNotify();
  const profile = snapshot.profile as { photo_url?: string; has_photo?: boolean } | undefined;
  const [version, setVersion] = useState(() => Date.now());
  // undefined: not known yet (the image request decides); true: show it; false: initials.
  const [has, setHas] = useState<boolean | undefined>(profile?.has_photo);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "remove">("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!file) return setPreview("");
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const choose = (f: File | null) => {
    setError("");
    if (!f) return setFile(null);
    if (!TYPES.includes(f.type)) return setError("Use a PNG, JPEG or WebP image.");
    if (f.size > MAX_BYTES) return setError("That image is over 5 MB. Choose a smaller one.");
    setFile(f);
  };
  const unavailable = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405);
  const upload = async () => {
    if (!file) return;
    setBusy("upload");
    setError("");
    try {
      const body = new FormData();
      body.append("file", file);
      await api("/profile/photo", "POST", body);
      setFile(null);
      setHas(true);
      setVersion(Date.now());
      if (input.current) input.current.value = "";
      notify("ok", "Profile photo saved.");
    } catch (e) {
      setError(unavailable(e) ? "Photo upload isn't available on this server yet." : (e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const remove = async () => {
    setBusy("remove");
    try {
      await api("/profile/photo", "DELETE");
      setHas(false);
      setVersion(Date.now());
      notify("ok", "Profile photo removed.");
    } catch (e) {
      notify("error", unavailable(e) ? "Photo removal isn't available on this server yet." : (e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const src = preview || (has === false ? "" : profile?.photo_url || `/api/profile/photo?v=${version}`);

  return (
    <Panel title="Photo">
      <div className="ax-photo">
        <span className="ax-photo-frame">
          {src ? (
            <img
              key={src}
              src={src}
              alt={preview ? "Preview of the new photo" : "Your profile photo"}
              onLoad={() => !preview && setHas(true)}
              onError={() => !preview && setHas(false)}
            />
          ) : (
            <span aria-hidden="true">{initials(name)}</span>
          )}
        </span>
        <div className="ax-stack" style={{ flex: "1 1 260px", gap: "var(--space-2)" }}>
          <Lede>Used where an application asks for a photo. Optional; PNG, JPEG or WebP, up to 5 MB.</Lede>
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
