# Setting up AXIOM on your computer

AXIOM is a private dashboard plus a small crew of browser workers that find jobs, fill in applications and keep a record
of what was sent. It runs entirely on your computer. Your resume, answers and employer accounts stay in Docker volumes
on your machine; nothing is uploaded anywhere except the requests to the model provider you choose and the employer
sites you apply to.

## What you need

| Need | Notes |
|---|---|
| A Mac | macOS is the supported host. The worker Chrome is a normal Chrome on your Mac. Linux works for the dashboard and the built-in headless browser, but not for the host-Chrome workers (see Troubleshooting). |
| [Docker Desktop](https://docs.docker.com/get-docker/) | Version 4.30 or newer (Compose v2). In Settings > Resources give it at least 6 GB of memory. |
| [Google Chrome](https://www.google.com/chrome/) | The workers drive a real Chrome, because many employer sites distrust headless browsers. |
| git | To download AXIOM and update it later. |
| About 6 GB of free disk | The image is 2.5 GB, plus the database and your data. |
| A model API key | One of DeepSeek, Xiaomi MiMo or Anthropic. This is what reads job pages and writes answers. A few dollars goes a long way. |
| A Gmail account (recommended) | Employers email one-time codes and verification links. AXIOM reads them from the mailbox you connect, with a Google app password. |

Optional: a 2Captcha key (solves captchas, paid per solve) and a Telegram bot (asks you questions on your phone).

## Install: one command

```sh
git clone https://github.com/premalshah999/AXIOM.git
cd AXIOM
make up
```

`make up` is the same as `scripts/setup.sh`. It:

1. creates `.env` (private to you, mode 600) with a generated database password and your computer's time zone;
2. builds the image (the first time it downloads about 1 GB and takes 5 to 10 minutes);
3. starts the database and the app, and waits until both are healthy;
4. prints the address to open, normally <http://127.0.0.1:8080>.

The database tables are created by the app the first time it starts; there is no separate migration step. Running
`make up` again is safe: it never replaces your `.env` or your database password.

## First run

1. Open the address. A fresh install lets you create the one account for this computer (name, email, password). After
   that, only that account, or the access token, can sign in.
2. Follow the setup guide: resume upload, contact details, work authorization, the roles you want, screening
   questions, self-identification, permissions and a final review. Answers are stored once and reused.
3. **Automatic submission is off.** Nothing is sent to an employer until you switch it on in Setup. Start with a dry run
   on a few jobs and read what the workers did.

## Add your keys

Edit `.env` (it is in the AXIOM folder; it never leaves your computer and is never committed), then apply with
`docker compose up -d`.

**Model.** Choose the provider and paste its key. The default is DeepSeek:

```
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
```

For Anthropic use `LLM_PROVIDER=haiku` with `HAIKU_API_KEY` (or `ANTHROPIC_API_KEY`); for Xiaomi MiMo use `mimo` with
`MIMO_API_KEY`. After the first start you can pick a different model for answers and for navigation in Setup > System.

**Gmail app password.** Google only gives app passwords to accounts with 2-Step Verification.

1. Turn on 2-Step Verification in your Google Account (Security).
2. Open <https://myaccount.google.com/apppasswords>, create an app password named AXIOM and copy the 16 letters.
3. In AXIOM open Setup > Email and paste your Gmail address and the app password. The password is checked, encrypted
   and never shown again. (You can instead put `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.env`.)
4. The mailbox must be the same address as your profile email, because AXIOM matches codes by recipient.

## The worker Chrome (for real job sites)

Without this, AXIOM uses a headless browser inside Docker. That is enough for the built-in demo (`make verify`) but many
employers block it. For real applications run a normal Chrome on your Mac and point AXIOM at it:

```sh
make chrome          # starts Chrome with a debugging port on 127.0.0.1:9224, using its own profile
```

Then add this line to `.env` and run `docker compose up -d`:

```
BROWSER_CDP_URL=http://host.docker.internal:9224
```

Leave that Chrome open while applications run. AXIOM opens its own tabs in it and closes them when a job is finished;
do not browse in it. It uses a separate profile (`.data/desktop-browser`), so it never touches your everyday Chrome.
If it is already running, `make chrome` does nothing.

### Twin Chrome (only for iCIMS-style hCaptcha sites)

Some sites distrust any browser a program is attached to. A second, untouched Chrome mints their captcha tokens:

```sh
make twin            # port 9225, starts with no window
```

and in `.env`: `BROWSER_TWIN_CDP_URL=http://host.docker.internal:9225`. Nothing else may attach to it.

## Ports

| Port | What | Reachable from |
|---|---|---|
| 8080 | The dashboard and API (`APP_PORT`) | Your computer only (`APP_BIND=127.0.0.1`) |
| 9224 | Worker Chrome debugging port | Your computer only; Docker reaches it as `host.docker.internal` |
| 9225 | Twin Chrome debugging port | Same |
| 5432 | PostgreSQL | Not published: only the app container can reach it |

To use another dashboard port set `APP_PORT=8081` before the first `make up`, or in `.env` set both `APP_PORT=8081` and
`BASE_URL=http://127.0.0.1:8081`, then `docker compose up -d`. `BASE_URL` must equal the address you type in the browser.
Do not set `APP_BIND=0.0.0.0` unless you trust everyone on your network: there is no TLS.

## The workers

By default AXIOM runs eight browser workers:

- **Six ATS workers** take new applications. Each runs the flow of the hiring platform it is given (Workday, Oracle,
  iCIMS, Taleo, SuccessFactors, Eightfold, Greenhouse, Lever, Ashby and others).
- **Two specialists** take applications that are difficult or have already failed, and have a somewhat larger budget (1.5
  times the time, model calls and token cap).

Eight workers need a few GB of memory. On a small machine lower them in `.env`, for example
`NORMAL_WORKERS=3` and `SPECIALIST_WORKERS=1`, then `docker compose up -d`. The full list of settings, and what each does, is
in `.env.example`.

## Everyday use

| Task | Command |
|---|---|
| Start | `make up` |
| Stop (your data is kept) | `make down` |
| See the logs | `make logs` |
| Container status and health | `make ps` |
| Run the built-in demo end to end | `make verify` |
| Update to the latest version | `make update` (pause applications first; the app restarts) |
| Print the access token | `make token` (treat it like a password) |

**Back up** the two Docker volumes (`<project>_postgres` and `<project>_applicant-data`, where the project is `axiom`
unless you changed it) and your `.env`. The `applicant-data` volume holds your uploaded resumes, the mailbox encryption
key and the saved run evidence; losing the key means re-entering mailbox and employer-account passwords.

**Start over completely** with `docker compose down -v`. This deletes the database and all your data. There is no undo.

## Troubleshooting

**"Docker is installed but not running".** Open Docker Desktop and wait for the whale to stop animating, then rerun.

**"port is already allocated".** Something else uses 8080. Set `APP_PORT` and `BASE_URL` as described under Ports.

**"Origin is not allowed. Check BASE_URL" or buttons do nothing.** The address in the browser differs from `BASE_URL`.
`localhost` and `127.0.0.1` count as different. Use the address `make up` printed, or fix `BASE_URL` in `.env`.

**The app container is unhealthy.** Run `make logs`. A wrong or missing model key does not stop the app from starting;
a database error does. `password authentication failed` means `POSTGRES_PASSWORD` in `.env` is not the one the existing
database volume was created with: restore the old value (the volume keeps its first password).

**A blank page or "Build the dashboard".** The image was built without the dashboard. Rebuild with
`docker compose build --no-cache app && docker compose up -d`.

**Applications stay in "waiting browser".** The worker Chrome is not reachable. On your Mac run
`curl http://127.0.0.1:9224/json/version`; if that fails, run `make chrome`. Then check `.env` has
`BROWSER_CDP_URL=http://host.docker.internal:9224` and that you restarted the app after adding it.

**Mail codes never arrive.** Open Setup > Email: it shows whether the login works. Check the app password, that IMAP is
enabled in Gmail settings, and that the mailbox is your profile email.

**Disk is filling up.** `docker system df` shows what Docker uses. `docker image prune` removes superseded images.
Never run `docker volume prune` while AXIOM is stopped: it would delete your data volumes.

**Linux or Windows.** The dashboard and the built-in headless browser work. The host-Chrome workers do not: Chrome's
debugging port listens on 127.0.0.1 only, and a Linux container cannot reach the host's loopback through
`host.docker.internal`. Use a Mac for the full setup.

## Uninstall

```sh
make down                              # stop, keep the data
docker compose down -v --rmi local     # stop and delete the data and the image
```

Then delete the AXIOM folder. The worker Chrome profile is in `.data/` inside it.
