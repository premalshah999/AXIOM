# src/ui: the component family

Tokens only, no literal colours; the CSS is `src/styles/components.css`. `frontend/DESIGN.md` says what each is for and when to use it. Import from the barrel: `import { Button, Panel, Table } from "./ui/index";`.

Page pieces that know the product (a status, a job cell, a row menu, filters, a search box) are in `src/worktools.tsx`; form pieces for Profile and Settings are in `src/setup-kit.tsx`.
