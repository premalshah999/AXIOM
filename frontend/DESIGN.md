# AXIOM dashboard design system (2026-10-31)

Read this before touching a page. It holds two stages: the **family** every page is made from, and the **reference pages** (Overview, Jobs and its drawer, Applications) to copy. Needs you, Resumes, Profile, Settings and the sign-in screens are on the new shell and tokens but not yet redesigned; section 7 says what each must become.

## 1. Rules

- **Design around the task.** Ask what the person came here to do, and put that first. A token swap is not a redesign. If a page is still "title, tabs, rows of inputs", it isn't done.
- **Judge like the owner.** Do a real task on the page with real data, and look at the screenshot: funded company's product, or college project? Passing contrast or overflow checks says nothing about that.
- **Remove words.** Translate engineer terms: never "pipeline", "worker slots", "rung", "ch 3", "execution-v2-...", "Answer model", "page acknowledgement". Say "Started", "busy/idle", "step", "Confirmation page", "Employer email". Real data only; nothing invented.
- **Vermilion means the machine is live or something needs you.** Never decoration, never a brand colour, never a primary button. `--live` is vermilion only while `data-run="on"` on `<html>` (real state, `main.tsx`).
- **State is lamp behaviour plus words, never a new colour.**
- **Every number opens the list behind it.** Use `ax-quiet-link`, a row button, or `MoreLink` ("All 3,060 →"). If the list shows a subset, say so in a caption.
- **Sentence case everywhere.** No `text-transform`. No decorative uppercase eyebrows, serif headings, side rails of cards, glassmorphism, card glow, decorative gradients, emoji or violet.
- **Stat tiles only where a number is really the point of the page.** No row of four out of habit.
- **At most one primary button per view.**
- **Every state considered:** loading (Skeleton), empty (Empty, saying what will appear and offering the next action), error (LoadError or ErrorLine, with retry), disabled, and long names (ellipsis plus a title).
- **Files and links must work in the native window.** A download is a plain link to a URL the app serves (`<a href="/api/..." download>` over a `Content-Disposition: attachment` response, as for a resume, a log or the Applications export), never a Blob made in the page: a webview may not save those. A link to another site is `<a target="_blank" rel="noreferrer noopener">` with an `http(s)` address (`External`, or the `ax-btn ax-lead` link in Applications): the window hands it to the system browser, and the label says so for a screen reader.
- **No document scroll; no horizontal page scroll at 390px; no layout shift on poll.** Motion is CSS only, fast, off under `prefers-reduced-motion` (`base.css` handles it).
- **Colours only in `tokens.css`** (`tests/test_identity_contrast.py` fails otherwise, and measures every pair AA in both themes). Fonts stay bundled. Never fetch logos or anything else from a third party: it would leak the job list.

## 2. Tokens (`src/styles/tokens.css`)

Two themes on `<html data-theme>`: dark (graphite, the default for a missing or unknown value) and light (bone). The choice is `localStorage["axiom.theme"]`, applied before first paint by the script in `index.html`; `readTheme` in `shell/prefs.ts` must agree.

**Surfaces, back to front.** Depth comes from these plus 1px hairlines, not from shadows.

| Token | Used for |
|---|---|
| `--ground` | Behind everything: the sidebar, the phone's bars |
| `--sheet` | The page. Nothing else may be page-coloured |
| `--raised` | Panels and tables on the page |
| `--overlay` | Drawers, dialogs, menus, toasts, the palette |
| `--inset` | A well cut into a surface: progress tracks, code, the drawer's status block, segmented controls, the bulk bar |
| `--field` | An input |
| `--window` | The display window: stays dark in both themes (`.ax-window`, Overview only). What sits inside takes dark values automatically |
| `--hover`, `--active` | Translucent overlays that work on any surface |
| `--surface` | **Not a colour of its own**: the current opaque surface, for sticky heads, pinned cells and save bars. Anything that sets a background must set this too |

**Lines:** `--line` (between things), `--line-soft` (between rows), `--line-strong` (a control's edge, 3:1).

**Text:** `--ink` (primary), `--ink-2` (secondary), `--ink-3` (quietest, still AA on every surface). Links and buttons never have their own colour.

**Controls:** `--btn`, `--btn-hover`, `--btn-active`, `--btn-edge`, `--btn-ink`, `--btn-sheen`; `--brand`, `--brand-hover`, `--brand-active`, `--brand-ink` (primary: bone on graphite, ink on bone).

**Signal:** `--led` (a lit dot), `--signal` (a lamp), `--signal-ink` (signal text), `--signal-soft` (a tint behind a count or lamp), `--signal-fill` + `--signal-fill-ink` (the destructive button that finally does it), `--bad` (an error's words). Window: `--window-edge`, `--window-rule`, `--window-ink`, `--window-ink-2`, `--dot-off`. Dots on a surface: `--dot-plate` (off), `--dot-lit` (on).

**Monograms:** `--mono-0` to `--mono-7`, `--mono-ink`. **Depth:** `--shadow-panel`, `--shadow-overlay`, `--scrim`. **Paper:** `--paper`, `--paper-shadow` (a resume page is white in both themes).

**Scale:** `--radius-sm` 6 (buttons, inputs, rows), `--radius` 8 (panels, tables, menus), `--radius-lg` 10 (the sheet, dialogs). `--control` 34, `--control-sm` 28 (40 and 36 on a touch screen, automatically). `--space-1` to `--space-12` on a 4px grid. `--gutter` 32 (16 on a phone). `--page-w`, `--form-w`, `--sidebar`. `--dur`, `--ease`.

### Type

Five sizes. Use the element or the token; never invent a size.

| Role | Token | Spec |
|---|---|---|
| Page title | `--text-page`, `h1` (the shell renders it) | 24, 600, slightly wide (`--wide`) |
| Section title | `--text-title`, `h2` | 15, 600, normal width |
| Body | `--text-body`, `h3` is this at 600 | 14 |
| Secondary | `--text-meta` | 13, with `--ink-2` or `--ink-3`. Labels, hints, table heads |
| Data | `--text-data`, `.mono`, `<time>` | 12.5 Fragment Mono. **Numbers, IDs and times only**, never words |

Doto (`.ax-read`) is for hero readouts of 56px and up, on Overview only. The Archivo and Doto files are the ones with the axes the look depends on (`full`, `standard`): don't swap them.

## 3. Shell (`src/shell/`, `src/styles/shell.css`)

- `html`, `body` and `#root` are the viewport and clip. `.ax-app` is the 232px sidebar beside `.ax-main`, the page **sheet**: a rounded surface with a hairline, forward of the sidebar. The sheet is a column: page header, banners, then one scroll region (`.ax-scroll`). Screens outside the shell (sign-in, setup guide) wrap themselves in `.ax-screen`.
- **Sidebar:** wordmark; Search (⌘K) as a quiet field; Overview, Jobs, Needs you (count), Applications, Resumes, Profile, Settings; a quiet "Coming soon" group; at the foot, one line of live status (`statusLine` in `identity/state.ts`) and the account row, whose menu holds who, the theme and sign out. Items come from `shell/nav.ts`.
- **Page header:** the shell renders the title from `nav.ts`. From inside a page, `<TopActions>` puts the page's actions at its right and `<TopLine>` puts one short line under the title, only if it helps. They portal and unmount with the page. Don't put a title in a page.
- **Phones (under 900px):** a slim top bar (wordmark, live status, search) and a five-item tab bar (Overview, Jobs, Needs you, Applications, More). More opens a bottom sheet with the other pages, the theme and sign out. Actions wrap under the title by themselves.
- **Command palette:** `⌘K` or Ctrl-K: pages, tabs, jobs (with monograms), Add a job, switch theme.
- **Scrolling:** a page that is one table uses `<div className="ax-page ax-page-fill">` and `<Table fill footer={<Pagination/>}>`: the table is the only scroller and its head stays put. Any other page is `<div className="ax-page">` and scrolls in `.ax-scroll`. Widths: `.ax-main` sets `--content-w`; Profile and Settings use `--form-w`.

### Routes

`src/context.ts` holds the one table: `canonical()` turns any old `view`, `tab` and `filter` into its home, `parseRoute()` applies it to the address, `go()` and `routeUrl()` apply it to code, and `main.tsx` rewrites the address bar. Add a new old name there, nowhere else. `route.q` carries a list's search in the URL, so a number elsewhere can open the list already searched.

| Page | `view` | Tabs (`tab`) |
|---|---|---|
| Overview | `batch` | |
| Jobs | `jobs` | `pipeline` (labelled Started), `discovered`, `reserved` |
| Needs you | `needs` | |
| Applications | `ledger` | `confirmed`, `unconfirmed`, `archive` |
| Resumes | `resumes` (the editor, `editor`, belongs to it) | |
| Profile | `profile` | `identity`, `experience`, `knowledge` |
| Settings | `settings` | `connections`, `accounts`, `email`, `discovery`, `system` |
| Coming soon | `soon-axe`, `soon-network`, `soon-emails` | |

Old names that must keep working: `?view=setup` (all its tabs), `/setup`, `?view=connections|accounts|email|discovery|system|sources`, `?gmail=`; `?view=setup&tab=profile&filter=identity|experience|preferences|facts|policies|rules`; `?view=setup&tab=answers`, `?view=knowledge|answers|knowledge base`; `?view=setup&tab=resumes`, `?view=resumes`, `?view=profile&tab=resumes` (opens Resumes); `?view=jobs&tab=archive`, `?view=archive`; `?view=successful|confirmed|applications|unconfirmed`; `?view=discovered|job board|pipeline|reserved`, `?page=job-board`, `?view=jobs&filter=new`; `?view=overview`; `?view=needs help|needs you|questions`. Anything unknown opens Overview.

## 4. The family (`src/ui/`, imported from `./ui/index`)

Build pages from these and they look right without new CSS. Write CSS only for what is really unique to the page, in a stylesheet of its own, prefixed for the page.

| Component | When, and how |
|---|---|
| **Button** | `variant`: `primary` (one per view), `secondary`, `ghost`, `danger` (asks first), `danger-solid` (does it). `size`: `md` 34, `sm` 28. `icon` leads, at 15px with `aria-hidden`. `loading` swaps the icon for a spinner and disables. `block` is full width |
| **IconButton** | An icon alone. `label` is required. `framed` gives it an edge; `size="md"` matches a Button |
| **Input, Textarea, Select, Field, Check** | A label over a control, with `hint` and `error`. `Select` is a form's plain select. `Field` wraps any other control. `Check` is a checkbox with its name and an optional note |
| **Listbox** | A filter or standalone choice as a designed menu, for every toolbar and pager. `{ value, options: [{value, label, count?}], onChange, label, prefix?, size?, align? }` |
| **Dropzone** | **The only way a file comes in. Never a visible native file input.** `{ file, onFile, accept, title, hint, label }` drags, clicks and keyboard; shows the chosen file with a remove button; refuses the wrong kind in words |
| **Switch** (`SwitchRow` in `setup-kit.tsx`) | An on/off setting that takes effect at once, with its name and one line |
| **Segmented** | Two to four choices in one well, when all should stay visible |
| **RouteTabs, Tabs** | Quiet words on a hairline with an optional `count`, the current one underlined. `RouteTabs` are real links. Use them only for views of the same thing |
| **Chips** (`worktools`) | Status filters: words with counts, the current one filled. A row of them is the filter; they scroll sideways on a phone |
| **SearchBox** (`worktools`) | The list's search, first in the toolbar |
| **Table** | A raised panel by itself; flat inside a Panel, Drawer or Dialog. 44px rows, sticky head. `columns: [{key, header, cell, num?, width?, truncate?, min?, hideBelow?: 860 \| 600, pin?, wrap?}]`, `fill`, `footer`, `selected` + `onSelectedChange` + `bulk` (checkboxes are always visible, the owner's decision), `canSelect`, `rowLabel`, `onRowClick`, `loading` (skeleton), `busy` (dims rows without jumping), `empty`, `error` |
| **JobCell** (`worktools`) | Monogram, employer and role as a table's first column. In a narrow table they stack and `meta` (status, time) shows under them, so hide those columns with `hideBelow: 600` |
| **Monogram, MonogramStack** | The employer's initial in a deterministic square, on every job, application and question row. `size`: `sm` 24 in a row, `md` 32, `lg` 40 in a drawer's head. Stack for several employers behind one thing |
| **StatusPill** (`worktools`), **Pill** | A lamp and a word. `StatusPill state=` reads `model.ts`. Lamps: applying pulses (`--live`), needs you is ringed, repairing is half lit, waiting is dashed, done is solid ink, stopped is hollow. Text stays ink: a column of them must stay calm. Put the reason in a Tooltip |
| **Panel** | A raised surface that groups something, `title`, `actions`, `flush` for a list or table. Inside a Drawer, Dialog or another Panel it's a plain section. Not everything needs one |
| **Dialog** | One short task, centred: add a job, answer one question. `{ open, onClose, title, sub, footer, width }`; footer buttons with the primary last; `data-autofocus` on the field |
| **Drawer** | A record opened from a list. `{ open, onClose, title, sub, lead, footer, width }`. Status and next step first, history below, raw detail in folds |
| **Popover** | The floating part of any menu: placement, flip, outside click, Esc, arrow keys. Use **RowMenu** (`worktools`) for a row's or drawer's "More"; a destructive item with `confirm` asks inside the menu. **ConfirmButton** asks in place on a page |
| **Tooltip** | What a number counts, why a status reads as it does. `<Tooltip label="...">`. Never the only place something essential is said: a phone has no hover |
| **Toast** | Outcomes, via `useNotify()` from `hooks.ts` or `useAction().run`. Errors stay until dismissed |
| **Skeleton** | Lists while loading, in the list's shape. `Loading` (a spinner line) only where no shape is known |
| **Empty** | `{ icon, title, hint, action, compact }`: what will appear, and a real button for the next action. A search with no result offers clearing it |
| **Notice, ErrorLine, LoadError** (`worktools`, `ui`) | A plain line; `warn` and `bad` get a lamp and a surface. LoadError retries |
| **Pagination** | `footer` of a Table. Client lists use `usePagination`; server lists pass a `Paging` |
| **Facts** (`worktools`) | Read-only label and value rows |
| **Fold** (`<details className="ax-fold">`) | Raw and rarely needed detail, tucked away |
| **`ax-rows` / `ax-row`** | A plain list of things that aren't a table. **`ax-quiet-link`** for a number that opens a list; **`ax-more-link`**, **`MoreLink`** for "All 44 →"; **`ax-link`** for an inline link, underlined |

**Focus:** every control has a visible ring in both themes (`:focus-visible` in `base.css`). **Keyboard paths must keep working:** ⌘K, Esc on every overlay, arrows in menus, Enter to submit.

## 5. Overview: the reference for a page with several parts (`src/batch.tsx`, `src/identity/`)

Reads complete at any activity level. In order:

1. **The top** (`Hero`, one Panel divided by hairlines): the Doto readout of confirmed today (from `confirmedToday`, opens Applications); the state sentence and any line under it (`panelState`, the worst true thing first: lost, restart, paused, complete, running, held, none); progress to target; the day chart; the lit window with the number of questions (opens Needs you; dark glass at zero); the round master key with one line under it. The key is Pause, Resume, Restart (with "Pause instead"), or Start with "Apply until N confirmed". Only lost and restart colour the sentence.
2. **Live work** (`LiveWork`, the dark window): one row per application really running: monogram, employer, role, a six-step track of dots (`RUNGS` in `identity/stages.ts`, from the worker's phase: open, sign in, fill, questions, review, send), what it's doing in words, time on the job, platform, Pause. Waiting-for-email runs are rows too, with the track unlit. Idle capacity is one figure and a row of dots, never empty columns. With nothing running, one sentence says why. The foot links what else waits (queued, being fixed, blocked in this batch).
3. **Get started** (only until the first job exists): a checklist with the next action per line.
4. **Two columns.** Left: `Questions` (top four by applications freed, each opening a Dialog with the real `QuestionCard`) and `Recent` (newest five confirmations, with their proof). Right: `Results`: the funnel and success by platform.

Data is real: `GET /api/funnel` (`pipeline.funnel`, read-only, all time: discovered, eligible, applied, applied by platform), and Confirmed is the Applications list itself. Bars share one scale from one baseline; only Confirmed is at full strength; the tail of platforms folds into Other. Tracks and bars are CSS dot patterns, drawn with `--led`, `--dot-lit`, `--dot-plate`.

## 6. Jobs: the reference for a list page (`src/jobs.tsx`, `jobboard.tsx`, `reserved.tsx`, `jobdetail.tsx`)

Applications (6b) copies it; so should any list.

```tsx
<div className="ax-page ax-page-fill">
  <TopActions><Button variant="primary" icon={<Plus size={15} aria-hidden="true" />}>Add job</Button></TopActions>
  <TopLine>Every job AXIOM has started or queued.</TopLine>
  <RouteTabs view="jobs" tabs={counted} label="Job lists" current={tab} />
  <div className="ax-toolbar"><SearchBox ... /><Listbox ... /><Chips ... /></div>
  <Table fill columns={columns} footer={<Pagination paging={paging} />} empty={<Empty icon={...} title="..." hint="..." action={...} />} ... />
</div>
```

- **Rows:** checkbox, JobCell (monogram, employer, role), Location (`hideBelow: 860`), Status (with the reason in a Tooltip) and Updated (both `hideBelow: 600`, and in JobCell's `meta` on a phone), then at most one action, plain words that gain a button's chrome when the row is pointed at (`className="ax-lead"`), and the "…" RowMenu. The whole row opens the drawer.
- **Views are named:** Started, Discovered, Reserved, each with a count, each saying what it is in one line. "Add job" is a button opening a small dialog (`AddJobDialog` in `jobintake.tsx`, mounted once in `main.tsx`; dispatch `OPEN_JOB_LINK`), never a permanent input.
- **The drawer** (`JobPanel`): head with a large monogram, employer, role; a meta line (location, platform, fit, Open posting). Then the **status block** (`StatusBlock`, an inset surface): lamp and word, what it means, why, and inside it the question to answer or the next action. Then what stopped it, Proof, History (a line of attempts; click to open one), Final page. Folds: Answers, Activity, This attempt in detail, Raw receipt, Technical details.

## 6b. Applications: the reference for a ledger (`src/ledger.tsx`, `archive.tsx`, `styles/applications.css`)

The proof that it works: every row says **when it was sent and what proved it**, and the receipt is one click away. It is Jobs' structure with these differences.

- **Header:** `TopLine` reads "3,060 confirmed · 72 today" (mono figures, each a quiet link: the first clears the filters, the second sets Sent to Today; "today" is `confirmedToday`, as on Overview) and, only when some exist, "7 unconfirmed" with a lit dot, opening that tab. Each tab has its own one-line meaning in `TopLine`. `TopActions` holds **Export CSV** (secondary: no primary here).
- **Tabs** are the three named views with counts: Confirmed, Unconfirmed, Archive. `LedgerView` polls the ledger and the archive once, so the counts show before a tab is opened, and hands each list its data.
- **Rows** (Confirmed): JobCell, **Proof** (icon and words from `evidenceLabel`, with a Tooltip saying what it means: Confirmation page, Employer email, both, Existing application, **No receipt on file**: confirmed with an empty receipt, because the applicant recorded it or the platform answered "created"), **Sent** (the day in words, the clock in figures; full time and age in the title), Platform (wide only), then two words that become buttons when the row is pointed at: **Receipt** (opens the drawer, which leads with Confirmed and its Proof; "Details" when there is no receipt) and **Posting** (a real link to the employer's page, in the system browser). Under 860px the Receipt word goes (the row is the receipt), under 600px the stacked JobCell carries proof and time.
- **Filters:** search, then Proof, Sent (any time, today, last 7, last 30 days) and Platform as Listboxes whose options carry counts under every other filter, so a choice shows exactly that many rows. "Sent" counts what AXIOM sent, so the employer's own "you already applied" notices fall away. Clear appears with any filter. Filters are `useDraft`, so they survive a tab switch; `route.q` opens the list searched. Search reads what the row shows: a job saved without a company or title is found by its link.
- **Export** is a link to `GET /api/successful-applications.csv` with the same filters (`evidence`, `ats`, `since`, `q`), served with `Content-Disposition: attachment`: the app saves it, in a tab or in the native window. The button says how many rows ("Export 103 as CSV") once filtered. The server applies the same filters and words (`jobpilot/api/routes/state.py`); a test keeps them equal.
- **Unconfirmed** is the server's `group=unconfirmed` list with the row action "Confirm as submitted" and bulk actions. **Archive** keeps the outcome, the reason, and a Log download link; "Archive earlier failures" asks first.

## 7. Pages still to redesign, on this standard

Their brief is in UI3_BRIEF.md. What each must do with the family above:

- **Needs you** (`needs.tsx`, `questions.tsx`, the `nq-` styles in `work.css`): an inbox. Group the same question across jobs ("Supervisor phone, 6 applications") with MonogramStack; list and answer pane, or better. `QuestionCard` already handles every kind of answer, including uploads through Dropzone; Enter to send and move on, "skip", "not applicable to me", progress, what answering unblocks. Questions that text can't answer look different and offer to drop the job.
- **Resumes** (`ResumesView` in `setup.tsx`, `features.tsx`, `editor.tsx`, `pdfpages.tsx`): lead with what is being sent and whether tailoring is on, in one sentence and one control (`application_resume_policy` in the snapshot). Guided path for setting it up. Do not turn tailoring on yourself. Library cards or rows with preview and download; the Dropzone is already wired for upload. Tailored versions open in a preview panel. Editor: a full-height workspace with a slim bar; it currently still shows the shell's page header (`main.view-editor`, `.ax-scroll.is-fixed`). Test every path.
- **Profile** (`profile.tsx`, `knowledge.tsx`, `qform.tsx`): section navigation within the page, a two-column grid for related fields, cards that open to edit, a sticky save bar only with changes (`ax-savebar`), progress cards.
- **Settings** (`connections.tsx`, `autonomy.tsx`, `mail.tsx`, `discovery.tsx`, `setup.tsx`): group by what people recognise; provider cards with status, masked key and Test; internals behind an Advanced fold. Fix the mailbox showing "connected" beside "Mailbox operation failed".
- **Sign-in, sign-up, reset, setup guide** (`account.tsx`, `onboarding.tsx`, `styles/onboarding.css`, `styles/setup.css`), **coming-soon** (`ComingSoon.tsx`), **error boundary** (`ErrorBoundary.tsx`), **404**: the same family; the first thing anyone sees.

When a page moves off `.ax-grid`, `.nq-*` and the like, delete what it leaves behind. Keep this file true.

## 8. Checking your work

- With a vite dev server from your worktree (`npx vite --host 127.0.0.1 --port <free>`, proxying `/api` to 8080) and `scratchpad/qa/live.py` (`QA_BASE`), which aborts non-GET requests. A new read-only endpoint isn't on production yet: fulfil it in the test browser from real SQL (`q.sh`).
- Check every page in both themes at 1440×900 and 390×844, and at 1024, 1280 and 1920.
- Test states by rewriting the real snapshot in the test browser: `autopilot_runtime.application_workers.assignments` for busy, `control.paused`, `autopilot_runtime.scheduler.degraded`, `autopilot_runtime.state`, an aborted snapshot poll for connection lost, empty `jobs`, `runs` and `counts` for a new account.
- `npx tsc -b`, `npm run build`, `grep -l frontend tests/*.py` through `pytest-locked.sh`, `npx knip`.
- Screenshots hold the owner's real data: scratch folder only, never the repo.
