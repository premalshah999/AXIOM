# Third-party notices

The repository keeps AGPL-3.0 licensing and attribution to its predecessor, [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot). The Studio application is a replacement runtime, not a continuation of the old Claude Code pipeline.

Runtime and build dependencies retain their respective licenses. Exact dependency versions and hashes are recorded in `uv.lock` and `frontend/package-lock.json`.

- FastAPI, PydanticAI, SQLAlchemy, React, Vite, and many utility libraries are independently maintained open-source projects.
- The cryptography package provides authenticated encryption; its installed distribution retains its license notice.
- DBOS, Playwright, and their dependencies include their own license notices in installed distributions.
- Archivo, Doto and Fragment Mono are self-hosted via Fontsource under SIL Open Font License 1.1. Their package distributions include the full license text.
- Lucide icons retain the Lucide/Feather license notices included in the package.
- MiMo inference, Telegram, and CapSolver are external services with separate terms and billing. A source-available application does not make those services free.

The dashboard build bundles only the selected fonts/icons and frontend code. Private profile data, uploaded resumes, employer sessions, model keys, and bot keys are not source-code assets.
