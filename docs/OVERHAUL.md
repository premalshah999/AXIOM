# AXIOM / ApplyPilot overhaul: plan, status and handoff

This is the living record of the October 2026 overhaul. Use it to continue the work from any account: everything needed
to resume is either here or linked from here. Update the **Status board** whenever a workstream moves.

## Owner's goals (2026-10-20)

1. **Production-proper and reproducible.**
   - Every pipeline works.
   - One container image, pinned versions, one frontend.
   - No broken, slow or conflicting services.
   - A sound API and system design.
2. **No personal logic in the codebase.** The applicant's facts live in the profile and knowledge base (the database),
   never in code or tests. Friends and family must be able to run their own copy.
3. **Workers.**
   - 6 ATS workers, plus 2 specialist workers for difficult or failed applications.
   - The specialists have better logic and a slightly larger budget.
   - Every application is classified by ATS platform, and each platform has its own flow.
4. **Speed.** Repeat the Workday result (6–7 min down to 2–3 min) on other ATS platforms.
5. **Failures.** Diagnose every failed and unsuccessful application to date. Find and remove obviously wrong pipeline flows.
6. **Knowledge base.** Keep the owner's current information as it is. Design the knowledge base well and improve
   retrieval where it helps.
7. **Quality.** A well-structured directory, readable and maintainable code, unit, integration and regression tests,
   and documentation. Keep the REA documentation and flows (`docs/rea/`) for future debugging.
8. **Handoff.** At the end the owner uses the web app alone to watch new applications before the demo.

## Ground rules (every agent, every session)

- **Never print, log or commit secrets.** That covers `.env` values, tokens, passwords, cookies, saved account
  credentials and mail app passwords. Report only booleans or lengths.
- **No personal data in code, tests or docs.** Use synthetic applicants such as `Alex Doe` and `applicant@example.com`.
  **AXIOM on GitHub is public.**
- **The worker Chrome (CDP 9224) and the twin Chrome (CDP 9225) are production.**
  - Never attach to them while a run is active.
  - Never close them.
  - Never launch another Chrome on those ports.
- **Tests run one at a time on this machine.** Use `scripts/dev/pytest-locked.sh`, which holds a machine-wide lock at
  background priority. Prefer targeted test files. Never run browser suites in parallel.
- **Production is touched only by the orchestrator.** That means deploying, restarting containers, changing settings or
  writing to the production database.
  - Agents may read the production database through `codex-handoff/bin/q.sh` (read-only SQL).
  - Agents may build images under a different tag and run an isolated compose project on other ports.
- **The orchestrator owns git.** Agents commit on their own branch in their own worktree. The orchestrator merges, tests
  and pushes. Nobody force-pushes except the orchestrator.
- Truthful answers only. Never mark an application confirmed without evidence. Never submit duplicates.

## Where things are

| What | Where |
|---|---|
| New canonical repo (this one) | `~/Desktop/applypilot/axiom` → GitHub `premalshah999/AXIOM` (`main`) |
| Legacy, October 4 (redacted) | AXIOM branch `legacy/oct-04` |
| Legacy, before the overhaul (redacted) | AXIOM branch `legacy/oct-20` |
| Old production source (before the cut-over; legacy) | `~/Desktop/applypilot/v3` (repo `ApplyPilot`, branch `v3-oct17`) |
| Live compose project (since the 2026-10-20 cut-over) | `~/Desktop/applypilot/axiom/compose.yaml` with `axiom/.env` (project `applypilot`; containers `applypilot-app-1`, `applypilot-db-1`) |
| Deploy | `cd ~/Desktop/applypilot/axiom && docker compose up -d --build --no-deps app`, between `hold.sh draining` and `hold.sh complete` |
| Status and SQL helpers | `~/Desktop/applypilot/codex-handoff/bin/` (`q.sh` read-only SQL, `api.sh`, `hold.sh`). `deploy.sh` is retired: it patch-builds the old tree |
| REA maps, tools, findings | `docs/rea/` |
| Agent worktrees | `~/Desktop/applypilot/axiom-wt/<workstream>` on branch `agent/<workstream>` |

The legacy branches were pushed with the owner's identifiers redacted, because AXIOM is public. The unredacted history
stays in the local `ApplyPilot` repo (`v1-oct04`, `v3-oct17`).

## Production state at the start (2026-10-20)

Production was paused for the iCIMS work:
- the job board's auto-apply is off;
- the token cap was raised for iCIMS (`job_token_cap` 2,000,000; restore to 600000);
- several jobs were paused;
- `WORKERS=6`.

The worker Chrome (9224) and the twin Chrome (9225, iCIMS hCaptcha tokens, `python -m jobpilot.cli twin-browser`) run
on the Mac host.

iCIMS now works end to end. Seven confirmed engine applications on 2026-10-19:
- ICE Java and ICE Full Stack;
- Rovisys;
- iPipeline SDE and SQE;
- Goldbelt;
- Eide Bailly.

## Workstreams

| # | Workstream | Agent | Branch / worktree | Deliverables |
|---|---|---|---|---|
| 1 | Failure forensics | Sonnet 5.5 | `agent/forensics` | `docs/forensics/FAILURE_ANALYSIS.md`: every unsuccessful run clustered by ATS × root cause, with counts, evidence and code pointers. Fixes and regression tests for the top clusters. A list of obviously wrong pipeline flows |
| 2 | Architecture and de-personalization | Sonnet 5.5 | `agent/architecture` | Personal logic and data removed (profile-driven instead). `docs/ARCHITECTURE.md` (system design, module map, data flow, worker pools). API review with a cleaner route design. Target directory layout, executed after the first merges |
| 3 | Infrastructure, reproducibility, workers | Sonnet 5.5 | `agent/infra` | One Dockerfile and compose with pinned versions. Stale services removed. One frontend build. A complete `.env.example`. One-command bootstrap. CI workflow. The 6 + 2 worker topology with specialist escalation and budget. `docs/SETUP.md` for friends and family, proven by a fresh-clone run on isolated ports |
| 4 | QA and tests | Sonnet 5.5 | `agent/qa` | Suite organised by markers (unit, integration, browser, regression). Flaky tests fixed. A fast CI subset. Missing API and flow tests added. Code review of recent changes and of the other branches before merge |
| 5 | ATS performance | Sonnet 5.5 | `agent/perf` | Per-ATS timing from the production database. Optimisations like Workday's (fewer redundant writes, scans, waits and model calls), with regression tests. Per-ATS flow and timing docs |
| — | Orchestration, knowledge base and retrieval, cut-over | Opus (main) | `main` | Merges, test gates, deploys, knowledge base design, retrieval improvements, production restore, final validation, this document |

## Integration order

1. Forensics fixes, performance fixes, QA test hardening. These are small, behaviour-level changes and merge first.
2. Infrastructure: image, compose, CI, worker topology.
3. Architecture restructure: moves and splits, on top of everything merged. Then the QA re-verify pass.
4. Cut-over: production deploys from `axiom`.
   - Settings restored.
   - Six ATS workers and two specialist workers.
   - The owner tests the web app alone.

Merge gate: the branch's targeted tests pass, `scripts/dev/pytest-locked.sh -m "not browser"` passes, and the full suite
passes before each production deploy.

## Status board

| Date | Item | State |
|---|---|---|
| 2026-10-20 | Legacy branches pushed (redacted), AXIOM `main` seeded from the October 20 snapshot | done |
| 2026-10-20 | REA maps, tools and findings copied to `docs/rea/` (redacted) | done |
| 2026-10-20 | Agent worktrees created; five agents started | done |
| 2026-10-20 00:10 | All five agents stopped on the first account's session limit with one commit between them (QA: `.gitignore` hid `jobpilot/data/employer_catalog.json`, so a fresh clone could not start; cherry-picked to `main`) | done |
| 2026-10-20 | Second account. **At most two agents run at once.** Forensics and infra resumed; architecture, QA and performance are queued | in progress |
| 2026-10-20 | Knowledge base and validators (orchestrator, `main`). Every refusal since October 14 reviewed; status-defined sponsorship answers (University of Chicago, Intel), U.S.-person status, background-check consent, e-signatures, topic and critical-regex false positives. Two wrong deterministic answers fixed (Intel "No" through a DACA shortcut; background-check willingness "No"). `docs/KNOWLEDGE.md` and `tests/test_screening_validation.py` | done, deploy pending |
| 2026-10-20 | Infra merged (`a2c3a2a`): one image, `app` + `db` compose, pinned versions, 6+2 topology (`jobpilot/specialist.py`), CI, `docs/INFRA.md`, `docs/SETUP.md`. Forensics merged (`d34ea60`): `docs/forensics/FAILURE_ANALYSIS.md`, embedded Greenhouse code fix, exception diagnostics, masked-phone repair guard | done |
| 2026-10-20 | Production: 12 hidden successes reconciled to `confirmed` (Perpay, Ford, Texas Instruments, United Placement, Booz Allen ×2, Chewy, HPE ×3, Viridien, Visa) from their recorded triage evidence | done |
| 2026-10-20 ~02:00 | **Cut-over: production now runs from `axiom` `main` (`b49ee2a`).** `axiom/.env` was made from the old `.env`: `COMPOSE_PROJECT_NAME=applypilot`, the existing `POSTGRES_PASSWORD`, `NORMAL_WORKERS=6`, `SPECIALIST_WORKERS=2`. `docker compose up -d --remove-orphans` recreated `applypilot-app-1` and `applypilot-db-1` (volumes kept) and removed `browser-cleanup`. Deploy from now on: `cd ~/Desktop/applypilot/axiom && docker compose up -d --build --no-deps app`, under the hold. The old `ApplyPilot/compose.yaml` and `codex-handoff/bin/deploy.sh` must not be used again | done; hold release and post-checks pending |
| 2026-10-20 | Hold released after the cut-over checks: data intact (5,899 jobs, 4,998 runs, 678 facts); runtime reports 6 normal + 2 specialist workers, scheduler active. `job_token_cap` restored to 600,000 (company limit 12) | done |
| 2026-10-20 | The other 47 `submission_unknown` jobs triaged read-only against their saved final pages: none shows a confirmation (most have no saved page). They stay unknown; `autopilot.resubmit_unknown` decides them with its receipt check | done |
| before the final push | **Scrub the public history.** The first pushes (`legacy/oct-04`, `legacy/oct-20`, `main` up to `b8de459`) still contain residues the first redaction missed: the surname, date of birth, schools, a former employer and cities. In a fresh mirror clone, run `git filter-repo --replace-text ~/Desktop/applypilot/codex-handoff/history-scrub.txt` (local only, never committed: its patterns are the personal details) over all branches, check with `git grep` on every ref, then force-push every branch. The mapping is the same one applied to `main`'s working tree, so `HEAD` content does not change. Run it only when no agent worktree has unmerged work, because every hash changes | pending |
| last step | Turn the job board's auto-apply back on (`PUT /api/job-board/settings` with `auto_apply: true`; it starts a batch of `batch_target`), unpause the held jobs (Schwab ×3, By Light), after the final deploy. Atlassian and the duplicate iPipeline stay paused | pending |
| 2026-10-20 | Personal details removed from tests and REA docs before pushing: the owner's school, former employer, internship city, county, metro region and OPT expiry date are replaced with synthetic equivalents | done |
| 2026-10-20 | De-personalization leftovers found on the way: a hard-coded home town in `jobboard.US_PLACE` (removed); surname leftovers in tests and comments (fixed); an absolute scratch path in `docs/rea/tools/drive.mjs` (fixed). Owner-specific comments ("Springfield", "Bethesda") remain for the architecture pass | partly done |

## How to resume

1. Read this file, then `docs/rea/maps/<ats>.md` for whichever ATS is in play.
2. Run `git -C ~/Desktop/applypilot/axiom worktree list` to see each workstream's branch, and `git log` on each branch
   for what was done.
3. Production health:
   - `sh codex-handoff/bin/api.sh GET /api/runtime-health` (engine version);
   - `sh codex-handoff/bin/q.sh "select state, count(*) from runs group by state"`.
4. Continue the first row of the Status board that is not done.
