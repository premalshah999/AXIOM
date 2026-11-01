# AXIOM

AXIOM finds jobs that fit you, fills in each employer's real application form on your own Mac, answers screening questions
from facts you gave it once, submits, and keeps a record of what proved each application arrived: the employer's confirmation
page or its email. It reads your mail to pick up verification codes and receipts, and to count who wrote back.

It works on Workday, Oracle, iCIMS, SuccessFactors, Greenhouse, Lever, Ashby, SmartRecruiters, Workable and many company career
sites. It uses no job-board account: it does not log in to LinkedIn or Indeed, and it applies at the employer's own site.

## Where it stands

- **One person has used it so far: its author.** It hasn't been walked through on a second Mac.
- **Completion, for that one applicant,** in the seven days to 2026-11-01: 1,101 of 1,199 applications were confirmed by the employer
  (91.8%), 69.8% on the first try. Postings that closed, jobs already applied to and steps only the applicant can do are left out of the
  1,199. Greenhouse 98.7%, Oracle 98.6%, Workday 97.0%, Workable 95.5%, Lever 95.2%, Ashby 92.0%, SuccessFactors 89.6%,
  SmartRecruiters 85.3%, iCIMS 83.0%, company sites 77.5%.
- **Whether it gets anyone hired is not known yet.** Completion is not an outcome. The app has just started counting replies.
- AI costs about a cent per confirmed application on recent runs.

## What you need

| | What | Why |
|---|---|---|
| 1 | A **Mac with Apple silicon**, macOS 15 or newer, 16 GB of memory | Chrome runs on your Mac, one window per application. Keep the Mac awake and plugged in while it applies |
| 2 | **Google Chrome** | Employer sites load in a real Chrome |
| 3 | An **AI key** from [OpenRouter](https://openrouter.ai) | Reads job pages and writes your answers. You pay OpenRouter directly; a few dollars goes a long way |
| 4 | A **Gmail** account with 2-Step Verification | Employers mail one-time codes and receipts; AXIOM reads them with an app password |
| 5 | *(Optional)* **Telegram** on your phone | Questions only you can answer reach you there as well as in the app |

## Install

1. Download `AXIOM.dmg` from [Releases](https://github.com/premalshah999/AXIOM/releases) (or build it: `desktop/build.sh`), open it, and drag AXIOM onto Applications in Finder.
2. Open it. AXIOM isn't signed with an Apple Developer ID yet, so the Mac says it could not be verified. Click **Done**, open **System Settings → Privacy &
   Security**, scroll to Security and click **Open Anyway**. You do this once. [Every step, with what has and hasn't been checked](docs/FRIENDS.md).
3. The setup guide opens by itself: your resume, contact details, work authorization, the jobs you want, screening questions, self-identification, permissions,
   your AI key and Gmail. It saves as you go.

**Automatic submission starts off.** Nothing goes to an employer until you allow it, and what AXIOM sends is yours to stand behind: it applies in your name.

## Using it

| Page | What it's for |
|---|---|
| **Overview** | What it's doing now |
| **Jobs** | What it can apply to, what it's applying to, what it couldn't apply to and why. Paste a link to add a job yourself |
| **Needs you** | A question only you can answer. Answer once and AXIOM remembers |
| **Applications** | Every application with its proof, the answers it gave, and how many employers have replied |
| **Settings** | Limits (a daily limit, and at most 3 applications per employer unless you change it), connections, your saved answers |

The answers come from what you told it, and a question it can't answer from those comes to you. Work-authorization answers follow the facts you saved, not the model.
Some employer sites show a captcha: AXIOM solves what it can, in your Chrome, and can use a 2Captcha key you add. It creates an account at an employer's site when the
application needs one, if you allow that in the setup guide.

## What leaves your Mac

Your resume, answers and employer passwords are stored on your Mac, encrypted where they are secrets. These leave it:

- **The applications themselves**, to the employers.
- **Text for the AI model**, through OpenRouter to the model you chose: job postings, the questions on a form, and the resume and profile facts needed to answer them.
- **Searches for jobs**, to public job boards and web search: the roles and places you target.
- **Employer web addresses**, to Google's favicon service and hiring platforms, to show logos.
- **Once a day**, a request to GitHub for the newest release number. The app never updates itself.
- **If you turn them on:** questions to Telegram, and captcha pictures to 2Captcha.

It has no telemetry and no account with us. Nothing you do reaches the authors.

## If something goes wrong

**Help → Copy Diagnostics** gives you a report with no passwords, keys or mail in it. [docs/DESKTOP.md](docs/DESKTOP.md) says where everything lives.

## For developers

[docs/DESKTOP.md](docs/DESKTOP.md) (the Mac app), [docs/NATIVE.md](docs/NATIVE.md) (the supervisor), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/RUNTIME.md](docs/RUNTIME.md) (how a run works), [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md) (how questions are answered), [docs/API.md](docs/API.md),
[DEVELOPER.md](DEVELOPER.md). The older Docker installation is in [docs/SETUP.md](docs/SETUP.md).

AGPL-3.0-only. Attribution to [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) is preserved. See [third-party notices](docs/third-party.md).
