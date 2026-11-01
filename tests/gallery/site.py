"""A fake employer on 127.0.0.1: pages that behave like the real traps, and a server that remembers exactly what it was sent.

Everything is synthetic. A scenario is a dict of path -> page, where a page is HTML, or a function of the request that returns HTML
(for postbacks and refusals). `Site.posts` is the employer's own record, the one thing a run cannot talk its way past: an application
either arrived once with the right values or it did not.

To look at a page yourself: python -m tests.gallery.site [scenario], then open the address it prints.
"""

import http.server
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit


@dataclass
class Request:
    method: str
    path: str
    query: dict
    form: dict
    site: "Site"
    wait: float = 0.0  # seconds this answer takes, for a slow postback

    def slowly(self, seconds):
        time.sleep(seconds)


def parse_form(content_type, body):
    """The named values of a urlencoded or multipart body; a file reads as its file name, and `<name>__bytes` is its size."""
    if "multipart/form-data" in (content_type or ""):
        boundary = re.search(r"boundary=([^;]+)", content_type).group(1).strip('"').encode()
        values = {}
        for part in body.split(b"--" + boundary):
            head, _, data = part.partition(b"\r\n\r\n")
            name = re.search(rb'name="([^"]*)"', head)
            if not name:
                continue
            key, data = name.group(1).decode(), data.rstrip(b"\r\n-")
            filename = re.search(rb'filename="([^"]*)"', head)
            if filename:
                values[key], values[key + "__bytes"] = filename.group(1).decode(), len(data)
            else:
                values[key] = data.decode("utf-8", "replace")
        return values
    return {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}


class Site:
    def __init__(self, pages):
        self.pages = pages
        self.posts = []  # (path, form values), in order: what the employer received.
        self.hits = []  # Every path asked for.
        self.state = {}  # Whatever a page function wants to remember between requests.
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def reply(self, body, status=200, kind="text/html; charset=utf-8", location=None):
                payload = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            def answer(self, form):
                parts = urlsplit(self.path)
                site.hits.append(parts.path)
                if self.command == "POST":
                    site.posts.append((parts.path, form))
                page = site.pages.get(parts.path)
                if page is None:
                    return self.reply("<!doctype html><title>Not found</title><h1>Page not found</h1>", 404)
                if callable(page):
                    page = page(Request(self.command, parts.path, parse_qs(parts.query), form, site))
                if isinstance(page, tuple):  # (status, location): a redirect after a POST, as most sites do it.
                    return self.reply("", page[0], location=page[1])
                return self.reply(page)

            def do_GET(self):
                self.answer({})

            def do_HEAD(self):
                self.answer({})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.answer(parse_form(self.headers.get("Content-Type"), body))

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def applications(self, path="/submit"):
        """The form posts that reached the employer's application endpoint."""
        return [form for where, form in self.posts if where.startswith(path)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@dataclass
class Scenario:
    """One trap: the pages, what the employer must have received, and what must never have been touched."""

    name: str
    trap: str  # The real incident it reproduces, in a sentence.
    pages: dict
    start: str = "/jobs/1"
    state: str = "confirmed"  # How the run must end.
    applications: int = 1  # Posts the employer's /submit must have received.
    expect: dict = field(default_factory=lambda: {"first_name": "Alex", "last_name": "Example", "email": "alex@example.test"})
    never: tuple = ()  # Paths that must not have been asked for: the chat widget, the search, the decoy.
    facts: dict = field(default_factory=dict)  # Profile facts beyond the name and email.
    terms: bool = False  # The profile accepts cookie and application terms, which dismissing a cookie dialog waits for.
    title: str = "Software Engineer"
    company: str = "ExampleSoft"


if __name__ == "__main__":
    import sys

    from .scenarios import SCENARIOS

    chosen = {s.name: s for s in SCENARIOS}.get(sys.argv[1] if len(sys.argv) > 1 else "") or SCENARIOS[0]
    served = Site(chosen.pages)
    print(f"{chosen.name}: {served.base}{chosen.start}\n{chosen.trap}\nPress Ctrl-C to stop. Others: {', '.join(s.name for s in SCENARIOS)}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        served.close()
