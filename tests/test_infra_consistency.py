"""The container and tooling facts that must not drift apart again: one image, one compose file, one frontend,
pinned versions that agree with each other."""

import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def files(pattern):
    """Tracked or new-but-not-ignored files, so a stray second Dockerfile or frontend is caught before it is committed."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return sorted(path for path in listing if path and fnmatch(Path(path).name, pattern))


def dockerfile():
    return (ROOT / "Dockerfile").read_text()


def build_args():
    return dict(re.findall(r"^ARG\s+([A-Z_]+)=(\S+)$", dockerfile(), re.M))


def test_one_dockerfile_one_compose_file_one_frontend():
    assert files("Dockerfile*") == ["Dockerfile"]
    assert files("compose*.y*ml") == ["compose.yaml"]
    assert files("docker-compose*") == []
    assert files("package.json") == ["frontend/package.json"]
    # One dashboard build, in one Node stage.
    assert len(re.findall(r"^FROM\s+node:", dockerfile(), re.M)) == 1
    assert len(re.findall(r"^FROM\s+python:", dockerfile(), re.M)) == 1


def test_runtime_versions_agree_with_the_version_files():
    args = build_args()
    assert (ROOT / ".nvmrc").read_text().strip() == args["NODE_VERSION"]
    minor = (ROOT / ".python-version").read_text().strip()
    assert args["PYTHON_VERSION"].startswith(minor + ".")
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'requires-python = ">={minor},' in pyproject


def test_every_base_image_is_pinned_to_a_patch_release():
    for ref in re.findall(r"^FROM\s+(\S+)", dockerfile(), re.M):
        assert ":" in ref and "latest" not in ref, f"unpinned base image: {ref}"
    args = build_args()
    assert re.fullmatch(r"\d+\.\d+\.\d+", args["NODE_VERSION"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", args["PYTHON_VERSION"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", args["UV_VERSION"])


def test_compose_has_exactly_the_app_and_the_database():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert set(compose["services"]) == {"app", "db"}, "no sidecar containers: they drift from the app image"
    db, app = compose["services"]["db"], compose["services"]["app"]
    assert re.fullmatch(r"postgres:\d+\.\d+-bookworm", db["image"]), "pin PostgreSQL to major.minor"
    assert "ports" not in db, "the database is reachable only from the app container"
    assert db["healthcheck"] and app["depends_on"]["db"]["condition"] == "service_healthy"
    assert "build" in app and "image" not in app, "the app image is built here, under the project's own name"
    assert "POSTGRES_PASSWORD:?" in db["environment"]["POSTGRES_PASSWORD"], "no built-in database password"
    # Published on loopback unless the owner asks otherwise.
    assert app["ports"] == ["${APP_BIND:-127.0.0.1}:${APP_PORT:-8080}:8080"]


def test_the_image_never_receives_secrets_or_local_state():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for entry in (".env", ".git", ".data", "tests", "jobpilot/static"):
        assert entry in ignored, f".dockerignore must exclude {entry}"
    gitignore = (ROOT / ".gitignore").read_text().split()
    assert ".env" in gitignore


def test_the_dashboard_is_built_by_the_image_not_copied_from_a_laptop():
    text = dockerfile()
    assert "COPY --from=dashboard /build/jobpilot/static ./jobpilot/static" in text
    assert "outDir: \"../jobpilot/static\"" in (ROOT / "frontend" / "vite.config.ts").read_text()
