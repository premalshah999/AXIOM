"""Uploaded resume variants (upload, download, remove) and the profile photo."""

import hashlib
import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader
from sqlalchemy import func, select

from ...db import Resume, Run, now, record
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
    async def upload_resume(file: UploadFile = File(...), name: str = Form(...), roles: str = Form(""), reuse: bool = False):
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            raise ValueError("This file is over 5 MB. Save a smaller PDF of your resume and add that.")
        if not content.startswith(b"%PDF-"):
            # A Word file renamed .pdf, usually.
            raise ValueError("This file isn't really a PDF. Open your resume and choose File, then Save As or Export, then PDF, and add that file.")
        if not name.strip() or len(name) > 120 or len(roles) > 2000:
            raise ValueError("Give this resume a short name and role tags")
        from ...features.resumes.master import picture_pages, readable_text

        refusal = ""
        warnings = []
        try:
            reader = PdfReader(io.BytesIO(content))
            # Most "locked" resumes are only restricted (no editing or copying) and open without a password: Word's and Preview's
            # protected exports, resume builders, a print-to-PDF with an owner password. Only a PDF that wants a password is locked.
            if reader.is_encrypted and not reader.decrypt(""):
                refusal = "This PDF asks for a password to open. Open it, then choose File, then Export or Print, then Save as PDF, and add that copy."
            elif len(reader.pages) > 20:
                refusal = "This PDF is longer than 20 pages. A resume is one or two pages: add the resume itself."
            else:
                whole = readable_text(reader)
                text = whole[:40000]
                pictures = picture_pages(reader)
                if len(whole) > len(text):
                    warnings.append("This resume is very long, so AXIOM reads only the start of it. A resume of one or two pages works best.")
        except Exception:
            refusal = "This PDF is damaged or locked in a way AXIOM can't read. Save your resume as a new PDF and add that."
        if refusal:
            raise ValueError(refusal)
        # A scan can carry a scanner's footer, so a few words don't make it readable.
        if len(text.strip()) < 30 or len(pictures) == len(reader.pages):
            raise ValueError(
                "This PDF is a picture of your resume, so AXIOM can't read the words. Open the original in Word, Pages or "
                "Google Docs, choose File, then Save As or Download, then PDF, and add that file."
            )
        # A font saved without its letters reads as "(cid:12)" and symbols: there is text, and none of it is words.
        if sum(c.isalpha() for c in text) < 0.5 * sum(not c.isspace() for c in text):
            raise ValueError(
                "The words in this PDF come out as symbols when AXIOM reads them, so it can't use them. Open the original in Word, Pages or "
                "Google Docs, choose File, then Save As or Download, then PDF, and add that file."
            )
        if pictures:
            pages = ", ".join(str(n) for n in pictures)
            warnings.append(
                f"{'Page' if len(pictures) == 1 else 'Pages'} {pages} of this PDF {'is a picture' if len(pictures) == 1 else 'are pictures'}, so AXIOM "
                "can't read the words there. Save your resume as a new PDF from Word, Pages or Google Docs and add that, or anything on those pages will be missed."
            )
        sha = hashlib.sha256(content).hexdigest()
        filename = sha + ".pdf"
        with db.exclusive() as s:
            same = s.scalar(select(Resume).where(Resume.sha256 == sha))
            if same and reuse:
                # The setup guide: the same file again, after a failed read or a guide closed half way, is that resume.
                result = record(same)
                return {**{k: v for k, v in result.items() if k not in {"text", "filename"}}, "warnings": warnings}
            if same:
                raise ValueError("You've already added this resume.")
            if s.scalar(select(func.count()).select_from(Resume).where(Resume.demo.is_(False))) >= 10:
                raise ValueError("You can keep up to 10 resumes. Remove one first.")
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
        return {**{k: v for k, v in result.items() if k not in {"text", "filename"}}, "warnings": warnings}

    @r.get("/api/resumes/{resume_id}/download")
    def download_resume(resume_id: str):
        with db.session() as s:
            row = s.get(Resume, resume_id)
            if not row:
                raise HTTPException(404)
            path = config.data_dir / "resumes" / row.filename
        return FileResponse(path, media_type="application/pdf", filename="resume.pdf")

    @r.post("/api/resumes/{resume_id}/prefer")
    def prefer_resume(resume_id: str):
        """Make this the resume applications attach when a job has not chosen another: the newest one (latest_resume_id)."""
        with db.exclusive() as s:
            row = s.get(Resume, resume_id)
            if not row:
                raise HTTPException(404)
            row.created_at = now()
        return {"preferred": True}

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
