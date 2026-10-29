"""Uploaded resume variants (upload, download, remove) and the profile photo."""

import hashlib
import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader
from sqlalchemy import func, select

from ...db import Resume, Run, record
from ...service import ACTIVE


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/resumes")
    def resume_list():
        """Uploaded resume variants without their text (the onboarding resume step reads this)."""
        with db.session() as s:
            rows = s.scalars(
                select(Resume).where(Resume.demo.is_(False)).order_by(Resume.created_at.desc())
            ).all()
            return [
                {"id": row.id, "name": row.name, "roles": list(row.roles or []), "created_at": row.created_at}
                for row in rows
            ]

    @r.post("/api/resumes")
    async def upload_resume(file: UploadFile = File(...), name: str = Form(...), roles: str = Form("")):
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise ValueError("Upload a PDF no larger than 5 MB")
        if not name.strip() or len(name) > 120 or len(roles) > 2000:
            raise ValueError("Give this resume a short name and role tags")
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted or len(reader.pages) > 20:
                raise ValueError()
            text = "\n".join(page.extract_text() or "" for page in reader.pages)[:40000]
        except Exception:
            raise ValueError("The PDF is encrypted, damaged, or longer than 20 pages") from None
        if len(text.strip()) < 30:
            raise ValueError("The PDF needs extractable text for classification. Export a text-based PDF.")
        sha = hashlib.sha256(content).hexdigest()
        filename = sha + ".pdf"
        with db.exclusive() as s:
            if s.scalar(select(Resume.id).where(Resume.sha256 == sha)):
                raise ValueError("This exact resume is already uploaded")
            if s.scalar(select(func.count()).select_from(Resume).where(Resume.demo.is_(False))) >= 10:
                raise ValueError("Keep up to 10 resume variants. Remove an unused variant first.")
            path = config.data_dir / "resumes" / filename
            path.write_bytes(content)
            path.chmod(0o600)
            row = Resume(
                name=name.strip(),
                filename=filename,
                sha256=sha,
                roles=[r.strip() for r in roles.split(",") if r.strip()],
                text=text,
            )
            s.add(row)
            s.flush()
            result = record(row)
        return {k: v for k, v in result.items() if k not in {"text", "filename"}}

    @r.get("/api/resumes/{resume_id}/download")
    def download_resume(resume_id: str):
        with db.session() as s:
            row = s.get(Resume, resume_id)
            if not row:
                raise HTTPException(404)
            path = config.data_dir / "resumes" / row.filename
        return FileResponse(path, media_type="application/pdf", filename="resume.pdf")

    @r.delete("/api/resumes/{resume_id}")
    def delete_resume(resume_id: str):
        with db.exclusive() as s:
            row = s.get(Resume, resume_id)
            if not row:
                raise HTTPException(404)
            if s.scalar(select(Run.id).where(Run.resume_id == resume_id, Run.state.in_(ACTIVE))):
                raise ValueError("This resume is in use by an active run")
            # Keep the immutable file for historical receipts; remove its inventory entry.
            s.delete(row)
        return {"deleted": True}

    @r.get("/api/profile/photo")
    def photo_info():
        """The saved profile photo's description ({"saved": false} when none); Setup › Profile shows it."""
        from ... import profile_photo

        return profile_photo.describe(db)

    @r.post("/api/profile/photo")
    async def photo_upload(file: UploadFile = File(...)):
        """Save the profile photo (JPEG, PNG, GIF or WebP, at most 5 MB); applications asking for a photo attach it."""
        from ... import profile_photo

        content = await file.read(profile_photo.MAX_BYTES + 1)
        return profile_photo.save(db, config.data_dir, file.filename or "photo", content)

    @r.get("/api/profile/photo/file")
    def photo_file():
        from ... import profile_photo

        path = profile_photo.path_for(db, config.data_dir)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type=db.get_setting(profile_photo.SETTING, {}).get("content_type", "image/jpeg"))

    @r.delete("/api/profile/photo")
    def photo_remove():
        from ... import profile_photo

        return {"deleted": profile_photo.remove(db, config.data_dir)}

    return r
