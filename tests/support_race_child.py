"""A separate worker process for tests/test_reservation_race.py: it asks for the one reservation of a run and may die at a
chosen moment, with no cleanup and no exception handlers, as a killed process does.

usage: python -I support_race_child.py MODE REPO DATABASE_URL DATA_DIR RUN_ID START_AT
  reserve                   ask once, print RESERVED or REFUSED <why>
  die-after-reserve         ask, and once it is granted exit hard (the crash between the reservation and the click)
  die-before-commit         ask, and exit hard with the reservation written but not committed (the crash inside it)
START_AT is a wall-clock time (seconds): every process waits for it after importing, so they ask together.
"""

import os
import sys
import time

mode, repo, url, data_dir, run_id, start_at = sys.argv[1:7]
sys.path.insert(0, repo)

from jobpilot.config import Settings  # noqa: E402
from jobpilot.db import Database  # noqa: E402
from jobpilot.service import Service  # noqa: E402

config = Settings(
    _env_file=None, database_url=url, dbos_database_url="", data_dir=data_dir, app_token="test-access-token",
    enable_workers=False, daily_application_limit=int(os.environ.get("RACE_DAILY_LIMIT", "100")),
).prepare()
service = Service(Database(url), config)

if mode == "die-before-commit":
    from sqlalchemy.orm import Session

    def written_not_committed(self, *args, **kwargs):
        self.flush()  # The UPDATE reaches PostgreSQL inside the open transaction, holding the app lock...
        os._exit(9)  # ...and the process ends before COMMIT.

    Session.commit = written_not_committed

while time.time() < float(start_at):
    time.sleep(0.001)
try:
    service.reserve_submission(run_id)
except Exception as exc:  # Whatever refused it is printed: the test tells "not authorized" from an error of ours.
    print("REFUSED", type(exc).__name__, exc, flush=True)
    sys.exit(0)
print("RESERVED", flush=True)
if mode == "die-after-reserve":
    os._exit(9)
