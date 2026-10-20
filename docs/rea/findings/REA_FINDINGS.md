# Where ApplyPilot fundamentally goes wrong: REA analysis (2026-10-17)

## How this was done

- **Tool:** REA 6.5.0 (`rea-agents`, the "Reverse Engineer Anything" CLI).
- **Browser:** a dedicated headless Chrome on port 9339 with its own profile, never the worker Chrome. The pages
  were live public postings: U.S. Bank (Workday), Uber (Oracle), NCMEC (Dayforce guest form) and ServiceNow
  (SmartRecruiters).
- **REA steps:**
  - `inspect-web-page` with accessibility text and script sources;
  - `export-web-scripts`, with static reading of the ATS bundles;
  - `observe-web-execution`, for network initiators and stacks while typing.
- **Our own engine:** I ran its widget code (`widgets.open_combobox`, `_popup_scope`, `wait_options`,
  `visible_options`) against the live forms.
- **Our data:** events from every run since the VC push began (2026-10-15 04:20 UTC).
- **Nothing was submitted.**

## The short version

The engine treats every application as a generic DOM. It scans labels, opens each picker to read its options,
types, reads the same node back, and stacks a per-site exception whenever that model breaks.

The real sites are data-driven apps:
- they ship the form as data;
- they keep options in memory or behind a search request;
- they commit values on blur and re-render the input;
- they put permanent menus and lists on the page.

Four mismatches cause most of the slowness and the "No offered choices" stalls.

## 1. Workday sends the whole form as data, and we rebuild it from the DOM

**Evidence.** REA exported `cx-jobs.min.js` from the U.S. Bank job page (`ev_cf201cb5…`). Its form renderer
builds every field from server metadata:
- `type` (integer, string, object, boolean);
- `required`, `hidden`, `readOnly`;
- `formatMask`, used by phone fields;
- `exitValidation`;
- `promptUrl` plus `renderPrompt.{dependentOn, isMultiSelect, isSearchTypeAheadEnabled}`;
- `items`, used by work and education rows;
- `categoryConfig`/`maxFiles`, used by uploads.

Nested ids are joined as `parent--child`. Our saved Workday steps carry exactly these ids: `formField-legalName--firstName`,
`formField-countryPhoneCode`, `formField-source`, `formField-<questionId>`. Our applications are rendered by this
code.

**Prompt options are complete JSON, fetched up front.** Each prompt fetches `promptUrl` as soon as it mounts and
maps the response `[{id, descriptor}]` to its options. The full option list is in memory and on the network
before anyone opens the picker.

**Impact.** In confirmed Workday runs, slow field writes (≥3 s each) take a median 3.2 of 7.9 minutes, or
**41% of run time**, about 22 per run. Since 10-15 that is 2,813 slow dropdown writes averaging 8.8 s, 1,584 text
writes averaging 9.6 s (p90 33 s) and 660 prompt writes averaging 13.1 s.

**Fix.**
- Listen with `page.on('response')` for the page metadata and `promptUrl` responses.
- Key them by `formField-<id>`, so required flags, types, masks, dependencies and complete option lists are known
  before touching the UI.
- Decide answers from that data. Use the UI only to enter the chosen value.

## 2. "Read every option first" doesn't match how pickers work, and the failures are swallowed

`_discover_options` opens each picker and reads options from the DOM, with an 8 s budget, before the resolver
decides. It then reopens the picker to select. Since 10-15:

| ATS | "Observation changed or timed out" | "No offered choices readable" |
|---|---|---|
| Workday | 652 (175 runs) | 276 (128 runs) |
| SuccessFactors | 248 | 62 |
| Dayforce | — | 226 (18 runs) |
| Eightfold | — | 90 |

Why, case by case:

**a. Dayforce: a whole-page "picker still open" check.**
- `widgets.dismiss_pickers` (widgets.py ~1150–1191) treats *any* visible `[role=listbox]` or `[role=menu]` on the
  page as a dropdown left open.
- Dayforce's permanent top navigation bar is `<ul role="menu" class="ant-menu … navbar-menu">` (1104×64). It can
  never be closed, so every combobox discovery raises `Previous dropdown remains open; its choices cannot be
  reused`.
- I reproduced this with our own widget code on the live guest form: every field failed in 1.2 s, while the
  options really are there (`[role=option]` Email / Mobile Phone / Home Phone in an Ant Design portal).
- REA accessibility evidence (`ev_86997d5c…`): 13 comboboxes plus one permanent, focusable `menu`.
- This alone explains Country 111, Preferred Contact Method 98, Country dialing code 74, Language 62,
  State/Province 60 and How did you hear 34.
- The existing hard-coded exclusions (`selectedItemList`, `"items selected"`) show this bug class was patched
  per site before. The real fix is ownership: only the popup this field controls (`aria-controls`/`aria-owns`,
  or a popup that appeared after this open) counts.

**b. The error reason is swallowed.**
- `_discover_options` ends in `except (PlaywrightError, ValueError): pass`, then emits "No offered choices were
  readable".
- The Dayforce cause was therefore invisible in our data for days. Log the exception type and message.

**c. Virtualized lists (Workday).**
- The listbox is a react-virtualized List with `overscanRowCount: 10`.
- The bundle has `turnOffListVirtualizationForAutomation`, which renders every item, but nothing ever sets it.
- The DOM never holds a long list, so reading the DOM can't be the source of truth for long pickers.

**d. Search-only fields.**
- SmartRecruiters "City" (20 failures) and Greenhouse "Location (City)" (16) have **no options until you type**.
- REA, observing SmartRecruiters (`ev_14e7c1a4…`): before typing, 0 options. Typing "Bethes" fired exactly one
  debounced request, `GET /oneclick-ui/api/location/autocomplete?q=Bethes` (Angular zone XHR), and 4 options
  appeared.
- The accessibility tree (`ev_a5726c46…`) names them "Bethesda, MD, US", "Bethesda, OH, US", "Bethesda, Wales,
  United Kingdom" and "Cannot find your city? Click here to fill in manually". The last one is an action, not a
  value.
- Reading these fields before typing can only fail.

**e. Wrong popup type.**
- Eightfold "Date" (69 failures) is `role=combobox` with `aria-haspopup=dialog`: a date picker.
- ARIA allows listbox, grid, tree or dialog popups. The engine treats every combobox as a listbox.

**Fix: search-first, ownership-scoped pickers.**
- Pick the strategy from the widget's declared contract:
  - `aria-autocomplete` list or both, Workday `isSearchTypeAheadEnabled`, and location fields: type the intended
    answer, wait for that search request to finish and the owned option set to stop changing, then choose. Never
    pre-read.
  - `aria-haspopup=dialog`: use the date driver.
  - Readonly, unselectable selects (Ant Design `readonly` inputs): open, then read the owned listbox only.
- Read the full list only when it's short and fully rendered. Otherwise trust the data channel (§1) or the
  search.
- Skip "can't find / enter manually / other" action rows unless they're the intended answer.

## 3. The commit model for text and masked fields is wrong (Workday)

**Evidence.** In the same Workday bundle, the TextField (component `E`, built on `formatMask`) is:
- **Uncontrolled.** It renders `defaultValue`, plus `key: inputKey || value`. With no `inputKey`, every committed
  value change makes React mount a **new input node**. That is why our handles detach, "Element is not
  attached", and why `rematch` sits in every path.
- **Committed on blur.** `onBlur` calls `setValue({id, value})` (async), then `exitValidation` (async), then sets
  errors or alerts. `onInput` only reformats the mask, and commits only when the input isn't focused.
- **Masked.** On every input event `applyMask` rewrites the value and the caret using `selectionStart`.

**Why it hurts.** `human_type` does `fill`, then `blur`, then reads back with `input_value() == text` on the old
node. For masked phones the displayed value is formatted, so raw equality fails and we clear and retype key by key.
Workday Phone Number has 218 slow writes averaging **34 s**, and `human_type` hit 95 deadline timeouts.

**Fix.**
1. Type, then Tab out to commit.
2. Wait until the field's committed state settles: a new node under the same stable id or name, and no pending
   validation.
3. Compare mask-normalized values: digits for phones, Workday's `cleanUpMask` rules for others.
4. Don't re-scan the page per field.

Expected: a Workday text write drops from about 9 s to under 2 s.

## 4. We're blind at submit time (Oracle), plus a honeypot rule

**Most Oracle 400s are harmless.** "Employer returned HTTP 400 during submission" appears 48 times on 22 Oracle
jobs, but 19 of the 33 runs that got a 400 ended confirmed. Example: SAIC's 400 came 1 s after the commit step
began, the confirmation page came 9 s later, and the acknowledgement email arrived. Most of these 400s come from
auxiliary requests around the code step or a draft save.

**Real Oracle validation is a transient toast.** In the bundle, `manageApplicationCreationErrors` turns a 400 body
into `"<Section title>: <server message>"` and shows it in a toast. We log only the status code.

**The two real failures** came after Oracle's resume import ("Profile successfully imported") created rows missing
required sub-fields: Degree, Full Name, Country, and a "Link 2" format error.

**Fix.**
- At submit, record the response's message, sanitized, plus the toast text.
- Treat 4xx on non-submit endpoints as non-fatal.
- After a resume import, expand the imported rows and validate them.

**Honeypot.** Oracle's email step contains a hidden text input labelled "honeypot" (`aria-hidden`,
`tabindex=-1`, `input-row--invisible`). Its value is sent as `control` in the candidate REST payload, which flags
bots. We have never filled it, since no event mentions it. Make it a hard rule: never write to
`aria-hidden`/`tabindex=-1`/invisible inputs.

## Not the problem (checked, so don't spend time here)

- **SmartRecruiters shadow-DOM option text.** `visible_options` reads it correctly ("Bethesda, MD, US").
- **Permanent menus on other ATSs.** None on the public Workday, Oracle, SuccessFactors, iCIMS, Taleo, Eightfold
  or SmartRecruiters pages. Application pages behind login weren't checked.

## Priority order

1. **P0, one-line class of fix:** scope `dismiss_pickers` and discovery to the field's owned popup, and log
   swallowed reasons. Unblocks Dayforce immediately.
2. **P0:** search-first for editable, async and location pickers; the dialog popup type goes to the date driver.
3. **P1:** Workday data channel (metadata plus `promptUrl` JSON).
4. **P1:** blur-commit and mask-aware verification. This attacks 41% of Workday run time.
5. **P2:** submit-time error capture; the honeypot rule.

## Reproduce

These scripts live in `codex-handoff/rea/`:
- `drive.mjs` drives the dedicated browser;
- `probe_engine.py` runs our widget code against a live page;
- `df_form_a11y.json`, `sr_exec.json` and `sr_a11y.json` are the REA evidence.

Setup:
```
npm install --ignore-scripts rea-agents@6.5.0          # in an empty folder
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --remote-debugging-port=9339 \
  --remote-debugging-address=127.0.0.1 --user-data-dir=<fresh dir> about:blank      # never port 9224
rea list-browser-targets http://127.0.0.1:9339 --format json
rea inspect-web-page http://127.0.0.1:9339 <TARGET> --include-script-sources \
  --allowed-origins https://<site origin> --allowed-origins https://<cdn origin> --format json > cap.json
rea export-web-scripts /abs/cap.json /abs/new-dir
rea observe-web-execution http://127.0.0.1:9339 <TARGET> --observation-ms 20000 --format json   # act after "armed"
```
