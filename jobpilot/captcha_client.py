"""Official 2Captcha SDK, isolated so cancellation stops its blocking poller.

Secrets enter on stdin and leave on stdout only to the owning Python process.
No command-line credentials, logs, retries, provider descriptions or artifacts.
"""

import asyncio
import json
import re
import sys

from twocaptcha import TwoCaptcha
from twocaptcha.exceptions.solver import ApiException, NetworkException, TimeoutException, ValidationException
from twocaptcha.exceptions.api import (
    ApiException as TransportApiException,
    NetworkException as TransportNetworkException,
)


def detail(exc, key=""):
    """Exception class plus provider error codes/HTTP status only; never free text."""
    text = str(exc).replace(key, "") if key else str(exc)
    codes = re.findall(r"\bERROR_[A-Z_]+\b|\bCAPC?HA_NOT_READY\b", text)
    status = re.findall(r"\b(?:status(?: code)?|response)[:= ]+([1-5]\d\d)\b", text, re.I)
    return " ".join([type(exc).__name__, *dict.fromkeys(codes), *("HTTP " + s for s in status[:1])])


def sdk_result(request):
    info, timeout = request["info"], request["timeout"]
    solver = TwoCaptcha(request["key"], defaultTimeout=timeout, recaptchaTimeout=timeout, pollingInterval=5)
    kind = info["kind"]
    if kind not in {"recaptcha", "hcaptcha", "turnstile"}:
        return {"error": "UNSUPPORTED_CAPTCHA"}
    args = {"sitekey": info["key"], "url": request["url"]}
    if isinstance(request.get("user_agent"), str) and request["user_agent"]:
        # Workers solve with the same browser identity the site sees (documented for reCAPTCHA).
        args["userAgent"] = request["user_agent"][:400]
    if kind == "recaptcha":
        args.update(version="v3" if info.get("v3") else "v2", enterprise=int(bool(info.get("enterprise"))))
        if info.get("action"):
            args["action"] = info["action"]
        if info.get("s"):
            args["data-s"] = info["s"]
        if info.get("invisible"):
            args["invisible"] = 1
    elif kind == "hcaptcha":
        if info.get("invisible"):
            args["invisible"] = 1
        if isinstance(info.get("rqdata"), str) and info["rqdata"]:
            args["data"] = info["rqdata"]
    elif kind == "turnstile":
        if info.get("action"):
            args["action"] = info["action"]
        if info.get("cdata"):
            args["data"] = info["cdata"]
    try:
        result = getattr(solver, kind)(**args)
    except TimeoutException as exc:
        return {"error": "SOLVER_TIMEOUT", "detail": detail(exc, request["key"])}
    except (NetworkException, TransportNetworkException) as exc:
        return {"error": "SOLVER_CONNECTION_ERROR", "detail": detail(exc, request["key"])}
    except (ApiException, TransportApiException, ValidationException) as exc:
        return {"error": "SOLVER_REJECTED", "detail": detail(exc, request["key"])}
    code = result.get("code") if isinstance(result, dict) else None
    return {"token": code} if isinstance(code, str) and code else {"error": "INVALID_SOLVER_RESULT"}


async def solve_token(key, info, url, timeout, user_agent=""):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "jobpilot.captcha_client",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout + 20):
            output, _ = await process.communicate(
                json.dumps(
                    {"key": key, "info": info, "url": url, "timeout": timeout, "user_agent": user_agent}
                ).encode()
            )
        if process.returncode or len(output) > 64_000:
            return {"error": "SOLVER_CONNECTION_ERROR"}
        result = json.loads(output)
        return result if isinstance(result, dict) else {"error": "INVALID_SOLVER_RESULT"}
    except TimeoutError:
        return {"error": "SOLVER_TIMEOUT"}
    except (ValueError, OSError):
        return {"error": "SOLVER_CONNECTION_ERROR"}
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


if __name__ == "__main__":
    request = {}
    try:
        request = json.loads(sys.stdin.buffer.read(64_000))
        result = sdk_result(request)
    except Exception as exc:
        # Not a network failure: record what actually broke (redacted).
        result = {"error": "SOLVER_INTERNAL_ERROR", "detail": detail(exc, request.get("key", ""))}
    sys.stdout.write(json.dumps(result))
