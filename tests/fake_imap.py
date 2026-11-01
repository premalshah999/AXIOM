"""A small local IMAP server that speaks the part of Gmail's dialect jobpilot/gmail_imap.py uses.

LOGIN, LIST (with Gmail's special-use flags), SELECT, NOOP, UID SEARCH X-GM-RAW / X-GM-MSGID, UID FETCH of
(UID X-GM-MSGID) and of the bounded body, LOGOUT. X-GM-RAW understands after:, to:, a {…} group of from:/subject:/"phrase"
terms ORed together, and ignores the rest, which is what the verification poll sends. Messages are added while a test runs
(`deliver`), so a code can arrive late. Never a real network: 127.0.0.1 and a port the OS picks.

Terms match whole words, as Gmail's do: from:northwind finds a@northwind.example and never a@hrnorthwind.example, and
from:icims.com finds a+autoreply@talent.icims.com. A loose substring match here would pass a search Gmail would not."""

import email
import re
import socketserver
import threading
import time
from email import policy
from email.message import EmailMessage
from email.utils import formatdate


def words(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


def has_words(haystack, needle):
    """The needle's words, consecutive, in the haystack's words."""
    tokens, wanted = words(haystack), words(needle)
    return bool(wanted) and any(tokens[i : i + len(wanted)] == wanted for i in range(len(tokens) - len(wanted) + 1))


class FakeMailbox:
    def __init__(self):
        self.messages = []  # {"uid", "msgid", "when", "raw", "folder"}
        self.lock = threading.Lock()
        self.logins = 0
        self.searches = []

    def deliver(self, *, sender, to, subject, body, html=None, when=None, folder="[Gmail]/All Mail", auth=True, delivered_to=None):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, to, subject, formatdate()
        if delivered_to:
            msg["Delivered-To"] = delivered_to
        if auth:
            domain = re.search(r"@([^>\s]+)", sender)[1]
            msg["Authentication-Results"] = (
                auth if isinstance(auth, str) else f"mx.google.com; dkim=pass header.i=@{domain}; spf=pass; dmarc=pass (p=REJECT) header.from={domain}"
            )
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        with self.lock:
            n = len(self.messages) + 1
            self.messages.append(
                {"uid": str(n), "msgid": str(1000 + n), "when": when or time.time(), "raw": msg.as_bytes(), "folder": folder}
            )

    def matches(self, query, item):
        parsed = email.message_from_bytes(item["raw"], policy=policy.default)
        texts = [str(parsed.get("subject", ""))]
        for part in parsed.walk():
            if part.get_content_type() in {"text/plain", "text/html"}:
                texts.append(part.get_content())
        everything = " ".join(texts + [str(parsed.get("from", "")), str(parsed.get("to", ""))])
        group = re.search(r"\{([^}]*)\}", query)
        rest = query.replace(group[0], "") if group else query
        after = re.search(r"after:(\d+)", rest)
        if after and not item["when"] > int(after[1]):
            return False
        to = re.search(r"to:(\S+)", rest)
        if to and not has_words(str(parsed.get("to", "")), to[1]):
            return False
        if not group:
            # A plain word list is an AND of terms and groups: the receipt search.
            return True
        for term in re.findall(r'(?:from:|subject:)?"[^"]*"|\S+', group[1]):
            term = term.lower()
            if term.startswith("from:") and has_words(str(parsed.get("from", "")), term[5:]):
                return True
            if term.startswith("subject:") and has_words(str(parsed.get("subject", "")), term[8:].strip('"')):
                return True
            if not term.startswith(("from:", "subject:")) and has_words(everything, term.strip('"')):
                return True
        return False


class Handler(socketserver.StreamRequestHandler):
    def send(self, line):
        self.wfile.write(line if isinstance(line, bytes) else line.encode())
        self.wfile.flush()

    def handle(self):
        box = self.server.mailbox
        selected = None
        self.send("* OK fake gmail ready\r\n")
        while True:
            line = self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                return
            tag, _, rest = line.partition(" ")
            command = rest.split(" ", 1)[0].upper()
            if command == "CAPABILITY":
                self.send(f"* CAPABILITY IMAP4rev1 X-GM-EXT-1 AUTH=PLAIN\r\n{tag} OK done\r\n")
            elif command == "LOGIN":
                box.logins += 1
                self.send(f"{tag} OK logged in\r\n")
            elif command == "LIST":
                self.send(
                    '* LIST (\\HasNoChildren) "/" "INBOX"\r\n'
                    '* LIST (\\All \\HasNoChildren) "/" "[Gmail]/All Mail"\r\n'
                    '* LIST (\\Junk \\HasNoChildren) "/" "[Gmail]/Spam"\r\n'
                    '* LIST (\\Trash \\HasNoChildren) "/" "[Gmail]/Trash"\r\n'
                    f"{tag} OK done\r\n"
                )
            elif command == "SELECT" or command == "EXAMINE":
                selected = re.search(r'"([^"]*)"', rest)[1]
                self.send(f"* {len(box.messages)} EXISTS\r\n{tag} OK [READ-ONLY] selected\r\n")
            elif command == "NOOP":
                self.send(f"{tag} OK noop\r\n")
            elif command == "LOGOUT":
                self.send(f"* BYE\r\n{tag} OK bye\r\n")
                return
            elif command == "UID":
                self.uid(tag, rest, selected)
            else:
                self.send(f"{tag} BAD unsupported\r\n")

    def uid(self, tag, rest, selected):
        box = self.server.mailbox
        with box.lock:
            # Inbox mail is in All Mail as well, as in Gmail; Spam is only in Spam.
            here = [m for m in box.messages if m["folder"] == selected or (selected == "[Gmail]/All Mail" and m["folder"] == "INBOX")]
        if re.match(r"UID SEARCH", rest, re.I):
            raw = re.search(r"X-GM-RAW\s+\"((?:[^\"\\]|\\.)*)\"", rest)
            ident = re.search(r"X-GM-MSGID\s+(\d+)", rest)
            if raw:
                query = raw[1].replace('\\"', '"').replace("\\\\", "\\")
                box.searches.append(query)
                hits = [m for m in here if box.matches(query, m)]
            else:
                hits = [m for m in here if ident and m["msgid"] == ident[1]]
            self.send("* SEARCH " + " ".join(m["uid"] for m in hits) + f"\r\n{tag} OK search done\r\n")
            return
        fetch = re.match(r"UID FETCH ([\d,]+) \((.*)\)$", rest, re.I)
        if not fetch:
            self.send(f"{tag} BAD unsupported\r\n")
            return
        for number, item in enumerate(here, 1):
            if item["uid"] not in fetch[1].split(","):
                continue
            if "BODY.PEEK" in fetch[2]:
                when = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(item["when"]))
                head = (
                    f'* {number} FETCH (UID {item["uid"]} X-GM-MSGID {item["msgid"]} INTERNALDATE "{when}" '
                    f'RFC822.SIZE {len(item["raw"])} BODY[]<0> {{{len(item["raw"])}}}\r\n'
                )
                self.send(head.encode() + item["raw"] + b")\r\n")
            else:
                self.send(f'* {number} FETCH (UID {item["uid"]} X-GM-MSGID {item["msgid"]})\r\n')
        self.send(f"{tag} OK fetch done\r\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start():
    """(server, mailbox): the server listens on 127.0.0.1; stop it with server.shutdown()."""
    server = Server(("127.0.0.1", 0), Handler)
    server.mailbox = FakeMailbox()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.mailbox
