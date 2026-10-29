# AXIOM dashboard design system (v1, 2026-10-29)

This system is modelled on the owner's BELLWETHER dashboard (`web/src/index.css`, `components/layout/SideNav.tsx`, `nav.ts` and `components/ui/*`).

The approach: everything recedes except the data. The look is near-black (or warm white) with hairline borders and small radii. One accent colour marks where you are, what to press and what is official. The owner's standing taste applies: no decorative eyebrows, no grids of stat tiles, no serif headings, no side rails of filler. Screens are focused and put the data first.

## 1. Themes

Every colour is a token defined once per theme on `:root[data-theme=…]`. Components never use a literal colour.

| Theme id | Mode | Ground | Accent (`--brand`) | Notes |
|---|---|---|---|---|
| `navy` (default) | dark | `#070b14` | `#5b8cff` | Deep navy-black, cool blue accent |
| `maroon` | light | `#f8f6f5` | `#8c1d40` | Warm white, maroon accent |
| `slate` | dark | `#0a0d10` | `#2dd4bf` | Graphite with a teal accent |
| `ivory` | light | `#faf8f3` | `#2f6f4e` | Ivory with a forest-green accent |
| `system` | follows the OS | | | Navy when dark, maroon when light |

Each theme defines the full set of tokens:
- **Surfaces:** `--ground`, `--surface`, `--surface-hover`, `--raised`, `--card`, `--field`, `--chip`
- **Lines:** `--line`, `--line-strong`
- **Text:** `--ink`, `--ink-2`, `--ink-3`
- **Accent:** `--brand`, `--brand-soft`, `--brand-ink`, `--accent-text`
- **Status:** `--ok`, `--ok-soft`, `--warn`, `--warn-soft`, `--bad`, `--bad-soft`, `--info`, `--info-soft`
- **Effects:** `--glow`, `--shadow`, `--scrim`

Status colours stay distinguishable from the accent. In the maroon theme, `--bad` is a clear red (`#d23c3c`), not maroon. Body text must meet WCAG AA contrast and secondary text 4.5:1.

The theme is saved in `localStorage` (key `axiom.theme`), wrapped in try/catch. It is applied to `document.documentElement.dataset.theme` before first paint, using a tiny inline script in `index.html`, so it never flashes.

## 2. Type and scale

- **Typeface:** Geist Variable (`@fontsource-variable/geist`) for the interface and Geist Mono for code and IDs. Use `font-variant-numeric: tabular-nums` everywhere. DM Sans is removed.
- **Sizes:**
  - `--text-micro` 11px
  - `--text-meta` 12px
  - `--text-ui` 13.5px (rows, cells, controls)
  - `--text-emphasis` 15px
  - `--text-display` 22px
  - `--text-title` 24px (a page title)
- **Radii:** 5, 8 and 12px. Spacing uses a 4px grid.
- **Motion:** `cubic-bezier(.2,.8,.2,1)`, 120–200 ms, honouring `prefers-reduced-motion`.

## 3. Shell (the scroll model is the fix for most scrolling bugs)

- `html, body, #root` are `height: 100%`, and `body { overflow: hidden }`. The document never scrolls.
- The layout is a grid of `[sidebar | main]`. Main is a column: a top bar (56px, `position: sticky`), then **one scroll region** (`overflow-y: auto`) holding the page.
- Wide tables scroll horizontally inside their own panel. Table heads are sticky inside the scroll region. No page creates a second vertical scroll region, except modals and drawers, which own their own.
- **Sidebar:** 240px open, 64px collapsed (icon rail, labels as tooltips). The collapsed state is remembered.
  - Top: brand mark, "AXIOM", collapse button.
  - Then a "Search ⌘K" button, which opens the command palette.
  - Then the nav sections (§4). The footer holds the theme menu, the worker and host status dot, and sign-out.
- **Mobile (< 900px):** the sidebar becomes a slide-over drawer opened from the top bar, and the top bar shows the page title.
- **Command palette (⌘K / Ctrl-K):** jump to any page, or search jobs by company or title (from the existing jobs API) to open that job's panel.

## 4. Navigation (sidebar)

Every group stays open. Each item maps to the existing route model (`view` + `tab` in `src/context.ts`). Keep every existing URL working.

**Unlabelled group (the daily work):**
- **Overview**: the current Batch view. Autopilot status and what is happening now.
- **Jobs**: the pipeline.
- **Needs you**: the questions waiting on the owner. A badge shows the count. This becomes its own page.
- **Discovered**: the job board.
- **Applications**: the ledger and archive.

**Setup:**
- Profile
- Knowledge (the questionnaires, §6)
- Resumes
- Accounts
- Email
- Discovery
- Connections
- System

**Coming soon:**
- **Axe**: AI interviewer
- **Network**: networking for users and affiliates
- **Emails**: email automation pipelines

Each coming-soon item has a small "Soon" pill. Clicking one opens a calm page: the name, one sentence on what it will do, and nothing else. They are never disabled-looking errors.

## 5. Components (shared, in `src/ui/`)

- **Panel:** a titled card with an optional actions slot.
- **Pill:** a status chip in neutral, brand, ok, warn, bad or info tone.
- **Tabs:** underline tabs that set the route `tab`.
- **Button:** primary (brand), secondary (line), ghost, danger, in sm and md sizes.
- **Field:** input, select and textarea, each with a label, hint and error.
- **Table:** a sticky head, one-line rows of 40px, row hover, selectable rows, a bulk-action bar, and empty and loading states.
- **Drawer:** the job detail opens as a right-side drawer over the page, with its own scroll.
- **Empty:** one line of what is missing, plus the one action that fixes it.
- **Toast.**

Pages use these primitives and the tokens, and nothing ad hoc. Class names for new components are prefixed `ax-`, so they never collide with legacy rules while pages migrate.

## 6. Onboarding and Knowledge

Onboarding is a step-by-step flow:
1. Account
2. Resume upload
3. Profile check (pre-filled from the resume)
4. Work authorization
5. Preferences (roles, locations, salary)
6. Connections (Gmail, model key)
7. Optional questionnaires

Questionnaires are voluntary modules on the Knowledge page. Each one shows a completion state, and every answer can be skipped:
- **Tax-credit (WOTC) screening:** the standard statutory questions. The default suggestion is "No" to each, and the owner confirms.
- **Supervisors and references:** per work-history row, the supervisor's name, title, phone and email, whether they may be contacted, and references.
- **Frequently asked:** generated from the questions applications most often stop on (production Needs-you statistics).

Answers are stored as knowledge facts and used by the resolver. Nothing in a questionnaire is required to start applying.

## 7. Quality bar

- **Every page:** loading, empty and error states, and keyboard focus visible.
- **Layout:** no layout shift on poll refresh, and no horizontal page scroll at 375px.
- **Theme check:** every page is checked in navy and maroon.
- **Copy:** plain and short, with sentence case.
