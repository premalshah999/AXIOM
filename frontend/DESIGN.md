# AXIOM dashboard design system (2026-10-31)

Read this before touching a page. It holds the **family** every page is made from (sections 1 to 4) and one section per page, each the reference for its kind of page: Overview (5, several parts), Jobs (6, a list), Applications (7, a ledger), Needs you (8, an inbox), Resumes and its editor (9, a state and a file viewer), Profile (10, a page of sections), Settings (11, a settings page) and the first-run screens (12). Every page is redesigned; copy the one that is closest to what you are building.

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
- **Scrolling:** a page that is one table uses `<div className="ax-page ax-page-fill">` and `<Table fill footer={<Pagination/>}>`: the table is the only scroller and its head stays put. Any other page is `<div className="ax-page">` and scrolls in `.ax-scroll`. Widths: `.ax-main` sets `--content-w`; Settings uses `--form-w`, Profile widens it (`profile.css`) to make room for its section list.

### Routes

`src/context.ts` holds the one table: `canonical()` turns any old `view`, `tab` and `filter` into its home, `parseRoute()` applies it to the address, `go()` and `routeUrl()` apply it to code, and `main.tsx` rewrites the address bar. Add a new old name there, nowhere else. `route.q` carries a list's search in the URL, so a number elsewhere can open the list already searched.

| Page | `view` | Tabs (`tab`) |
|---|---|---|
| Overview | `batch` | |
| Jobs | `jobs` | `pipeline` (labelled Started), `discovered`, `reserved` |
| Needs you | `needs` | |
| Applications | `ledger` | `confirmed`, `unconfirmed`, `archive` |
| Resumes | `resumes` (`&artifact=<id>` opens that tailored version's preview; the editor, `editor`, belongs to it: `&artifact=<id>`, `&artifact=master` for your copy, neither for a new one) | |
| Profile | `profile` | `identity`, `experience`, `knowledge`, narrowed by `filter` to a section (section 10) |
| Settings | `settings` | `connections`, `email` (Mailbox), `discovery` (Job search), `applying`, `accounts` (Employer accounts), `account` (Your account), `database`, `system` (Advanced) |
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

Applications (section 7) copies it; so should any list.

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

## 7. Applications: the reference for a ledger (`src/ledger.tsx`, `archive.tsx`, `styles/applications.css`)

The proof that it works: every row says **when it was sent and what proved it**, and the receipt is one click away. It is Jobs' structure with these differences.

- **Header:** `TopLine` reads "3,060 confirmed · 72 today" (mono figures, each a quiet link: the first clears the filters, the second sets Sent to Today; "today" is `confirmedToday`, as on Overview) and, only when some exist, "7 unconfirmed" with a lit dot, opening that tab. Each tab has its own one-line meaning in `TopLine`. `TopActions` holds **Export CSV** (secondary: no primary here).
- **Tabs** are the three named views with counts: Confirmed, Unconfirmed, Archive. `LedgerView` polls the ledger and the archive once, so the counts show before a tab is opened, and hands each list its data.
- **Rows** (Confirmed): JobCell, **Proof** (icon and words from `evidenceLabel`, with a Tooltip saying what it means: Confirmation page, Employer email, both, Existing application, **No receipt on file**: confirmed with an empty receipt, because the applicant recorded it or the platform answered "created"), **Sent** (the day in words, the clock in figures; full time and age in the title), Platform (wide only), then two words that become buttons when the row is pointed at: **Receipt** (opens the drawer, which leads with Confirmed and its Proof; "Details" when there is no receipt) and **Posting** (a real link to the employer's page, in the system browser). Under 860px the Receipt word goes (the row is the receipt), under 600px the stacked JobCell carries proof and time.
- **Filters:** search, then Proof, Sent (any time, today, last 7, last 30 days) and Platform as Listboxes whose options carry counts under every other filter, so a choice shows exactly that many rows. "Sent" counts what AXIOM sent, so the employer's own "you already applied" notices fall away. Clear appears with any filter. Filters are `useDraft`, so they survive a tab switch; `route.q` opens the list searched. Search reads what the row shows: a job saved without a company or title is found by its link.
- **Export** is a link to `GET /api/successful-applications.csv` with the same filters (`evidence`, `ats`, `since`, `q`), served with `Content-Disposition: attachment`: the app saves it, in a tab or in the native window. The button says how many rows ("Export 103 as CSV") once filtered. The server applies the same filters and words (`jobpilot/api/routes/state.py`); a test keeps them equal.
- **Unconfirmed** is the server's `group=unconfirmed` list with the row action "Confirm as submitted" and bulk actions. **Archive** keeps the outcome, the reason, and a Log download link; "Archive earlier failures" asks first.

## 8. Needs you: an inbox (`src/needs.tsx`, `questions.tsx`, `styles/needs.css`, answer controls in `work.css`)

A list at the left, one answer pane at the right (one column on a phone: the list, then the pane with a back arrow). `useInbox` builds the entries; the list leads with what frees the most applications, then by how long it has waited.

- **Entries are not rows of the database.** The same question on several jobs is one entry (`groupQuestions`: the field's wording and options decide, never the key), so one answer reaches every job asking (`deliver` posts it to each; the server answers twins itself, so a later POST that finds its row answered, HTTP 400 "already answered", counts as sent). Supervisor and reference questions across jobs are one entry, **Supervisors and references**, with a card per past job (`claimOf`: it reads the same labels the engine does, `answers/people_rules.py`). Saving a card writes the questionnaire and answers every waiting box it settles.
- **What no answer completes looks different.** The server's marker is typed on `Review`: `cannot_automate` is a plain reason, and `kind: "cannot_automate"` (with `input_kind` keeping the answer's shape) means nothing the owner can type or send fits, such as a video of themselves. `blockOf` reads it; the list puts these under "Need something only you can make", the pane gives the reason, **Drop this job** (asks first) and, for a piece of the owner's own work, a Dropzone. Do not guess from the question's words in the browser.
- **The pane** names who is asking (monograms, "and 3 others"), the question large, then the control that fits (buttons for a few short choices, a Dropzone for a file, a date, a text box), **Send to N applications**, Skip, **Not applicable to me**, the shortcut hint (Enter sends and moves to the next, ⌥S skips), and under it what answering frees or helps ("Frees 4 applications", each with "Continues"; "1 more question after this").
- **A stack of employers** in the list shows one monogram and a "+N" chip when there are three or more, two when there are two: the first column is 44px.
- **An application stopped on the owner with no question** is "Nothing to answer here": Try again, Skip, Open job. Not dressed as a question.
- **A job saved as a link has no name yet.** `jobWords` (`format.ts`) is the one place that reads it: the employer from the address ("Helixbio"), the role as "Role not read yet", the address's host as a muted hint after it. Use it for any list of jobs; never print "Untitled role" or a bare host as the name.

## 9. Resumes: a page that says a state and owns a file viewer (`src/resumes.tsx`, `editor.tsx`, `pdfpages.tsx`, `styles/resumes.css`, `editor.css`, `pdf.css`)

**PDFs stay in the app.** It runs in a native window, where a link that opens a new tab goes nowhere, and the server answers "attachment". `PdfPages` (`fit`: `page` or `width`, with zoom), `PdfThumb` (a real first page, small), `usePdf(url)` (one read per session), `downloadPdf` and `savePdf` (a real file name; the server calls them all resume.pdf). Never `<a target="_blank">` to a PDF.

1. **What employers get** (`Sending`): a picture of the file, one sentence, one control. Off: "Employers get X, exactly as you uploaded it", Set up tailoring. On: a Switch that turns it off at once. Read `policy.resume_id`: that's what the queue sends.
2. **The guided path** (`SetupPath`): import, compare, approve, turn on, one current step with the one primary. Employers get the PDF until the last step. Turning on (`PUT /api/v2/resumes/policy`, refused without an approved master) always goes through a Dialog saying what changes, and is the owner's click: no agent, test or default turns it on.
3. **Two columns.** Your resumes (rows with the file's first page, Download, a menu; the resume in use can't be removed, because the queue would have nothing to send; the Dropzone opens a Dialog for its name). Tailored versions: a Table of job, how many bullets were reworded, and what happened: **Sent Oct 7, Maybe sent, Never sent** come from the runs that pinned that version (`sent`), so never say a draft "was never used" from the policy alone.
4. **The preview panel** (`VersionPreview`, a Drawer): what happened to this version first, then a fold of what it reworded, then the PDF filling the rest. ← → and the footer step through the list; the address follows.

**The editor** hides the shell's header and takes the region: a slim bar (back, whose document this is, build status, Discard when changed, Download, the one primary), the form at the left, the page on an inset well at the right. Entries are rows that open; bullets grow with their text; reordering and hiding appear on pointing (always, on touch). Source shows what the controls produce now, in full. Your copy opens with the two documents side by side and the form closed. Under 960px the form and the page take turns. Each address is its own load (`EditorLoader` is keyed by it): never reuse the last answer for a new address.

## 10. Profile: the reference for a page of sections (`profile.tsx`, `profile-work.tsx`, `profile-rules.tsx`, `profile-photo.tsx`, `profile-fields.tsx`, `knowledge.tsx`, `qform.tsx`, `styles/profile.css`)

The page is a section list at the left and **one section at a time** at the right (a scrolling row of sections under the header below 1180px), over **one draft and one Save**.

- **Sections and their addresses.** `SECTIONS` in `profile.tsx` holds them; each keeps an old address alive through `tab` (the old tab it grew out of) and `filter` (the old section name, or a new one): Personal details (`identity`), Work and education (`experience`), Job preferences (`experience` + `preferences`), Supervisors and references (`knowledge` + `references`), Questionnaires (`knowledge`), What AXIOM may do for you (`knowledge` + `policies`), When options don't match (`knowledge` + `rules`), Saved facts and memory (`knowledge` + `memory`; the old `facts` opens its JSON tab). Add a section there, not in `nav.ts`.
- **What the list shows beside a name** is real or nothing: a vermilion count for applications actually waiting (Supervisors and references: open questions that ask for a supervisor or a reference, counted once per application from the live snapshot), a count of basic details still empty, a quiet tally (`26/37` answered, `7/9` permissions on), a dot where the draft has an unsaved change.
- **One draft** (`ProfileProvider`, kept in memory across sections by `useDraft`). `dirty` means the draft really differs from what is saved (keys compared in sorted order, so an edit undone is not a change). The **save bar** (`pf-savebar`) exists only then, says which sections hold changes, and saves on Ctrl or ⌘ S. Questionnaires (supervisors, references, FAQ) save on their own as you go and say so.
- **Fields** sit on a 6-column grid inside one Panel per section, in titled `Group`s: give a field `pf-s2` (a third), `pf-s3` (a half) or nothing (the row); everything is one column on a phone. First, middle and last name share a line. `Tags` (type and press Enter or a comma) replaces a comma-separated input, `Range` a number on a slider, `Pick` a choice in a form (a Listbox filled to the field's width).
- **Experience, education and notes are cards** (`pf-card`: monogram, title, employer, dates, two lines of what you did). A card opens in a **Drawer** to edit, with Remove asking first. A card added and left empty is taken back on close.
- **Supervisors and references** is where applications are waiting. The section leads with how many (the employers' monograms, "See them" opening Needs you), says AXIOM never invents these, and shows two meters. Below, one row per job in the work history that opens in place (`PersonCard` in `qform.tsx`; the first opens by itself while none is added), and up to three references. `ModuleForm` takes `bare` when the host supplies the title: only a floating save pill remains.
- **Questionnaires are progress cards**: state, what it is for, which sections are still open, a meter, one button (Start, Continue, Review).
- **Permissions** are plain sentences with a switch, grouped (Applying, Agreements and consents, Your privacy), with the count that are on.

## 11. Settings: the reference for a settings page (`src/settings.tsx` and the files it mounts)

One page, a list of sections beside the one that is open (`.st-nav`, real links; on a phone one row that scrolls sideways and keeps the open one in view). The section is the route's `tab`; the list is `PAGE_TABS.settings` in `shell/nav.ts`. Grouped by what a person recognises, the engine's own switches last:

| Section (`tab`) | File | What it is |
|---|---|---|
| Connections (`connections`) | `connections.tsx` | **Cards**, not input rows: one per AI model (one is in use, "Use this one" picks it) and per service (Gmail, Telegram, 2Captcha, Serper, TypeSafe, employer sign-in). A card says what it does, whether it is set and in use, shows the key **masked by the server** (never read back; Replace opens a field that holds only what is typed), and has Test. One sticky save bar for all of it. Advanced fold: OpenRouter hosting, a screenshot model, and any field the server adds that no card claims |
| Mailbox (`email`) | `mail.tsx` | The mailbox's true state, trusted senders (a table; Add a rule is a Dialog), recent verifications. Connect Gmail is a Dialog |
| Job search (`discovery`) | `discovery.tsx` | What to look for (switches, `Segmented` choices), employer boards (table, Add a board Dialog, Failing chip), Custom sources in a fold |
| Applying (`applying`) | `system.tsx`, `discovery.tsx` | Send applications and Pause (apply at once), automatic applications (fit, batch size), Limits (daily, per employer, per job: two endpoints, one form, one save bar) |
| Employer accounts (`accounts`) | `accounts.tsx` | A table with **what needs you first** (`Chips` filter with counts, "Set password" or "Retry verification" per row, the rest in the row menu); how AXIOM signs in and Import a session are folds |
| Your account (`account`) | `account.tsx` (`SignInSettings`) | Password, recovery codes, sign out, setup guide, as rows in one panel |
| Database (`database`) | `settings.tsx` | **A slot.** `DatabaseSection` has the heading and `DatabasePending` for a body. `src/database.tsx` (agent8/db-config) mounts by being imported in `settings.tsx` and rendered in place of `<DatabasePending />`; nothing else changes |
| Advanced (`system`) | `system.tsx` | Status in plain words, Workers (read at start, so a restart notice), what AXIOM may do on its own, application platforms |

Rules this page set:

- **A status is true or it is not shown.** `mailHealth` (`mail.tsx`) decides what a mailbox says: "Connected" only when no failure is newer than the last success. The server keeps `state` (login accepted) apart from `error` (the last request's failure), so connected with an error is "Trouble reaching Gmail" with the cause in words and "Last worked 5m ago". It reads `last_ok_at`, `last_error_at` and `retrying` when the server sends them (agent8/fix-mail) and falls back to `error` and `checked_at` when it does not. A refused login is "Needs reconnecting".
- **One form over `/api/connections`, several places.** `useConnections` is the controller (draft, masked values, test, save); the cards, the Limits and the Workers each use their own copy and their own save bar. `FieldInput` edits one non-secret field by its type; `SecretField` replaces one secret.
- **Plain words for engine terms.** "Model for answers", "Model for moving through pages", "Workers: 6 regular, 2 for difficult sites", "Version c11630cac88b" (never `execution-v2-…`).
- **Folds for internals** are `.st-adv` (a surface of its own, closed by default, with a plain line saying what is inside).
- Styles are in `styles/settings.css` (`st-` prefix): `.st-card` (a service), `.st-key` (a masked key), `.st-state` (a line that says what is true, with a lamp), `.st-rows` (name left, control right), `.st-fields`, `.st-sub` (a column's value shown under the first column when the table is narrow).

## 12. First run: the setup guide, sign-in, coming soon, a crashed page

These are the first things anyone sees, so they are one family: a raised card on the page (`--raised`, hairline, `--radius-lg`), the wordmark above or beside it, a title and one line, then the task. Outside the shell, inside `.ax-screen`.

- **The setup guide** (`onboarding.tsx`, `styles/onboarding.css`): a welcome, seven short steps, a finish. In order: **Data** (where the data lives), **Resume**, **Contact**, **Eligibility** (work authorization, with Background questions and Self-identification as folds most people leave closed), **Search** (roles, places), **Automation** (send for me or review first; switches), **Connections** (AI key and inbox, both skippable). The progress line has one named segment per step (a reached step is a way back), "Step 3 of 7" and "About 4 min left" (the sum of `minutes` in `STEPS`). The way on lives in a sticky bar (Back, an optional skip, one primary); Enter in a field continues. The usual answer is already chosen where one exists; nothing is defaulted for the person (`test_wizard_background_facts.py`): "Answer No to the rest" fills unanswered background questions only when pressed. The finish says "AXIOM is ready" only when nothing AXIOM needs is missing, otherwise "Almost ready" with a Fix link per line; questionnaires moved to Profile › Knowledge and are offered from the finish. Server step ids are unchanged (`server` in `STEPS`); `OPEN_AT` maps a stored step back to a guide step.
- **The Data step is a slot.** `DatabaseStep` shows Local as chosen and nothing to fill in. The Local, Supabase and Server component from `agent8/db-config` (`frontend/src/database.tsx`) replaces its body; keep Continue enabled while Local is selected.
- **Sign-in, sign-up, recovery codes, reset** (`account.tsx`, `styles/auth.css`): one `AuthCard` (wordmark above, quiet links to the other ways in under it); a password has its own Show and Hide inside the field; the rules a new password must meet tick off as they are met; recovery codes sit in a numbered well with Copy and Download. They appear only for someone who set a lock. Settings › Your account is in the same file.
- **Coming soon** (`shell/ComingSoon.tsx`, `styles/screens.css`): plainly not switched on (a dashed lamp and "Coming soon"), one sentence, three lines of what it will do, a picture of the idea drawn in tokens, and a way back. Add a page by adding to `nav.ts` and to `WILL` in the file.
- **A crashed page** (`shell/ErrorBoundary.tsx`): what happened, that the rest still works, Try again (clears the error) and Reload, the message behind a fold.
- **No 404 page.** Any unknown address opens Overview (§3), so there is nothing to show.

## 13. Checking your work

- With a vite dev server from your worktree (`npx vite --host 127.0.0.1 --port <free>`, proxying `/api` to 8080) and `scratchpad/qa/live.py` (`QA_BASE`), which aborts non-GET requests. A new read-only endpoint isn't on production yet: fulfil it in the test browser from real SQL (`q.sh`).
- Check every page in both themes at 1440×900 and 390×844, and at 1024, 1280 and 1920.
- Test states by rewriting the real snapshot in the test browser: `autopilot_runtime.application_workers.assignments` for busy, `control.paused`, `autopilot_runtime.scheduler.degraded`, `autopilot_runtime.state`, an aborted snapshot poll for connection lost, empty `jobs`, `runs` and `counts` for a new account.
- **Writes go to a scratch instance, never to production.** Run the app from your worktree under `env -i` with `PYTHONPATH=<worktree>` (the shared venv's editable install points at another worktree), `DATA_DIR` and a sqlite `DATABASE_URL` in your scratch folder, your own `APP_TOKEN`, `ENABLE_WORKERS=false`, a free port with a matching `BASE_URL`, blank mail and Telegram settings, and every provider `*_BASE_URL` pointed at a stand-in on your own port (`scripts/demo_instance.sh` is the short form). With workers off the Overview hides Start and Pause: rewrite `config.workers_enabled` in the snapshot in the test browser, and the click still reaches the real API.
- `npx tsc -b`, `npm run build`, `grep -l frontend tests/*.py` through `pytest-locked.sh`, `npx knip`.
- Screenshots hold the owner's real data: scratch folder only, never the repo.
