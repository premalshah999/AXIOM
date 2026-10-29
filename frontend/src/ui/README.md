# src/ui: shared primitives

Tokens only, no literal colours. Class names are prefixed `ax-` so they never collide with the legacy rules in
`src/style.css` while pages migrate. CSS lives in `src/styles/components.css`.

Import from the barrel, spelled `./ui/index` (a bare `./ui` resolves to the legacy `src/ui.tsx` beside this folder):

```ts
import { Button, Panel, Pill, Table } from "./ui/index";
```

## Page scaffolding (CSS, no component)

- `<div className="ax-page">` is a page inside the shell: 1320px max width, 24px padding, 20px rhythm. A page that
  uses it removes the legacy `.main` padding automatically. Put no title in the page: the top bar owns it.
- `<div className="ax-page ax-page-fill">` is one table or list filling the region; pair with `<Table fill />` so the
  table is the page's only vertical scroller and its head stays sticky.
- `<div className="ax-toolbar">` is a wrapping row of filters; `ax-toolbar-end` pushes controls to the right.
- Top-bar actions: `import { TopActions } from "./shell/Shell"` then `<TopActions><Button>Export</Button></TopActions>`.

## Panel
`{ title?, actions?, children, flush?, className?, id? }` A titled card. `actions` sits right of the header. `flush`
removes body padding (for a table inside).

## Pill
`{ tone?: "neutral" | "brand" | "ok" | "warn" | "bad" | "info", children, title?, dot? }` A status chip.

## Tabs
- `RouteTabs { view, tabs: {id, label, count?}[], label, current? }` Underline tabs that are real links and set the
  route `tab` for `view` (`go({ view, tab })`). Unknown `route.tab` falls back to the first tab.
- `Tabs { tabs, value, onChange(id), label }` The same look for local state.

## Button
`{ variant?: "primary" | "secondary" | "ghost" | "danger", size?: "sm" | "md", loading?, block?, icon?, ...button props }`
`loading` disables the button and shows a spinner. Defaults to `type="button"`.

## Field
`Input`, `Select`, `Textarea`: `{ label, hint?, error?, className?, ...native props }`. The label is wired with
`htmlFor`; `hint` and `error` are linked through `aria-describedby`, and `error` sets `aria-invalid`.
`Select` takes `<option>` children.

## Table
`Table<T> { columns, rows, rowKey(row), label, onRowClick?, selected?, onSelectedChange?, bulk?, loading?, error?, empty?, fill?, minWidth? }`
- `columns: { key, header, cell(row), num?, width?, truncate?, wrap? }[]`. Rows are one line of 40px; `truncate` ends
  a cell in an ellipsis at `width` (default 280px); `wrap` lets it grow.
- Pass `onSelectedChange` (and `selected`, a `Set` of row keys) for the checkbox column and the bulk bar; `bulk` is
  the buttons shown beside "N selected".
- States: `loading` with no rows shows skeletons, no rows shows `empty` (use `<Empty/>`), `error` shows its node.
- Scrolls sideways inside its own panel. Sticky head against the page needs `fill` (see Page scaffolding).

## Drawer
`{ open, onClose, title, children, footer?, width? }` A right-side drawer with its own scroll, Esc and scrim to
close, focus moved in and returned on close, Tab kept inside.

## Empty
`{ title, hint?, action?, icon? }` One line of what is missing and the one action that fixes it.

## Toast
`ToastStack { toasts, onDismiss }` is rendered once by `main.tsx`. Pages raise toasts with `useNotify()` from
`src/hooks.ts`: `notify("ok" | "info" | "error", "text")`. Errors announce as alerts and stay until dismissed.

### Table extras (work pages)
`canSelect(row)` disables a row's checkbox and leaves it out of select-all; `busy` dims the rows that are shown while the
next page or filter loads (the table keeps its size, so nothing jumps); `rowLabel(row)` names each row's checkbox.
