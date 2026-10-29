import asyncio
import codecs
import functools
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

import certifi
import httpx


async def public_url(url: str, allow_private=False):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Only public http(s) URLs are supported")
    if allow_private:
        return
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, p.hostname, p.port or 443, 0, socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError("The URL hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private, loopback and metadata-service URLs are blocked")


@functools.cache
def _tls_context():
    # Building a verified TLS context loads the CA bundle (~tens of ms of CPU);
    # every source fetch used to build one on the shared application loop.
    return ssl.create_default_context(cafile=certifi.where())


async def fetch(url, *, method="GET", payload=None, allow_private=False, max_bytes=4_000_000):
    """Validate every redirect, bound response size, and do not forward credentials."""
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, trust_env=False, verify=_tls_context()
    ) as client:
        for _ in range(5):
            await public_url(url, allow_private)
            async with client.stream(
                method, url, json=payload, headers={"User-Agent": "ApplyPilot/1.0"}
            ) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin

                    url = urljoin(url, r.headers["location"])
                    if r.status_code == 303:
                        method, payload = "GET", None
                    continue
                r.raise_for_status()
                # Decoded as the chunks arrive: one decode of a 16 MB feed is a single call that holds the
                # interpreter (8-30 ms) and no thread offload shortens it, but small ones let the loop interleave.
                decoder, parts, size = codecs.getincrementaldecoder("utf-8")("replace"), [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Source response exceeds its size limit")
                    parts.append(decoder.decode(chunk))
                parts.append(decoder.decode(b"", final=True))
                return "".join(parts), str(r.url)
    raise ValueError("Too many redirects")
