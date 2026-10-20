"""Shared synthetic mailbox data for correlation and receipt regressions."""

import base64
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from jobpilot.db import Job, Mailbox, Run, now, record
from jobpilot.mail import RuleInput


def message(body="Your verification code is 483921", *, html=False, message_id="message-1", received=None):
    return {
        "id": message_id,
        "internalDate": str(int((received or time.time()) * 1000)),
        "payload": {
            "mimeType": "text/html" if html else "text/plain",
            "headers": [
                {"name": "From", "value": "Example Careers <no-reply@notify.example.test>"},
                {"name": "To", "value": "Alex <alex@example.test>"},
                {
                    "name": "Authentication-Results",
                    "value": "mx.google.com; dkim=pass; dmarc=pass header.from=notify.example.test",
                },
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def seed(service, employer="https://careers.example.test", *, run_id="a" * 32):
    mail = service.mail
    with service.db.exclusive() as s:
        box = Mailbox(
            email="alex@example.test",
            credentials=mail.vault.seal(
                {
                    "access_token": "fake-access-secret",
                    "refresh_token": "fake-refresh-secret",
                    "expires_at": time.time() + 3600,
                }
            ),
        )
        run = Run(
            id=run_id,
            job_id="b" * 32,
            state="running",
            started_at=now(),
            packet={"profile": {"email": "alex@example.test"}},
        )
        s.add_all([box, run])
        s.flush()
        return record(box), record(run)


async def configured(service, **kwargs):
    box, run = seed(service, **kwargs)
    rule = await service.mail.save_rule(
        RuleInput(
            mailbox_id=box["id"],
            employer_origin=kwargs.get("employer", "https://careers.example.test"),
            sender_domains=["notify.example.test"],
        )
    )
    return box, run, rule


def provider(messages):
    def route(request):
        assert request.headers["authorization"] == "Bearer fake-access-secret"
        if request.url.path.endswith("/messages"):
            assert "after:" in request.url.params["q"] and "to:alex@example.test" in request.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": m["id"]} for m in messages]})
        return httpx.Response(200, json=next(m for m in messages if request.url.path.endswith("/" + m["id"])))

    return httpx.MockTransport(route)


EMAIL = "fixture@example.test"


def receipt_seed(service, count, *, email_matched=False, start=None, state="confirmed"):
    start = start or datetime.now(UTC) - timedelta(hours=1)
    ids = []
    with service.db.session() as s:
        if not s.scalar(select(Mailbox)):
            s.add(Mailbox(email=EMAIL, credentials="owned-fixture", state="connected"))
        for i in range(count):
            job = Job(
                identity=f"owned-{state}-{email_matched}-{i}",
                url=f"https://owned.test/{i}",
                demo=False,
                company="Example",
                title="Engineer",
            )
            s.add(job)
            s.flush()
            receipt = {"email": {"message_id": str(i), "sender_trusted": True}} if email_matched else {}
            run = Run(
                job_id=job.id,
                state=state,
                receipt=receipt,
                packet={"profile": {"email": EMAIL}},
                created_at=(start + timedelta(seconds=i)).isoformat(),
            )
            s.add(run)
            s.flush()
            ids.append(run.id)
    return ids


class FolderClient:
    def __init__(self):
        self.folder = '"INBOX"'
        self.calls = []
        self.folders = {
            '"INBOX"': {"17": "900"},
            '"[Gmail]/All Mail"': {"17": "901", "18": "900"},
            '"[Gmail]/Spam"': {"17": "902"},
            '"[Gmail]/Trash"': {},
        }

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\All) "/" "[Gmail]/All Mail"',
            b'(\\Junk) "/" "[Gmail]/Spam"',
            b'(\\Trash) "/" "[Gmail]/Trash"',
        ]

    def select(self, folder, readonly):
        assert readonly
        assert folder in self.folders
        self.folder = folder
        self.calls.append(("select", folder))
        return "OK", [b"2"]

    def uid(self, operation, *args):
        self.calls.append((operation, self.folder, args))
        folder = self.folders[self.folder]
        if operation == "search":
            if args[1] == "X-GM-MSGID":
                return "OK", [" ".join(uid for uid, mid in folder.items() if mid == args[2]).encode()]
            assert args[1] == "X-GM-RAW"
            return "OK", [" ".join(folder).encode()]
        assert operation == "fetch"
        ids = args[0].split(",")
        if "BODY.PEEK" not in args[1]:
            return "OK", [f"1 (UID {uid} X-GM-MSGID {folder[uid]})".encode() for uid in ids]
        assert len(ids) == 1
        uid = ids[0]
        return "OK", [
            (
                f'1 (UID {uid} X-GM-MSGID {folder[uid]} INTERNALDATE "05-Oct-2026 14:00:00 +0000" RFC822.SIZE 90 BODY[]<0> {{90}}'.encode(),
                f"From: no-reply@example.test\r\n\r\nMessage {folder[uid]}".encode(),
            ),
            b")",
        ]
