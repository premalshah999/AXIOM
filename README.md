# AXIOM — your job-application autopilot

AXIOM finds jobs that match you, fills in the employer's application form the way you would, answers screening
questions from facts you gave it once, and keeps a record of every application it sent. It works on the big hiring
systems (Workday, Oracle, iCIMS, SuccessFactors, Taleo, Greenhouse, Lever, Ashby, Eightfold, Dayforce and many company
career sites).

It runs **on your own computer**. Your resume, answers and employer passwords stay on your Mac. The only things sent out
are the job applications themselves and the text the AI model reads to answer a question.

> In the app the dashboard is called **ApplyPilot**; AXIOM is the name of the project.

---

## What you need

| | What | Why | Cost |
|---|---|---|---|
| 1 | A **Mac** with **16 GB of memory** or more | AXIOM drives a real Chrome browser on your Mac | — |
| 2 | **Docker Desktop** ([download](https://www.docker.com/products/docker-desktop/)) | Runs the AXIOM app and its database | Free for personal use |
| 3 | **Google Chrome** ([download](https://www.google.com/chrome/)) | Employer sites trust a real Chrome | Free |
| 4 | An **AI key** from DeepSeek (recommended), Anthropic or Xiaomi MiMo | Reads job pages and writes your answers | DeepSeek: about $5 lasts for hundreds of applications |
| 5 | A **Gmail** account with 2-Step Verification | Employers email you one-time codes; AXIOM reads them for you | Free |
| 6 | *(Optional)* **Telegram** on your phone | AXIOM asks you questions it cannot answer on its own | Free |

You do **not** need to know how to program. You will type three short commands, copied from this page.

---

## Step 1 — Install Docker Desktop and Chrome

1. Install **Google Chrome** if you do not have it.
2. Download and install **Docker Desktop**, open it once, and accept its terms. Leave it running (the whale icon in the
   menu bar).
3. In Docker Desktop open **Settings → Resources** and set **Memory to 6 GB**. Click **Apply & restart**.
   (More is not better here: AXIOM needs the rest of your memory for Chrome.)

## Step 2 — Download AXIOM

1. Open the **Terminal** app (press `⌘ + Space`, type `Terminal`, press Enter).
2. Copy these two lines, paste them into Terminal and press Enter:

   ```sh
   git clone https://github.com/premalshah999/AXIOM.git
   cd AXIOM
   ```

   If your Mac asks to install "command line developer tools", click **Install**, wait for it to finish, and paste the
   two lines again.

## Step 3 — Start AXIOM

In the same Terminal window, paste:

```sh
make up
```

The first start downloads about 1 GB and takes 5–10 minutes. When it is ready you will see:

```
AXIOM is running:  http://127.0.0.1:8080
```

`make up` also starts the two Chrome windows AXIOM works in (one for applications and a small "twin" for security
checks). You can minimise them, but do not close them while applications run.

**From now on**, after restarting your Mac: open Docker Desktop, open Terminal, and run `cd AXIOM` then `make up`.

## Step 4 — Create your account

Open **http://127.0.0.1:8080** in your normal browser and create your account (name, email, password).

You will see **ten recovery codes** once. Save them somewhere safe: they are the only way back in if you forget your
password.

## Step 5 — Connect your AI key (Setup → Connections)

Everything you connect is entered **in the app**. You never need to edit a settings file.

**Get a DeepSeek key (recommended):**

1. Go to [platform.deepseek.com](https://platform.deepseek.com), sign up and add credit (Top up → $5 is plenty to start).
2. Open **API keys → Create new API key**, name it `AXIOM`, and copy the key (it starts with `sk-`).

**Add it to AXIOM:**

1. In AXIOM open **Setup → Connections**.
2. Under **AI model** choose **DeepSeek**, paste the key into **DeepSeek API key**, and click **Save connections**.
3. Click **Test** next to the key. You should see **Connected**.

Prefer Anthropic (Claude)? Get a key at [console.anthropic.com](https://console.anthropic.com), choose **Anthropic
Claude Haiku** as the provider and paste it into **Anthropic API key**.

Keys are encrypted on your Mac and never shown again. To change one, paste the new one and save.

## Step 6 — Connect your email (Setup → Email)

Employers email codes like "Your verification code is 482913". AXIOM reads them so applications do not stop.

1. Your Google account needs **2-Step Verification** (Google Account → Security).
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an app password named
   `AXIOM`, and copy the 16 letters.
3. In AXIOM open **Setup → Email**, enter your Gmail address and the app password, and save.

Use the **same email address** you put in your profile: AXIOM matches codes by who they were sent to.

## Step 7 — Build your knowledge base

This is the most important step. Everything AXIOM writes on an application comes from what you tell it here, and it
**never invents an answer**: if something is missing it asks you once and remembers.

1. **The setup guide** opens after you sign up (you can reopen it from the dashboard). It walks you through:
   - **Resume** — upload your PDF. AXIOM reads it and pre-fills the next steps (this needs the AI key from Step 5).
   - **Contact details and address.**
   - **Work authorization** — citizenship, visa status, whether you will need sponsorship.
   - **The jobs you want** — titles, locations, remote or on-site, salary expectation.
   - **Screening questions** — the common ones (notice period, willingness to relocate, background check, and so on).
   - **Self-identification** — gender, ethnicity, veteran and disability status. You may choose "decline to answer".
   - **Permissions** — which routine consents AXIOM may accept for you.
2. **Setup → Profile** — check your education and work history. Dates, job titles and employers here are what fill
   the "Work experience" and "Education" sections of every form.
3. **Setup → Knowledge** — answers you have given, short stories AXIOM may reuse in essays ("Why do you want to work
   here?"), and anything you taught it about a specific employer. Edit or delete anything here at any time.
4. **Setup → Resumes** — upload more than one resume (for example "Data Science" and "Software"); AXIOM picks the one
   that fits each job.

**Tip:** the more complete your profile, the fewer questions AXIOM asks. When it does ask (the **Needs you** list on
the dashboard, or Telegram), answer once — it saves the answer for every later application.

## Step 8 — Tell it which jobs to find (Setup → Discovery)

Set your target roles, locations and how recent the postings should be. AXIOM searches job boards and company career
sites on its own and shows matches under **Jobs**.

You can also **add a job yourself**: open **Jobs → Pipeline**, paste one or more posting links into **Add job links**,
and press Enter. Each one goes straight into the queue.

## Step 9 — Try it, then let it run

1. **Automatic submission starts OFF.** Pick a few jobs and click **Apply**. AXIOM fills the form and stops just
   before submitting, so you can see what it did (open the job and look at its timeline and screenshots).
2. When you are happy, turn on **Automatic submission** (on the dashboard's first-run checklist, or in
   **Setup → System**). From then on AXIOM applies
   continuously within your daily limit (**Setup → System → Limits**).

---

## Optional: questions on your phone with Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts and copy the **bot token** it gives you.
2. Message **@userinfobot** and copy your numeric **user ID** (a number, not your @username).
3. In AXIOM open **Setup → Connections → Telegram**, paste both and save. Click **Test**.
4. Open your new bot in Telegram and press **Start**. Within a minute it starts sending questions and updates.

## Optional: captcha solving

Some employer sites show image puzzles. AXIOM solves most of them itself; for the rest you can add a
[2Captcha](https://2captcha.com) key in **Setup → Connections → Captcha solving** (paid per puzzle, a few cents each).

---

## Reading the dashboard

| Group | What it means | What you do |
|---|---|---|
| **Active** | Being worked on right now, or waiting for a free worker | Nothing |
| **Needs you** | A question only you can answer (for example a reference's name, a work sample, a video) | Answer it once; AXIOM continues |
| **Repairing** | Something on the employer's site went wrong; AXIOM retries automatically | Nothing, usually |
| **Unconfirmed** | AXIOM clicked Submit but the employer showed no clear confirmation; it keeps checking your email for one | Check your email if you want to be sure |
| **Done** | Submitted and confirmed by the employer's page or email | Nothing |

## Everyday commands

Open Terminal and type `cd AXIOM` first.

| To | Type |
|---|---|
| Start AXIOM (also after a restart) | `make up` |
| Stop AXIOM (your data is kept) | `make down` |
| Update to the latest version | `make update` |
| See what is happening | `make logs` (press `Ctrl + C` to stop watching) |

## Keeping your Mac fast

AXIOM uses a real Chrome window for each application it works on at the same time.

- On a **16 GB Mac**, AXIOM runs **3 applications at once** (set automatically). Close other heavy apps while it runs.
- Docker Desktop should have **6 GB** (Step 1). More does not make AXIOM faster.
- If your Mac feels slow, run `make down`, quit and reopen Chrome, then `make up`.

---

## Troubleshooting

**"Docker is not running".** Open Docker Desktop and wait until the whale icon stops animating, then run `make up`.

**The page at http://127.0.0.1:8080 does not open.** Run `make up` again and use exactly the address it prints.

**"No API key for the model provider".** Open **Setup → Connections**, paste your AI key, save, and click **Test**. If
the test says the account has no credit, add credit on the provider's website.

**Email codes are not picked up.** In **Setup → Email** check that the address is the same as your profile email and
that you used an app password (16 letters), not your normal Gmail password.

**An application keeps asking the same question.** Answer it in **Needs you**; it is saved for all later applications.
To change a saved answer, open **Setup → Knowledge**.

**A site says "already applied".** AXIOM records it as done; nothing is sent twice.

**I forgot my password.** On the sign-in page choose **Forgot password** and use one of your recovery codes.

**Something else.** Run `make logs`, copy the last lines, and open an issue on GitHub.

---

## Privacy and safety

- Everything is stored in Docker on your Mac. Keys and employer passwords are encrypted.
- AXIOM answers only from facts you gave it. Legal and personal questions are never guessed.
- Nothing is submitted until you turn on automatic submission.
- Each employer's own rules (like "one application per person") are respected.

## For developers

[docs/SETUP.md](docs/SETUP.md) (installation details), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/KNOWLEDGE.md](docs/KNOWLEDGE.md) (how questions are answered), [docs/API.md](docs/API.md),
[docs/INFRA.md](docs/INFRA.md), [DEVELOPER.md](DEVELOPER.md). Every setting is listed in [.env.example](.env.example);
service keys can be entered in **Setup → Connections** instead and take precedence over `.env`.

AGPL-3.0-only. Attribution to [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) is preserved. See
[third-party notices](docs/third-party.md).
