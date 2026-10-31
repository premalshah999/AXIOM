"""A scratch PostgreSQL for the database-choice tests: initdb in a temp folder, loopback only, a free port, stopped afterwards.

Skips when no PostgreSQL 17 is installed (CI images without one). Never the installation's own cluster: its own folder and port.
"""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import psycopg
import pytest

from jobpilot.native import pg as native_pg

PASSWORD = "scratchpw"


def find_bin():
    for folder in (*native_pg.SEARCH, os.path.dirname(shutil.which("postgres") or "")):
        if folder and os.access(Path(folder) / "postgres", os.X_OK) and os.access(Path(folder) / "initdb", os.X_OK):
            return Path(folder)
    return None


class Scratch:
    def __init__(self, folder, port, bin_dir):
        self.folder, self.port, self.bin = folder, port, bin_dir
        self.count = 0

    def url(self, database, *, scheme="postgresql", user="applypilot", password=PASSWORD, query=""):
        return f"{scheme}://{user}:{password}@127.0.0.1:{self.port}/{database}{query}"

    def create(self, prefix="t"):
        self.count += 1
        name = f"{prefix}{self.count}_{int(time.time()) % 100000}"
        self.sql("postgres", f"create database {name}")
        return name

    def sql(self, database, text, params=None, user="applypilot", password=PASSWORD):
        with psycopg.connect(host="127.0.0.1", port=self.port, user=user, password=password, dbname=database, autocommit=True) as conn:
            cur = conn.execute(text, params)
            return cur.fetchall() if cur.description else []

    def connect(self, database, **kw):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="applypilot", password=PASSWORD, dbname=database, autocommit=True, **kw)


@pytest.fixture(scope="session")
def scratch_pg(tmp_path_factory):
    bin_dir = find_bin()
    if not bin_dir:
        pytest.skip("PostgreSQL 17 is not installed here")
    folder = tmp_path_factory.mktemp("scratch-pg")
    data = folder / "data"
    (folder / "pw").write_text(PASSWORD)
    env = {**os.environ, "LC_ALL": "en_US.UTF-8", "TZ": "UTC"}
    done = subprocess.run([str(bin_dir / "initdb"), "-D", str(data), "-U", "applypilot", f"--pwfile={folder / 'pw'}", "--auth=scram-sha-256",
                           "--encoding=UTF8"], capture_output=True, text=True, env=env)
    if done.returncode:
        pytest.skip("initdb failed here: " + done.stderr[-200:])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    options = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories= -c max_connections=100 -c synchronous_commit=off -c shared_buffers=32MB"
    started = subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(data), "-o", options, "-l", str(folder / "log"), "-w", "start"],
                             capture_output=True, text=True, env=env)
    if started.returncode:
        pytest.skip("PostgreSQL did not start here: " + started.stderr[-200:])
    scratch = Scratch(folder, port, bin_dir)
    try:
        for role in ("anon", "authenticated"):
            scratch.sql("postgres", f"create role {role} nologin")
        scratch.sql("postgres", "create role limited login password 'limitedpw'")
        scratch.sql("postgres", "create role tight login password 'tightpw' connection limit 4")
        yield scratch
    finally:
        subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"], capture_output=True, env=env)
