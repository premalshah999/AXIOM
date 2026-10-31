# AXIOM dashboard design system: The Instrument (2026-10-31)

A front panel. Lines are the machine: ruled, fixed, engraved. Dots are what it is doing: live, lit, countable. The owner's rule for all of it: not generic, not cluttered, and it must stay true with real data.

## 1. The rules

- **One panel, one page.** The channel bank, the readouts, the lit window and the round key live on Overview only. Every other page carries the identity through type, the plate, hairlines and lamps, and stays calm and legible.
- **Vermilion means the machine is live, or something needs you.** Never decoration. `--live` is vermilion only while real state says the machine runs (`data-run` on `<html>`, set in `main.tsx` from `identity/state.ts`, and off until the first answer).
- **State is never a new colour.** It is lamp behaviour plus words: running pulses, a question is ringed, repairing is half lit, no receipt is dashed, confirmed is solid ink, stopped is hollow.
- **Doto only at 56px and up.** It gets scratchy small. Under about 20px, data (time, count, platform) is Fragment Mono and sentences are Archivo.
- **Copy says what it does.** "Pause applying", not poetry. Engraved labels are lowercase by CSS; the text in the DOM stays sentence case, and real names (company, role) keep their capitals.
- No KPI tiles, no cards inside cards, no glassmorphism, no glow on surfaces, no pill on every row. A row's one action is a quiet link. At most one primary key on a screen, except Needs you's round send keys, one per question.
- A screen has one obvious thing to look at. Keep real data and guidance; remove noise. Explanatory paragraphs mostly go.
- Motion only where it means the machine is alive: CSS only, off under `prefers-reduced-motion`. No backdrop-filter, no blend modes over the page, no JS animation loops. The grain is one background tile.

## 2. Themes

Exactly two, on `<html data-theme>`: `dark` (graphite `#232321`, the default for a missing or unknown value) and `light` (bone aluminium `#E5E3DB`). They are named Dark and Light. The display window stays dark in both. The switch is on the rail, and "Switch to the … theme" is in the palette. The choice is in `localStorage` under `axiom.theme`, set before first paint by the inline script in `index.html`. Old stored values still work: `navy`, `slate` and `system` read as dark; `maroon` and `ivory` read as light. `readTheme` in `src/shell/prefs.ts` and that script must agree.

### Tokens (`src/styles/tokens.css`, the only file with colours and fonts)

| Group | Tokens |
|---|---|
| Plate | `--ground`, `--ground-2` (hover), `--ground-3` (a recess), `--raised` (overlays), `--field` (controls), `--tint`, `--plate` (an opaque piece of plate, with its grain) |
| Lines | `--line`, `--line-strong` (ticks, control edges: 3:1) |
| Text | `--ink`, `--ink-2`, `--ink-3`, `--accent-text` (links are ink and underlined) |
| Keys | `--key`, `--key-ink`, `--key-top`, `--key-bot`, `--key-down`; `--brand`, `--brand-ink` (the master key) |
| Window | `--window`, `--window-edge`, `--window-rule`, `--window-ink`, `--window-ink-2`, `--dot-off` |
| Lamp | `--led`, `--signal`, `--signal-ink`, `--signal-on-window`, `--bad`, `--live` |
| Material | `--shade`, `--shade-deep`, `--engrave`, `--grain`, `--overlay-shadow`, `--scrim`, `--paper`, `--paper-shadow` |
| Type | `--font-read` (Doto), `--font-text` (Archivo), `--font-data` (Fragment Mono), `--wide`, `--text-meta` 13, `--text-ui` and `--text-body` 14, `--text-sub` 16, `--text-title` 20, `--text-page` 30 |
| Layout | `--radius` 3, `--radius-sm` 2, `--space-1` to `--space-12` (4px grid), `--page-w`, `--form-w`, `--gutter`, `--rail`, `--inset`, `--bar-height`, `--soon` |

`tests/test_identity_contrast.py` measures every pair WCAG AA in both themes and fails on a literal colour outside `tokens.css`.

### Fonts

Bundled, Latin subsets only, 118 KB: Doto `full` (6 KB; has ROND and wght), Archivo `standard` (88 KB; has wdth and wght), Fragment Mono (25 KB). Those are the files with the axes the look depends on, measured in Chromium. Don't swap them for a package's default file, which lacks them.

## 3. Shell

- `html`, `body` and `#root` are the viewport and `body` clips: **the document never scrolls.** Screens outside the shell (sign-in, setup guide) wrap themselves in `.ax-screen`.
- `.ax-app` is a column of the top plate and main. Main is a column: page title, one-line banners, then **one scroll region** (`.ax-scroll`).
- **The top plate** is the rail (wordmark, theme switch) and the **tuning scale**: nine stations and a needle that moves to the current page. Each station reads something live under its name: Overview's state word, the job and confirmed counts, Needs you's count with its lamp, the owner's name. Search (`⌘K`) and the account menu sit in the corner the scale leaves free.
- Coming soon (Axe, Network, Emails) are the last three stations, under a "coming soon" band. Under 1340px they narrow to 0.72 of a page's station; under 1140px Axe drops its "ai interviewer" line.
- **Phones (under 900px):** the scale becomes a strip along the bottom with real names at full size, sliding under a fixed needle; tapping a station goes there and centres it. This replaces the old slide-over drawer. Search, the theme switch and the account are in the top bar.
- **The page title** is outside the scroll region, in engraved Archivo. Overview has none: its panel is its title. A page may add one or two things beside it with `<TopActions>`.
- Lists use the page width; Profile and Settings keep a form's width.

## 4. Overview: the panel

Rail plus nine columns, the same grid as the scale, so at nine workers each channel stands under a station. All of it is in `src/identity/` and `batch.tsx`.

- **Confirmed today:** a Doto readout, counted by `confirmedToday` from `GET /api/successful-applications` since local midnight. Applications' "today" reads the same function.
- **The sentence.** One, the worst true thing first, from `panelState`: connection lost, needs a restart, paused, target reached, running, held, no batch. Actions: `start` (with "Apply until"), `resume`, or `pause`, and "Needs a restart" adds a primary Restart automation beside Pause. Only lost and restart take the lamp colour. "Connection lost" shows here and not again in the banner.
- **The day:** `DayChart`, a matrix of half-hour columns, one lit dot per confirmation. A busy day goes to quarter hours, then says "each dot is N". Nothing is invented.
- **The lit window:** the count of questions waiting, in the lamp colour; dark glass at zero.
- **The round key:** `MasterKey`.
- **The channel bank:** `ChannelBank`, one channel per worker slot (1 to 30), from runtime-health's workers. Under 120px a channel it becomes a row of ladders over a list of the busy ones, which is also what a phone gets. A lit channel carries company, role, what it is doing, platform, time on the job and Pause; the company opens the job. Long names clamp with the full text in a title attribute. A channel goes dark only when its run really stops, in order, so pausing puts them out one at a time.

### Channels: the rung mapping (`identity/stages.ts`)

Six rungs, bottom to top. `running` alone only proves "open", so a channel reads the run's newest telling event from `GET /api/runs/{id}`, one run at a time, every few seconds, while the page is visible.

| Rung | Run events (`events.kind`) |
|---|---|
| open | `browser`, `new_tab` |
| sign in | `auth`, `account`, `captcha`, `email_wait`, `email_verified` |
| fill | `step`, `form_page`, `form_post`, `field_error`, `agent_step`, `agent_action_result`, `recovery`, `browser_recovery` |
| questions | `answer_quality`, `answer_validation`, `knowledge_inference`, `knowledge_inference_declined` |
| review | `verification` |
| submit | `submitting`, `submission_requests`, or run state `submitting` |

## 5. Navigation

| Station | `view` | Tabs (`tab`) |
|---|---|---|
| Overview | `batch` | |
| Jobs | `jobs` | `pipeline`, `discovered`, `reserved` |
| Needs you | `needs` | |
| Applications | `ledger` | `confirmed`, `unconfirmed`, `archive` |
| Profile | `profile` | `identity`, `experience`, `knowledge`, `resumes` |
| Settings | `settings` | `connections`, `accounts`, `email`, `discovery`, `system` |
| *Coming soon:* Axe, Network, Emails | `soon-axe`, `soon-network`, `soon-emails` | |

`src/shell/nav.ts` lists the items and tabs. The resume editor (`editor`) belongs to Profile.

**Every URL the dashboard has ever had keeps working.** `src/context.ts` holds the one table: `canonical()` turns any old `view`, `tab` and `filter` into its new home, `parseRoute()` applies it to the address, `go()` and `routeUrl()` apply it to code, and `main.tsx` rewrites the address bar. Add a new old name there, nowhere else.

| Old | New home |
|---|---|
| `?view=setup` (no or unknown tab), `/setup`, `?view=connections`, `?view=setup&tab=storage` | Settings › Connections |
| `?view=setup&tab=accounts\|email\|discovery\|system\|connections`, `?view=accounts\|email\|discovery\|system`, `?view=sources`, `?gmail=` | that tab of Settings (`sources` is Discovery; `gmail` is Email) |
| `?view=setup&tab=profile`, `&filter=identity` | Profile › Identity |
| `&tab=profile&filter=experience` | Profile › Experience |
| `&tab=profile&filter=preferences` | Profile › Experience, Preferences section |
| `&tab=profile&filter=facts\|policies\|rules` | Profile › Knowledge, that section |
| `?view=setup&tab=answers`, `?view=knowledge`, `?view=answers`, `?view=knowledge base` | Profile › Knowledge |
| `?view=setup&tab=resumes`, `?view=resumes` | Profile › Resumes |
| `?view=jobs&tab=archive`, `?view=archive` | Applications › Archive |
| `?view=successful`, `?view=confirmed`, `?view=applications` | Applications › Confirmed |
| `?view=unconfirmed` | Applications › Unconfirmed |
| `?view=discovered`, `?view=job board`, `?page=job-board`, `?view=jobs&filter=new` | Jobs › Discovered |
| `?view=pipeline`, `?view=reserved` | that tab of Jobs |
| `?view=overview`, anything unknown | Overview |
| `?view=needs help`, `?view=needs you`, `?view=questions` | Needs you |

## 6. Primitives (`src/ui/`, `src/worktools.tsx`, `src/setup-kit.tsx`)

Build pages from these and they look right without new CSS. API details are in `src/ui/README.md`.

- **Button:** a key, lit from above, on its edge; it presses down. Primary is the dark master key; a disabled one stands down. `round` is the round commit key for one short word. Ghost has no key; danger is lamp-coloured text.
- **Tabs and Segmented:** a slide switch cut into the plate. **Filters (`Chips`):** a row of keys in one recess, with counts in mono; the one that is down carries a thin line of `--live`.
- **Pill / StatusPill:** a lamp and a word. Text is ink unless it needs you.
- **Panel:** a titled section on a hairline. Not a card.
- **Field:** a label over a recess with a real edge. On a page, `FormGrid` turns fields into labelled rows divided by hairlines; in a drawer they stack.
- **Table:** no outer card, hairline rules, 40px rows. Company and role read as two columns inside `JobCell`, each clamped with its full text in a title. **Row checkboxes are always visible** (the owner's decision). At most one lead action per row, shown as a quiet link.
- **Drawer, menu, palette:** the only raised things. **Toast:** one line in the display window. **Empty:** one line; its action reads as a link. **Notice:** one plain line, with a lamp only for what needs you.

Every control has a visible focus ring in both themes.

## 7. Pages

- **Jobs and Applications:** tabs, filter keys and a table. No readouts.
- **Needs you:** one sentence on what the questions hold back, then rows of who is asking and the question, with short choices as keys and a round send key.
- **Job drawer:** title with real capitals, status sentence, the one action, then history. No nested panels.
- **Settings and Profile:** one column of labelled rows on hairlines. Secrets stay masked.
- **Setup guide:** one question group per step and a thin progress line.
- **Coming soon pages:** the name and one sentence.

## 8. Quality bar

- Every page keeps its loading, empty and error states. Every keyboard path keeps working.
- No layout shift on poll refresh; no document scroll; no horizontal page scroll at 390px.
- Check every page in both themes at 1440×900 and 390×844, and the scale at 1024, 1280 and 1920.
- `npx knip` lists only the three font packages, which are used through `url()` in `tokens.css`.
