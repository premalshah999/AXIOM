"""Isolated pdflatex compilation.

- A fresh temporary directory per job; only the generated .tex is written there.
- Shell escape disabled, kpathsea paranoid read/write (no reading outside the
  TeX tree and the job directory), halt on first error.
- Environment scrubbed: no credentials, no proxies; HOME/TEXMF* point into the
  temp dir. pdflatex performs no network access itself.
- CPU and output-file limits per process, a whole-run timeout that kills the
  process group, a maximum PDF size, and bounded concurrency so one slow
  compile never serializes every application.

Stronger isolation (separate container/user without network) is a deployment
option documented in DEVELOPER.md.
"""

import asyncio
import os
import re
import shutil
import signal
import tempfile
from dataclasses import dataclass

MAX_PDF_BYTES = 2 * 1024 * 1024
_SLOTS: dict[int, asyncio.Semaphore] = {}


@dataclass
class Compiled:
    ok: bool
    pdf: bytes | None
    code: str = ""  # compile_error | compile_timeout | compiler_missing | output_too_large
    retryable: bool = False
    detail: str = ""
    errors: list = None  # [{"line": int | None, "message": str}] for the editor


def parse_errors(log: str) -> list[dict]:
    errors = []
    for line in log.splitlines():
        m = re.match(r"^\./resume\.tex:(\d+): (.+)$", line)
        if m:
            errors.append({"line": int(m.group(1)), "message": m.group(2).strip()})
        elif line.startswith("! "):
            errors.append({"line": None, "message": line[2:].strip()})
    if not errors and "Emergency stop" in log:
        errors.append({"line": None, "message": "LaTeX stopped early. Check the last edited lines."})
    seen, out = set(), []
    for e in errors:
        key = (e["line"], e["message"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out[:20]


def slots(limit: int) -> asyncio.Semaphore:
    loop = id(asyncio.get_running_loop())
    if loop not in _SLOTS:
        _SLOTS[loop] = asyncio.Semaphore(limit)
    return _SLOTS[loop]


def _limits(cpu_seconds: int):
    def apply():
        import resource

        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))

    return apply


def _env(work: str, binary: str) -> dict:
    return {
        "PATH": os.pathsep.join(dict.fromkeys([os.path.dirname(binary), "/usr/bin", "/bin"])),
        "HOME": work,
        "TMPDIR": work,
        "TEXMFOUTPUT": work,
        "TEXMFVAR": os.path.join(work, ".texmf-var"),
        "TEXMFCONFIG": os.path.join(work, ".texmf-config"),
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        "LANG": "C",
        "LC_ALL": "C",
    }


async def compile_pdf(tex: str, *, timeout: float = 40, runs: int = 2, concurrency: int = 2) -> Compiled:
    binary = shutil.which("pdflatex")
    if not binary:
        return Compiled(False, None, "compiler_missing", False, "pdflatex is not installed on this host")
    async with slots(concurrency):
        work = tempfile.mkdtemp(prefix="ap-tex-")
        try:
            path = os.path.join(work, "resume.tex")
            with open(path, "w", encoding="utf-8") as f:
                f.write(tex)
            cmd = [
                binary,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "resume.tex",
            ]
            deadline = asyncio.get_running_loop().time() + timeout
            for _ in range(runs):  # Two passes settle references and page geometry.
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return Compiled(False, None, "compile_timeout", True, "Compile exceeded its time limit")
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=work,
                    env=_env(work, binary),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    preexec_fn=_limits(int(timeout) + 1),
                )
                try:
                    out, _ = await asyncio.wait_for(proc.communicate(), remaining)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await proc.wait()
                    if asyncio.current_task().cancelling():
                        raise
                    return Compiled(False, None, "compile_timeout", True, "Compile exceeded its time limit")
                if proc.returncode != 0:
                    log = out.decode("utf-8", "replace")
                    errors = parse_errors(log)
                    detail = "; ".join(
                        e["message"] if e["line"] is None else f"line {e['line']}: {e['message']}"
                        for e in errors[:3]
                    )
                    return Compiled(False, None, "compile_error", False, detail[:500], errors)
            pdf_path = os.path.join(work, "resume.pdf")
            if not os.path.exists(pdf_path):
                return Compiled(False, None, "compile_error", False, "No PDF was produced")
            if os.path.getsize(pdf_path) > MAX_PDF_BYTES:
                return Compiled(False, None, "output_too_large", False, "PDF exceeds the size limit")
            with open(pdf_path, "rb") as f:
                return Compiled(True, f.read())
        finally:
            shutil.rmtree(work, ignore_errors=True)
