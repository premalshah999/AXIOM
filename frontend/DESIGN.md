# AXIOM dashboard design system (v2, 2026-10-30)

Quiet and sparse, in the manner of Linear and Stripe's dashboard. Two themes.

The test for every element: does the person need this right now to decide or act? If not, remove it, or put it one click away. When in doubt, delete. A screen has one obvious thing to look at and at most one primary button.

## 1. What is not allowed

- A bordered rounded card around anything; cards inside cards.
- Coloured pills on rows; a tinted background on anything that isn't a real problem.
- Glow, gradients, drop shadows, coloured left-border callouts, pulsing dots.
- An explanatory paragraph at the top of a page or panel. Hint text under a field, unless a person would otherwise get it wrong.
- Giant display numbers, and progress bars used as decoration.
- Grids of stat tiles, decorative eyebrows, serif headings, uppercase labels, filler side rails.
- More than one primary button on a screen, or a primary button in the top bar.
- Any size or weight outside §3. Any colour outside `tokens.css`.

## 2. Themes

Exactly two, set on `<html data-theme>`. A missing or unknown value is dark.

| Theme | Ground | Text | Accent |
|---|---|---|---|
| `dark` (default) | deep navy `#0b1220` | near-white | restrained blue |
| `light` | warm white `#fbf9f7` | near-black | maroon `#8c1d40` |

The toggle is the sun or moon item in the account menu (and "Switch to … theme" in the palette). The choice is saved in `localStorage` under `axiom.theme` and applied before first paint by the inline script in `index.html`. Old stored values still work: `navy`, `slate` and `system` read as dark; `maroon` and `ivory` read as light. `readTheme` in `src/shell/prefs.ts` and the inline script must agree.

**The accent appears only on:** the primary button, the current sidebar item, links, focus rings. Everything else is neutral: tabs, filters, switches, checkboxes and the brand mark use the text colours.

**Status colour** is only for a small dot or the text of something that needs attention: needs an answer (`--warn`), failed (`--bad`). Success and neutral states are plain muted text. `--bad` is a clear red, never to be read as the maroon accent.

Contrast is WCAG AA in both themes on both surfaces, including `--ink-3` at 12px.

### Tokens (`src/styles/tokens.css`)

| Group | Tokens |
|---|---|
| Surfaces | `--ground` (page and sidebar), `--raised` (overlays only), `--field` (controls), `--tint` (hover, current, selected) |
| Lines | `--line`, `--line-strong` |
| Text | `--ink`, `--ink-2`, `--ink-3` |
| Accent | `--brand` (button fill), `--brand-ink`, `--accent-text` (links, current item), `--focus` |
| Status | `--warn`, `--bad` |
| Overlays | `--overlay-shadow`, `--scrim` |
| Scale | `--text-meta`, `--text-ui`, `--text-body`, `--text-title`, `--radius`, `--space-1` to `--space-12` |
| Layout | `--page-w`, `--gutter`, `--side-w`, `--side-w-collapsed`, `--top-h` |

The block at the end of that file maps every older name (`--card`, `--surface`, `--ok`, `--brand-soft`, `--radius-lg`, `--text-micro`...) onto these, so unconverted pages go flat and neutral on their own. Never use those names in new code. Delete a line once nothing reads it.

## 3. Type and space

- **Geist only** (Geist Mono for IDs and code). Tabular numbers everywhere.
- **Four sizes:** 12 meta, 13 tables and controls, 14 body, 20 page title.
- **Two weights:** 400 and 500. The font faces in `tokens.css` declare that range, so a stray `font-weight: 700` renders at 500.
- **No uppercase labels.** Sentence case everywhere.
- **4px grid. One radius (6px)** for controls and overlays. Tables and sections have no outer card, only hairline rules.
- **No shadows** except drawers, menus, toasts and the palette.
- **Motion:** 140ms ease, honouring `prefers-reduced-motion`. Nothing pulses or shimmers.

## 4. Shell

- `html`, `body` and `#root` are the viewport and `body` clips: **the document never scrolls.** Screens outside the shell (sign-in, setup guide) wrap themselves in `.ax-screen`.
- `.ax-app` is a grid of `[sidebar | main]`. Main is a column: the top bar, one-line banners, then **one scroll region** (`.ax-scroll`) holding the page.
- `.main` is the page frame: about 1080px of content, left-aligned in the main area, with a 32px gutter (16px under 900px). There is no horizontal page scroll at 390px.
- **Top bar:** the page title in the title size, flush with the page's left edge. No border and no controls of its own. A page may put one or two things at its right with `<TopActions>` (in a row under the title on a phone).
- **Sidebar:** 220px, on the same ground as the page, one hairline. It collapses to a 56px icon rail (the control shows while the sidebar is hovered; the choice is remembered). Under 900px it is a slide-over drawer opened from the top bar, and search moves to an icon in the bar.
- **Search** is the small `⌘K` at the top of the sidebar, opening the palette. The palette jumps to any page or tab, finds jobs by company or title, adds a job link and switches theme.
- **Footer:** only the account row (avatar and name). It opens a small menu: theme toggle, sign out. Worker and host status live on Overview and Settings › System.

## 5. Navigation

Six items with no group labels, then a quiet "Coming soon" label over muted text, with no pills or badges. Needs you shows a small plain count.

| Sidebar | `view` | Tabs (`tab`) |
|---|---|---|
| Overview | `batch` | |
| Jobs | `jobs` | `pipeline`, `discovered`, `reserved` |
| Needs you | `needs` | |
| Applications | `ledger` | `confirmed`, `unconfirmed`, `archive` |
| Profile | `profile` | `identity`, `experience`, `knowledge`, `resumes` |
| Settings | `settings` | `connections`, `storage`, `accounts`, `email`, `discovery`, `system` |
| *Coming soon:* Axe, Network, Emails | `soon-axe`, `soon-network`, `soon-emails` | |

`src/shell/nav.ts` lists the items and tabs. The resume editor (`editor`) belongs to Profile.

**Every URL the dashboard has ever had keeps working.** `src/context.ts` holds the one table: `canonical()` turns any old `view`, `tab` and `filter` into its new home, `parseRoute()` applies it to the address, `go()` and `routeUrl()` apply it to code, and `main.tsx` rewrites the address bar. Add a new old name there, nowhere else. Write new links with the new names.

| Old | New home |
|---|---|
| `?view=setup` (no or unknown tab), `/setup`, `?view=connections` | Settings › Connections |
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

- **Panel:** a plain titled section on a hairline. Not a card.
- **Pill / StatusPill:** muted text. A small dot and full-strength text only for `warn` and `bad`.
- **Tabs:** text on a hairline, the first flush with the page.
- **Filters (`Chips`):** one quiet row of text with counts.
- **Button:** one primary look, used at most once a screen; a disabled primary goes neutral. Secondary is a hairline; ghost has none; danger is red text.
- **Field:** a label over a control, with a hint only when needed. On a page, `FormGrid` turns fields into one column of labelled rows divided by hairlines.
- **SaveBar:** a plain line at the foot of the region, shown only when something changed.
- **Table:** no outer card, hairline row rules, one-line 40px rows, a row's checkbox shows on hover. Columns: company and role, status text, time, one row menu. At most one lead action per row, and only when action is needed. Two lines per row under 600px, so both company and role can be read.
- **Drawer, menu, palette, toast:** the only raised things, and the only things with a shadow.
- **Empty:** one line; its action reads as a link.
- **Notice:** one plain line with an action.

Status text is never the only signal of the action available, and every control has a visible focus ring.

## 7. Pages

- **Overview:** one sentence of state ("Applying. 4 of 9 workers busy, 37 confirmed today."), one primary button (start or stop), one line linking to questions if any wait, the working workers only if any, then a plain recent-activity table.
- **Jobs and Applications:** plain tables, as above. Adding a job link lives on Jobs › Pipeline and in the palette.
- **Needs you:** a plain list of questions with the answer control inline; company and role muted above. "Why you're asked" only when expanded.
- **Job drawer:** title, status sentence, the one action, then history. No nested panels.
- **Settings and Profile:** one column of labelled rows divided by hairlines; section titles, no cards. Secrets stay masked.
- **Setup guide:** one question group per step, a thin progress line, no rail, no marketing copy.
- **Coming soon pages:** the name and one sentence.

## 8. Copy

Short, plain, sentence case. Cut every sentence that explains what the page is. No "Here you can...". No jargon a new user would not know ("draft", "run", "pool", "lane", "specialist").

## 9. Quality bar

- Every page keeps its loading, empty and error states, and they stay quiet. Every keyboard path keeps working.
- No layout shift on poll refresh; no document scroll; no horizontal page scroll at 390px.
- Check every page in both themes, at 1440×900 and 390×844.
