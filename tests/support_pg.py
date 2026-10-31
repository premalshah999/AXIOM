"""A scratch PostgreSQL server for tests that need the real locking, not SQLite's: the reservation that prevents a duplicate
application is `Database.exclusive()` (one advisory lock per app) around a compare-and-set, and SQLite's BEGIN IMMEDIATE
cannot show whether that holds under concurrent connections.

One throwaway cluster per test session: its own data directory, 127.0.0.1 only, a free port, no password (nothing else can
reach it), removed with the session. It never touches the installation's own cluster (jobpilot/native/pg.py), whose
port and data directory it does not use. A computer without PostgreSQL 17 skips the tests that ask for it.
"""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

USER = "scratch"


def find_bin():
    """The directory of a PostgreSQL 17 installation (the major version the installation runs), or None."""
    from jobpilot.native.pg import SEARCH

    candidates = [os.environ.get("AXIOM_PG_BIN"), *SEARCH]
    on_path = shutil.which("postgres")
    if on_path:
        candidates.append(str(Path(on_path).parent))
    for folder in filter(None, candidates):
        binary = Path(folder) / "postgres"
        if os.access(binary, os.X_OK):
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10).stdout
            if " 17." in out:
                return Path(folder)
    return None


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ScratchServer:
    def __init__(self, bin_dir, directory):
        self.bin, self.directory, self.port = Path(bin_dir), Path(directory), free_port()
        self.process = None
        self.count = 0

    def start(self):
        env = {**os.environ, "TZ": "UTC", "LC_ALL": "C"}
        subprocess.run(
            [str(self.bin / "initdb"), "-D", str(self.directory), "-U", USER, "--auth=trust", "-E", "UTF8",
             "--locale=C", "--no-sync"],
            check=True, capture_output=True, env=env,
        )
        # fsync off: nothing here must survive the computer, only the server process (a killed client, not a power cut).
        settings = {"listen_addresses": "127.0.0.1", "unix_socket_directories": "", "fsync": "off",
                    "shared_buffers": "16MB", "max_connections": 100, "jit": "off", "timezone": "UTC",
                    "synchronous_commit": "off", "log_min_messages": "warning"}
        argv = [str(self.bin / "postgres"), "-D", str(self.directory), "-p", str(self.port)]
        for key, value in settings.items():
            argv += ["-c", f"{key}={value}"]
        self.process = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.process.poll() is not None:
                raise RuntimeError("the scratch PostgreSQL server exited while starting")
            ready = subprocess.run([str(self.bin / "pg_isready"), "-q", "-h", "127.0.0.1", "-p", str(self.port)])
            if ready.returncode == 0:
                return self
            time.sleep(0.1)
        raise RuntimeError("the scratch PostgreSQL server did not become ready")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(2)  # SIGINT: fast shutdown, connections cut.
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def url(self, database):
        return f"postgresql+psycopg://{USER}@127.0.0.1:{self.port}/{database}"

    def create_database(self):
        """A new empty database: every test starts from nothing, as the SQLite ones do."""
        import psycopg

        self.count += 1
        name = f"race{self.count}"
        with psycopg.connect(host="127.0.0.1", port=self.port, user=USER, dbname="postgres", autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        return name

    def connect(self, database):
        import psycopg

        return psycopg.connect(host="127.0.0.1", port=self.port, user=USER, dbname=database, autocommit=True)
