# AXIOM (ApplyPilot)

A self-hosted workspace for discovering suitable jobs and managing evidence-backed applications. Eight browser workers run
it: six ATS workers that each follow the flow of the hiring platform they are given, and two specialists that take
difficult or already-failed applications with a somewhat larger budget.

## Quick start

You need Docker Desktop and Google Chrome on a Mac.

```sh
git clone https://github.com/premalshah999/AXIOM.git
cd AXIOM
make up
```

That creates a private `.env`, builds the one image, starts the app with PostgreSQL and prints the address to open. The
full walk-through, with API keys, the Gmail app password, the worker Chrome and troubleshooting, is in
[docs/SETUP.md](docs/SETUP.md).

## Documentation

- [docs/SETUP.md](docs/SETUP.md): install and everyday use, for anyone.
- [docs/INFRA.md](docs/INFRA.md): image, compose, settings audit, worker topology and cut-over notes.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the system is built, where its state lives, and the target design. [docs/API.md](docs/API.md): every HTTP route and its review. [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md): how a question is answered.
- [.env.example](.env.example): every setting, its default and whether it is required.
- [DEVELOPER.md](DEVELOPER.md): developer guide. [docs/OVERHAUL.md](docs/OVERHAUL.md): the October 2026 overhaul plan and status.

Current worker, pause and submission settings are shown in the dashboard. Uncertain submissions and existing confirmations remain protected. Offline regressions do not establish live employer success.

AGPL-3.0-only. Attribution to [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) is preserved. See [third-party notices](docs/third-party.md).
