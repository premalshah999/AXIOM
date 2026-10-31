# src/ui: shared primitives

Tokens only, no literal colours. CSS lives in `src/styles/components.css`. A page built from these looks right without writing CSS (see `frontend/DESIGN.md`).

Import from the barrel, spelled `./ui/index` (a bare `./ui` resolves to the legacy `src/ui.tsx` beside this folder):

```ts
import { Button, Panel, Pill, Table } from "./ui/index";
```

## Page scaffolding (CSS, no component)

- `<div className="ax-page">` is a page inside the shell: a column with 32px rhythm. Width and gutters come from the frame. Put no title in the page: the top bar owns it.
- `<div className="ax-page ax-page-fill">` is one table or list filling the region; pair with `<Table fill />` so the table is the page's only vertical scroller and its head stays sticky.
- `<div className="ax-toolbar">` is a wrapping row of search and filters; `ax-toolbar-end` pushes controls to the right.
- Beside the page title: `import { TopActions } from "./shell/Shell"` then `<TopActions><Button>Export</Button></TopActions>`. One or two things at most, never a primary on every page.

## Panel
`{ title?, actions?, children, flush?, className?, id? }` A plain titled section on a hairline. `actions` sits right of the title. `flush` is for a table inside, which brings its own rules.

## Pill
`{ tone?: "neutral" | "brand" | "ok" | "warn" | "bad" | "info" | "fix", children, title?, dot? }` A lamp and a word. Each tone is a lamp behaviour in `components.css`; only `warn` and `bad` colour the text.

## Tabs
- `RouteTabs { view, tabs: {id, label, count?}[], label, current? }` A slide switch whose positions are real links and set the route `tab` for `view` (`go({ view, tab })`). Unknown `route.tab` falls back to the first tab. Profile's and Settings' lists are `PAGE_TABS` in `src/shell/nav.ts`.
- `Tabs { tabs, value, onChange(id), label }` The same look for local state.

## Button
`{ variant?: "primary" | "secondary" | "ghost" | "danger", size?: "sm" | "md", loading?, block?, round?, icon?, ...button props }`
`loading` disables the button and shows a spinner. Defaults to `type="button"`. At most one primary on a screen; a disabled primary goes neutral.

## Field
`Input`, `Select`, `Textarea`: `{ label, hint?, error?, className?, ...native props }`. The label is wired with `htmlFor`; `hint` and `error` are linked through `aria-describedby`, and `error` sets `aria-invalid`. `Select` takes `<option>` children. Keep `hint` only where a person would otherwise get it wrong.

Inside `FormGrid` (`src/setup-kit.tsx`) on a page, fields become one column of labelled rows divided by hairlines; in a drawer they stack. `.ax-control` on a bare control is not needed for the look: `base.css` draws `input`, `select`, `textarea`, checkboxes and radios.

## Field wrapper and Check
`Field { label, hint?, error?, children }` puts a label over any control (a list input, a secret input); the label wraps the control, so no id is needed. `Check { checked, onChange(checked), label, note?, disabled? }` is a checkbox with its name and, only where a person could get it wrong, one line of what it does. On a page, put either directly in `FormGrid` for a hairline row. `Textarea` takes `mono` for JSON and code.

## ConfirmButton, Segmented, Loading, ErrorLine, External
- `ConfirmButton { label, confirm, note?, onConfirm, pending?, variant?: "danger" | "secondary", size? }` asks in place ("Yes, remove / Cancel"); Esc cancels.
- `Segmented { value, options: {value, label}[], onChange, label, block? }` is a few buttons acting as one radio group (arrow keys move).
- `Loading { label? }` and `ErrorLine { error, retry? }` are the quiet one-line loading and failure states.
- `External { href }` is a link to another site that opens a new tab.

Their small bits of CSS are in `styles/setup.css` until the identity moves them into `components.css`.

## Table
`Table<T> { columns, rows, rowKey(row), label, onRowClick?, selected?, onSelectedChange?, bulk?, loading?, error?, empty?, fill?, minWidth?, canSelect?, busy?, rowLabel? }`
- `columns: { key, header, cell(row), num?, width?, truncate?, min?, hideBelow?, pin?, wrap? }[]`. No outer card; rows are one line of 40px with hairline rules; row checkboxes always show. `truncate` ends a cell in an ellipsis and gives it the room left over; `hideBelow` drops a column under 860px or 600px; `pin` keeps the row's actions in view; `wrap` lets a cell grow.
- Pass `onSelectedChange` (and `selected`, a `Set` of row keys) for the checkbox column and the bulk line; `bulk` is the buttons beside "N selected".
- `canSelect(row)` disables a row's checkbox and leaves it out of select-all; `busy` dims the rows while the next page or filter loads (the table keeps its size, so nothing jumps); `rowLabel(row)` names each row's checkbox.
- States: `loading` with no rows shows skeleton lines, no rows shows `empty` (use `<Empty/>`), `error` shows its node.
- Scrolls sideways inside itself. A sticky head against the page needs `fill`.

## Drawer
`{ open, onClose, title, children, footer?, width? }` A right-side drawer with its own scroll, Esc and scrim to close, focus moved in and returned on close, Tab kept inside. Mark a field `data-autofocus` to focus it on open.

## Empty
`{ title, hint?, action?, icon? }` One line of what is missing and the one action that fixes it, which reads as a link.

## Toast
`ToastStack { toasts, onDismiss }` is rendered once by `main.tsx`. Pages raise toasts with `useNotify()` from `src/hooks.ts`: `notify("ok" | "info" | "error", "text")`. Errors announce as alerts and stay until dismissed.
