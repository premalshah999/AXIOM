// The identity header of Profile › Personal details: the photo, the name and one line of contact, and a dialog to change the photo.
//   GET    /api/profile/photo       -> {saved: boolean, name?, content_type?, size?, sha256?, saved_at?}
//   GET    /api/profile/photo/file  -> the image bytes (404 when none)
//   POST   /api/profile/photo       -> multipart "file" (JPEG, PNG, GIF or WebP, up to 5 MB)
//   DELETE /api/profile/photo       -> {deleted}
// Isolated: nothing else on the Profile page depends on it, and a failed read leaves initials in place.
import { Camera } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api";
import { useNotify, usePoll } from "./hooks";
import { Button, ConfirmButton, Dialog, Dropzone } from "./ui/index";

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

function Avatar({ name, src, alt, large }: { name: string; src: string; alt: string; large?: boolean }) {
  return (
    <span className={"pf-avatar" + (large ? " is-large" : "")}>
      {src ? <img key={src} src={src} alt={alt} /> : <span aria-hidden="true">{initials(name)}</span>}
    </span>
  );
}

export function IdentityHeader({ name, detail }: { name: string; detail: string }) {
  const notify = useNotify();
  const info = usePoll<Info>((signal) => api("/profile/photo", "GET", undefined, signal), 120000);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "remove">("");
  const has = !!info.data?.saved;
  const saved = has ? `/api/profile/photo/file?v=${info.data?.sha256 || ""}` : "";

  useEffect(() => {
    if (!file) return setPreview("");
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const close = () => {
    setOpen(false);
    setFile(null);
    setError("");
  };
  const choose = (f: File | null) => {
    setError("");
    if (f && f.size > MAX_BYTES) return setError("That image is over 5 MB. Choose a smaller one.");
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
      await info.refresh();
      notify("ok", "Profile photo saved.");
      close();
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
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="pf-identity">
      <Avatar name={name} src={saved} alt="Your profile photo" />
      <div className="pf-identity-text">
        <strong title={name}>{name || "Your name"}</strong>
        {detail && <span title={detail}>{detail}</span>}
      </div>
      <Button size="sm" icon={<Camera size={14} aria-hidden="true" />} onClick={() => setOpen(true)}>
        {has ? "Change photo" : "Add photo"}
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Profile photo"
        sub="Optional. Used where an application asks for a photo."
        footer={
          <>
            {has && !file && (
              <span className="pf-foot-start">
                <ConfirmButton label="Remove photo" confirm="Remove" pending={busy === "remove"} onConfirm={remove} />
              </span>
            )}
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" loading={busy === "upload"} disabled={!file} onClick={upload}>
              Save photo
            </Button>
          </>
        }
      >
        <div className="pf-photo-body">
          <Avatar name={name} src={preview || saved} alt={preview ? "Preview of the new photo" : "Your profile photo"} large />
          <div className="pf-photo-drop">
            <Dropzone
              file={file}
              onFile={choose}
              accept={TYPES.join(",")}
              label="Choose a profile photo"
              title={has ? "Drop a new photo, or click to choose" : "Drop a photo, or click to choose"}
              hint="JPEG, PNG, GIF or WebP, up to 5 MB"
            />
            {error && (
              <p className="ax-error" role="alert">
                {error}
              </p>
            )}
          </div>
        </div>
      </Dialog>
    </div>
  );
}
