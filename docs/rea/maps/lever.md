# Lever (jobs.lever.co hosted application form) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9341), never the worker Chrome. Nothing was
submitted. I never clicked Submit, so hCaptcha's `execute()` never ran on a Lever page and no token was requested. I loaded
public pages, read public scripts, and made one plain `GET` of the static `/thanks` route (it creates nothing). One
experiment ran on a local `data:` page with no third party involved (§5).

**Pages probed**

| Page | URL | What it is |
|---|---|---|
| Metergy Solutions | `jobs.lever.co/metergysolutions/1a20d596-888d-4191-b22d-24d7435a7a69/apply` | A short form, no EEO block |
| Shield AI | `jobs.lever.co/shieldai/7c8aaf63-ab92-4c0f-8a54-9cf1ce07ca69` (job page), `/apply`, `/thanks` | A long form: location select, custom cards, EEO block, disability signature |
| Production saved page | run `3cc81486…` (Shield AI, 10-18 19:22 UTC) `help.jpg` | The page Lever re-renders after refusing a submission |

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_2b1408c172768b98e26f877ff6ea5d2fada5c2801103d0534f332e0715d4de4b` | inspect-web-page on the Metergy `/apply` page, with scripts, JSON shapes and accessibility. Scripts: `secure-api.js` (49,547 B), `parseResume.js`, `retrieveLocations.js`, `application.js`, jQuery 3.6.1, Bugsnag, and Cloudflare's `/cdn-cgi/challenge-platform/scripts/jsd/main.js`. REA retained no text for the Lever scripts, so I read them with a plain `GET` of the same public files |

**Other sources**
- Public JS by plain GET: `jobs.lever.co/js/{parseResume,application,retrieveLocations}.js`;
  `js.hcaptcha.com/1/secure-api.js` and `api.js`; `newassets.hcaptcha.com/c/<hash>/hsw.js` (1.48 MB).
- Production data, read only: 107 submit runs on 95 Lever jobs, 0 confirmed. Reasons: 48 "security check refused", 13 "Browser
  control failed at browser.py:441", 11 "2Captcha: SOLVER_REJECTED", 12 worker-Chrome connection messages, 3 interrupted, 2
  "puzzle not solved in this browser", 2 missing Education text, 1 missing Transcript. Open now: 18 waiting_browser, 2 new, 2 paused,
  2 submission_unknown (Zoox, AIFund).
- `captcha.py`, `captcha_observation.py`, `browser.py`, `desktop_cdp.py`, `forms.py`, `widgets.py`, `js/scan.js`.

**Conventions.** **Observed** means seen live, in public script text or in a saved production page. **Inferred** means
my reading, not verified end to end.

---

## 1. Workflow

| Step | URL | Notes |
|---|---|---|
| Job page | `https://jobs.lever.co/{company}/{uuid}` | `a.postings-btn.template-btn-submit[data-qa=show-page-apply]`, text "apply for this job", href `…/{uuid}/apply`. No hCaptcha on this page; Cloudflare JSD is present |
| Application form | `…/{uuid}/apply` | One page, `form#application-form` (`method=post`, `enctype=multipart/form-data`, `action=""`) |
| Submit | `POST …/apply` as a document navigation | Native form post (`resource_type: document` in production's `submission_http_error` events) |
| Refusal | Same URL, HTTP 400 | Re-renders the form with an orange banner "✱ There was an error verifying your application. Please try again." Fields stay filled; **the file input is empty again** (screenshot: "ATTACH RESUME/CV" with no file) |
| Receipt | `…/{uuid}/thanks` | Static route, observed by GET: `div.page.thanks`, `div.confirmation-message` "Application submitted!", "Return to the main page". It loads for any posting without applying (200 for Shield AI), so a bare visit is **not** a receipt |

- There is **no account and no email code**. The only gate is hCaptcha.
- *From the 10-18 research:* Lever's Postings and Data APIs accept applications only with the employer's own
  key or partner OAuth; there is no applicant-side API, so the hosted form is the only route.

---

## 2. Page tech (observed)
- Server-rendered page with jQuery 3.6.1, Bugsnag, a cookie banner (`cookieconsent`, buttons `cc-btn cc-allow` and
  `cc-deny`), Google tag, and the Cloudflare JSD script (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`). No DataDome, no
  reCAPTCHA, no Turnstile.
- No shadow DOM. Iframes: two fixed, hidden full-viewport hCaptcha **enclave** frames, a 1x1 hidden frame, and (Shield AI) a
  233x48 "LinkedIn Embedded Content" frame for "Apply with LinkedIn".
- A plain `GET` of the job page and the `/apply` page returned 200 with no bot wall from this network.
- Navigator values my browser gave the page: `webdriver=false`, `window.chrome` an object without `runtime`, 5 plugins, `languages=["en-US"]`,
  a UA without "HeadlessChrome".

---

## 3. Fields and widgets (observed)

### Standard block (`name` attribute)

| Field | DOM | Notes |
|---|---|---|
| Resume/CV | `input#resume-upload-input[name=resume][type=file]`, `data-qa=input-resume`, required | Visible button "ATTACH RESUME/CV". **Uploading starts an automatic parse, see below** |
| Full name | `input[name=name]` `data-qa=name-input`, required | |
| Email | `input[name=email][type=email]`, required | |
| Phone | `input[name=phone]` | |
| Current location | `input#location-input[name=location]` (`.location-input`), required on some postings | Typeahead, below |
| Current company | `input[name=org]` | |
| Links | `input[name="urls[LinkedIn]"]`, and per posting `urls[Twitter]`, `urls[GitHub]`, `urls[Portfolio]`, `urls[Other]` | |
| Opportunity location | `select[name=opportunityLocationId]` ("Select...", "Washington, D.C.", "San Diego, California") | Only on multi-location postings; first on the form at Shield AI |

Hidden inputs (names only): `selectedLocation`, `cards[<uuid>][baseTemplate]` (one per custom card), `accountId`,
`linkedInData`, `origin`, `referer`, `timezone`, `socialReferralKey`, `socialSource`, `resumeStorageId`,
**`h-captcha-response`**, `source`.

### Custom questions ("cards")
- Names are `cards[<uuid>][field0..n]`, grouped under section headings the employer chooses (Metergy: "STANDARD US",
  "SALARY", "LONG ISLAND CITY OFFICE"; Shield AI: "COMPLIANCE WITH WORK AUTHORIZATION", "EXPORT CONTROL REQUIREMENTS", "MISSION",
  "HOW DID YOU HEAR ABOUT US?", "FULL LEGAL NAME", "FELLOWSHIP QUESTION/TRANSITIONING SERVICE MEMBER", "WORK LOCATION
  AVAILABILITY").
- Types seen: radio groups (2 to 20 options, required marked "✱"), text ("Type your response"), textarea (required or not),
  `select` ("Please select an option", Yes/No). Required is the `required` attribute and the "✱" mark in the label.
- Checkbox groups that share a name are one question with "at least one" semantics: `application.js` removes `required`
  from the whole `.required-field :checkbox[required]` set once any box is checked.

### EEO and self-ID (Shield AI)
- `eeo[gender]` select: Select ..., Male, Female, Decline to self-identify.
- `eeo[race]` **radio group**, 8 options ("Hispanic or Latino …", "White (Not Hispanic or Latino)", and so on).
- `eeo[veteran]` select: identify as one or more protected categories / not a protected veteran / decline.
- `eeo[disability]` select `#disabilitySelectElement`: Yes / No / "I do not want to answer".
- **Choosing any disability option reveals and requires** `eeo[disabilitySignature]` ("Enter your full name") and
  `eeo[disabilitySignatureDate]` ("MM/DD/YYYY"): `application.js` shows `#disabilitySignatureSection` and sets `required` on its inputs when the select
  has a value, including "No".
- Pronouns (script present): `#candidatePronounsCheckboxes` with `.standardPronounsOption`, `#useNameOnlyPronounsOption`
  and `#customPronounsOption` plus `#customPronounsTextField`. They are mutually exclusive in script.
- Veteran and disability sections have expandable info buttons (`.eeo-more-info-button`, `aria-expanded`).

### Location typeahead (`retrieveLocations.js`)
- Typing in `.location-input` calls `GET /searchLocations?text=<q>` after a 500 ms debounce (queries over 100 characters are rejected).
- Results are `div.dropdown-location#location-<n>` in `.dropdown-results`, with no ARIA roles.
- A `mousedown` on a result (or Enter on the active one) writes `JSON.stringify(result)` to the hidden `selectedLocation`.
- **A blur while the dropdown is open and the target is not a result clears both the input and `selectedLocation`.** Typing then
  tabbing away loses the value. `widgets.lever_location` (`widgets.py:2013`) types, waits for `.dropdown-location`, clicks, then
  verifies `selectedLocation`.

### Resume upload and auto-parse (`parseResume.js`)
- On `#resume-upload-input` `change`, the page sends `POST /parseResume` (multipart: `resume`, `accountId`; max 100 MB).
- The JSON reply **fills fields the user has not touched**: `org`, `phone`, `name`, `email`, `location` (plus hidden
  `selectedLocation`), `residentialLocation[...]`, `urls[...]` and the hidden `resumeStorageId`.
- "Touched" means a `change` or `paste` event on that input. A filled value without a `change` yet still counts as untouched.
- Status elements: `.resume-upload-working`, `.resume-upload-success`, `.resume-upload-failure`, `.resume-upload-oversize`.

### Buttons
- `button#btn-submit[type=button]` "SUBMIT APPLICATION" (`postings-btn template-btn-submit`).
- `button#hcaptchaSubmitBtn[type=submit].hidden`: the real submit, clicked by script.
- Cookie banner: `button[type=submit].cc-btn` "accept" and "deny" (class `cc-allow`, `cc-deny`).
- "Apply with LinkedIn" (an embedded widget frame).

---

## 4. Validation
- Native HTML5 `required` blocks the post before any network request. Empty required fields never reach the server.
- The server-side banner seen in production: "✱ There was an error verifying your application. Please try again."
- Not observed: field-level server errors. Production's other stops are profile gaps: "Provide All Post-Secondary Education
  Attained - Formatted as: College Name; Degree Obtained …" (2 runs), "Please upload your Transcript?" (1 run).

---

## 5. Bot protection: hCaptcha through `secure-api.js`

### What the page does (observed in the page HTML and public scripts)
```
<script src="https://js.hcaptcha.com/1/secure-api.js?host=jobs.lever.co&onload=onLoad" async defer>
<div id="h-captcha" class="h-captcha" data-sitekey="e33f87f8-88ec-4e1a-9a13-df9bbb1d8120">   <input id="hcaptchaResponseInput" type="hidden" name="h-captcha-response">
onLoad: captchaId = hcaptcha.render('h-captcha', {sitekey, callback: onSuccess, 'expired-callback': onExpired})
onSuccess(token): hcaptchaResponseInput.value = token; if (executedFromSubmitButton) clickSubmitButton()
#btn-submit click: if (token && !expired) clickSubmitButton(); else { executedFromSubmitButton = true; hcaptcha.execute(captchaId) }
form 'submit' event: if (!token || expired) { preventDefault(); executedFromSubmitButton = true; hcaptcha.execute(captchaId) }
clickSubmitButton(): document.getElementById('hcaptchaSubmitBtn').click()   // so HTML5 validation still runs
```
- There is **no `error-callback` and no `close-callback`**: if the puzzle closes, nothing happens and nothing posts.
- There is no `rqdata` anywhere in the page or in `secure-api.js` (0 matches). The "enterprise" matches in the HTML are the CSS class
  `gated-feature-enterprise`, not captcha settings.
- The page renders twice: the `.h-captcha` class makes hCaptcha auto-render, and `onLoad` renders again. I saw two enclave frames and two
  `checksiteconfig` calls. Only the explicit one has the callback.

### What `secure-api.js` is (observed in its source)
- A 49 KB loader, not the 390 KB `api.js`. It **forces `size = "invisible"`** for every widget, whatever the page passes.
- It builds a fixed, viewport-sized `iframe` to `newassets.hcaptcha.com/captcha/v1/<build>/static/hcaptcha-enclave.html#frame=enclave&…`,
  with `sandbox="allow-scripts allow-same-origin"`. Inside it hCaptcha creates the `frame=challenge` and `frame=checkbox-invisible` frames.
  All are `visibility:hidden` until a puzzle is needed.
- It stamps the build id as `se=<hash>` and passes only whitelisted render parameters (`sitekey, callback, expired-callback,
  chalexpired-callback, error-callback, open-callback, close-callback, size, theme, endpoint, challenge-container, mode …`).
- It is bound to the page's host (`host=jobs.lever.co`).

### What the widget sends on load (observed)
- Two `POST api.hcaptcha.com/checksiteconfig` calls. The query keys are `host, sc, se, sitekey, spst, swa, v`; **`se` is the secure-build marker**
  (iCIMS's call has no `se`).
- Response keys `c, features, pass`: `pass:true`, `c.type:"hsw"` with difficulty `c:1000`, `features:{custom_theme:true, enc_get_req:true}`
  (iCIMS's also has `a11y_challenge`).
- Then `GET newassets.hcaptcha.com/c/<hash>/hsw.js` (1.48 MB, obfuscated, with base64-embedded WebAssembly). No plain-text probe string
  (`webdriver`, `stack`, `console`, `Runtime`, `playwright`, `selenium`) appears in it, so **its checks cannot be listed statically**.
- On Submit, `execute()` triggers `POST api.hcaptcha.com/getcaptcha/<sitekey>` (seen in production's `submission_requests`).

### The automation signal I could verify
1. **hsw.js writes an `EvalError` to the console** in each enclave frame at load. With Playwright attached, `page.on("console")` received two
   `endGroup` messages whose text is an `EvalError` stack pointing into `hsw.js:2:1479901`. Nothing else is logged. *Inferred:* this is a
   console probe; the usual use is detecting a DevTools client with Runtime enabled.
2. **Local A/B on a `data:` page (Chrome 155, no third party).** The page logs an `Error` through each console method while
   `Error.prepareStackTrace` is a trap.
   - No client attached (page opened through `/json/new`, title read through `/json/list`): the trap fires for **0 of 11** console calls.
   - Playwright `connect_over_cdp` (the engine's driver): the trap fires for **11 of 11** (`debug, log, info, warn, error, dir, table, trace,
     group, groupEnd`, and `groupEnd` with an `EvalError`).
   - The older `stack`-getter trap fires for none in either case, so Chrome 155 patched that variant.
   So **a page can tell that Playwright's CDP attach is present**, because the driver sends `Runtime.enable` to every frame, hCaptcha's
   cross-origin enclave frames included. The engine attaches this way at `browser.py:598` and `desktop_cdp.py:57`.
3. The static values (`navigator.webdriver`, UA, plugins, languages) look normal.

### What the server decides (inferred; not testable without submitting)
Production, 107 runs:
- Bought tokens (2Captcha, the solver's IP): HTTP 400 "error verifying your application".
- Tokens hCaptcha issued to this very browser after a puzzle (Zoox, AIFund, 10-18 02:51): also HTTP 400.
- 10-18 19:22 Shield AI: puzzle "closed without a response token", and the same second a 400 on the document POST.

So the 400 is not "token missing or from another IP". Lever's backend verifies the token with more than a pass/fail: the most
consistent reading is **hCaptcha Enterprise's risk score (siteverify `score` / `score_reason`) falling below Lever's threshold**.
Two negative signals are measurable from here, a third is not:
- **CDP attach** (above).
- **The egress IP.** `api.ipify` gave `38.13.33.142`, owned by Starry, Inc. (AS395354), a residential ISP in Washington. The same address was named by
  SmartRecruiters' DataDome page today as "Automated (bot) activity on your network (IP 38.13.33.142)". Hundreds of automated
  submissions from one address likely cost reputation with every bot-scoring vendor, hCaptcha included.
- Behaviour telemetry and fingerprint depth inside `hsw.js`: not observable.

### What would be needed to pass (described, not implemented)
- **A token type or solver service cannot pass by itself.** The 400 appears for tokens bought elsewhere and for tokens issued to this browser.
  A passive "pass" token is not available to us on request.
- **A driver that does not announce itself.** The one signal I could reproduce is `Runtime.enable` on every frame. Remedies are of three kinds: a patched
  Playwright/Puppeteer build that gets frame contexts without `Runtime.enable` (the "rebrowser-patches" idea), a minimal raw-CDP driver using only
  DOM, Input and Network domains, or no debugging session at all (the owner's own Chrome with prefill by extension and the owner clicking Submit).
- **A cleaner network identity** for Lever runs, as an experiment, since the current address is already flagged by another vendor.
- **A measurement before any production change, with no application submitted:** leave the required `name`/`email`/`resume` fields empty and press
  `#btn-submit`. Lever calls `hcaptcha.execute()`; HTML5 validation then stops the post, because `clickSubmitButton()` clicks a real submit
  button. Read the `getcaptcha` response shape (a puzzle means a high risk score; a pass token with no puzzle means a good one) under each driver and
  network. Run one real application only after a driver/network pair shows no puzzle. I did not run it: it calls `execute()` on Lever's live site,
  which this task excludes.

---

## 6. Submit and receipt
- Click `#btn-submit` (button, not submit). With no token: `execute()` -> puzzle or silent pass -> `onSuccess` -> hidden submit -> native POST.
- Success (inferred from the engine's notes, not observed): a navigation to `…/thanks` with "Application submitted!" in `.confirmation-message`.
  A bare GET of `/thanks` proves nothing.
- Refusal (observed in production): HTTP 400 on the document POST, the banner above, an empty file input.
- The engine already reads the banner as category `security` (`browser.py:2909`, `adapters/base.py:1251`) and restarts a fresh form
  (`restart_rejected_form`, `browser.py:736`).

---

## 7. Engine risks, ranked by jobs recovered

1. **hCaptcha refusal of every Lever submission** (about 26 open or paused jobs, 0 recovered). Not fixable in the form logic.
   - Today's hold on Lever is correct (excluded from dispatch).
   - Next step: the non-submitting measurement in §5 under (a) the engine's driver, (b) a no-`Runtime.enable` driver, and (c) another
     network. Code to read or change: `browser.py:598` and `desktop_cdp.py:57` (attach), `captcha.py:418-432` (site-run widget decision),
     `captcha_observation.py` (provider frames stay untouched).
   - Keep two safe rules: never write `#hcaptchaResponseInput` yourself (Lever posts only through its callback), and do not buy tokens
     for sitekey `e33f87f8-…` (`CaptchaSolver.site_executed` already does this).
2. **Resume parse overwrites typed fields.** Once captcha is solved this hits every Lever job. `/parseResume` fills any field that has had no
   `change` or `paste`. The engine has no Lever-specific ordering (grep finds none outside Oracle).
   - Fix, in the upload step near `forms.py:2414`: attach the resume first, wait until `.resume-upload-working` is hidden and
     `.resume-upload-success` or `.resume-upload-failure` is shown, then fill the fields, blur each, and read back `name`, `email`, `phone`, `location`,
     `org` and `urls[*]` before Submit. Also confirm `resumeStorageId` is non-empty.
3. **Fields that appear or change late.**
   - `eeo[disabilitySignature]` and `eeo[disabilitySignatureDate]` become required once any disability option is chosen, "No" included.
     Fill the name and today's `MM/DD/YYYY`. The owner rule "answered date = today" already exists (b6f1d8e).
   - `eeo[race]` is a radio group, not a select; pronouns are mutually exclusive checkboxes.
   - `select[name=opportunityLocationId]` must be chosen on multi-location postings.
   - Location: type, wait for `/searchLocations`, click the result, never Tab away with the dropdown open.
4. **Cookie-banner buttons are `type=submit`** (`cc-allow`, `cc-deny`). The `forms.py:4240-4256` guard would call them a possible final submit.
   Dismiss the banner by its class, and make the final control `button#btn-submit`, ignoring the hidden `#hcaptchaSubmitBtn`.
5. **`/thanks` is a static route.** If a recovery path ever reopens it, do not call it a receipt without a POST in this run.
6. **Profile gaps stop runs before the commit:** Education text and Transcript upload (3 runs). Owner policy.

---

## Engine checklist (testable)

**Before the browser**
- [ ] Classify the job's hCaptcha as `secure-api` by the page script `js.hcaptcha.com/1/secure-api.js?host=jobs.lever.co`, and keep Lever excluded
      unless a driver/network pair has passed the non-submitting measurement.

**Form**
- [ ] The page has `form#application-form`, `input#resume-upload-input` and `button#btn-submit[type=button]`. The final resolver returns
      `#btn-submit` only. A unit test on the saved Metergy and Shield AI DOM must exclude `#hcaptchaSubmitBtn` and `.cc-btn`.
- [ ] After attaching the resume, `POST /parseResume` completes (`.resume-upload-success|failure` visible) **before** any field is filled. After
      filling, read back `name`, `email`, `phone`, `location`, `org`, `urls[*]`.
- [ ] Location: `GET /searchLocations?text=…` returns 200, a `.dropdown-location` is clicked, and `input[name=selectedLocation]` is non-empty.
- [ ] Choosing an `eeo[disability]` value makes `#disabilitySignatureSection` visible; fill both inputs.
- [ ] `select[name=opportunityLocationId]` is set when present.

**Submit**
- [ ] One click on `#btn-submit` leads to `getcaptcha` and, with a token, one native `POST …/apply`. The engine never sets `#hcaptchaResponseInput`.
- [ ] HTTP 400 with the banner "There was an error verifying your application. Please try again." is recorded as `security`, not as a field error, and
      the next attempt re-attaches the resume.
- [ ] Success means a navigation to `…/thanks` that followed a POST in this run, plus "Application submitted!" in `.confirmation-message`.
