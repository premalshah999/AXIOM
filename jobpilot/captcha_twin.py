"""hCaptcha tokens from the clean twin Chrome (owner decision 2026-10-19).

iCIMS answers its email step with the returning-candidate Central login (`loginOnly=1`) whenever its invisible hCaptcha
does not trust the browser, and Central cannot create accounts, so a new candidate never reaches the application (REA
2026-10-19, codex-handoff/rea/maps/icims.md). hCaptcha distrusts the worker Chrome because Playwright instruments every
frame of every tab in a Chrome it is connected to.

The twin is a second local Chrome that no Playwright session ever connects to (`python -m jobpilot.cli twin-browser`,
BROWSER_TWIN_CDP_URL). It opens the same page in a throwaway browser context, in a window that is minimized at once,
and executes its own invisible widget with the site's key. On this machine and IP that passes without a puzzle in about
a second. The worker tab then hands the token to the site's own callback.

Only plain CDP is used here: Page.navigate and Runtime.evaluate (never Runtime.enable). There is no pointer input: a
minimized window acknowledges each mouse event only after about 5 s, and the widget passes without any (2026-10-19).
"""

import asyncio
import itertools
import json
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets

LOCAL_HOSTS = {"127.0.0.1", "localhost", "host.docker.internal"}
# Two mints at a time keep the twin light; each one lasts about two seconds.
_slots = {}

MINT = r"""(key, ms) => new Promise(resolve => {
 let settled = false;
 const done = value => { if (!settled) { settled = true; resolve(value); } };
 setTimeout(() => done({error: 'timeout'}), ms);
 const go = () => {
  try {
   const holder = document.createElement('div');
   holder.style.cssText = 'position:fixed;left:0;bottom:0;';
   document.body.appendChild(holder);
   const id = hcaptcha.render(holder, {sitekey: key, size: 'invisible',
    callback: token => done({token}),
    // A puzzle means the twin is not trusted either; the worker's own solvers take over.
    'open-callback': () => done({error: 'puzzle'}),
    'error-callback': e => done({error: 'error: ' + e}),
    'expired-callback': () => done({error: 'expired'}),
    'chalexpired-callback': () => done({error: 'challenge expired'}),
    'close-callback': () => done({error: 'closed'})});
   hcaptcha.execute(id);
  } catch (e) { done({error: 'render: ' + e}); }
 };
 if (window.hcaptcha && hcaptcha.render) go();
 else {
  const script = document.createElement('script');
  script.src = 'https://js.hcaptcha.com/1/api.js?render=explicit&recaptchacompat=off';
  script.onload = go;
  script.onerror = () => done({error: 'script'});
  document.head.appendChild(script);
 }
})"""


class TwinError(Exception):
    pass


async def socket_url(endpoint):
    """The browser-level WebSocket of the twin, reached the way desktop_cdp reaches the worker Chrome."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise TwinError("the twin browser must use a local HTTP debugging address")
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(endpoint.rstrip("/") + "/json/version", headers={"Host": "localhost"})
        response.raise_for_status()
        path = urlsplit(response.json()["webSocketDebuggerUrl"]).path
    host = await asyncio.to_thread(socket.gethostbyname, parsed.hostname)
    return urlunsplit(("ws", f"{host}:{parsed.port or 80}", path, "", ""))


class CDP:
    def __init__(self, ws):
        self.ws, self.ids = ws, itertools.count(1)

    async def call(self, method, params=None, session=None):
        n = next(self.ids)
        message = {"id": n, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self.ws.send(json.dumps(message))
        while True:
            reply = json.loads(await self.ws.recv())
            if reply.get("id") == n:
                if "error" in reply:
                    raise TwinError(f"{method}: {str(reply['error'].get('message', ''))[:120]}")
                return reply.get("result", {})


async def _mint(endpoint, page_url, sitekey, timeout):
    expected = urlsplit(page_url).hostname
    address = await socket_url(endpoint)
    async with websockets.connect(address, max_size=None, open_timeout=5, ping_interval=None) as ws:
        cdp = CDP(ws)
        context = (await cdp.call("Target.createBrowserContext", {"disposeOnDetach": True}))["browserContextId"]
        target = None
        try:
            target = (
                await cdp.call(
                    "Target.createTarget", {"url": "about:blank", "browserContextId": context, "newWindow": True}
                )
            )["targetId"]
            try:
                window = (await cdp.call("Browser.getWindowForTarget", {"targetId": target}))["windowId"]
                await cdp.call("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "minimized"}})
            except TwinError:
                pass  # A visible window mints as well; minimizing only keeps it off the owner's screen.
            session = (await cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]

            async def evaluate(expression, wait=False):
                result = await cdp.call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True, "awaitPromise": wait},
                    session,
                )
                return result.get("result", {}).get("value")

            await cdp.call("Page.navigate", {"url": page_url}, session)
            for _ in range(48):
                await asyncio.sleep(0.25)
                if await evaluate("document.readyState") == "complete":
                    break
            # The site's own api.js loads asynchronously; reuse it rather than loading a second copy.
            for _ in range(12):
                if await evaluate("!!(window.hcaptcha && hcaptcha.render)"):
                    break
                await asyncio.sleep(0.25)
            if await evaluate("location.hostname") != expected:
                raise TwinError("the twin page left the employer host")
            value = await evaluate(f"({MINT})({json.dumps(sitekey)}, {int(timeout * 1000)})", wait=True) or {}
            if isinstance(value.get("token"), str) and value["token"]:
                return {"token": value["token"]}
            return {"error": str(value.get("error") or "no token")[:120]}
        finally:
            if target:
                try:
                    await cdp.call("Target.closeTarget", {"targetId": target})
                except TwinError:
                    pass
            try:
                await cdp.call("Target.disposeBrowserContext", {"browserContextId": context})
            except TwinError:
                pass


async def mint(endpoint, page_url, sitekey, *, timeout=25):
    """{'token': ...} minted by the twin, or {'error': reason}. Never raises: the caller keeps its own solvers."""
    slots = _slots.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(2))
    try:
        async with slots:
            async with asyncio.timeout(timeout + 20):
                return await _mint(endpoint, page_url, sitekey, timeout)
    except TwinError as exc:
        return {"error": str(exc)}
    except (OSError, httpx.HTTPError, websockets.WebSocketException, TimeoutError, KeyError, ValueError) as exc:
        return {"error": type(exc).__name__}
