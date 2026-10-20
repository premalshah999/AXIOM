# Greenhouse (job-boards / boards.greenhouse.io and employer-site embeds) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9342), never the worker Chrome. Nothing was
submitted and nothing was typed into identity fields. The only text typed was the city prefix "Chic" into "Location
(City)", to observe the typeahead; it was cleared. I loaded the confirmation routes with a plain GET; they are
static pages and create nothing.

**Pages probed**

| Page | URL | What it is |
|---|---|---|
| Sparksoft | `https://job-boards.greenhouse.io/sparksoftcorporation/jobs/5257455007` | A hosted job board, and the job behind the production "Final submit control was not unique" stall |
| Asana | `job-boards.greenhouse.io/asana/jobs/8194522` | Redirects to **`https://asana.com/jobs/apply/8194522?gh_jid=8194522`**, an employer page that embeds the form in an iframe |
| Asana, direct | `https://job-boards.greenhouse.io/embed/job_app?for=asana&token=8194522` | The classic embed URL, opened on its own |
| Asana confirmation | `https://job-boards.greenhouse.io/embed/job_app/confirmation?for=asana&token=8194522` | GET only |
| Sparksoft confirmation | `…/sparksoftcorporation/jobs/5257455007/confirmation` | GET only |
| Pinterest | Redirects to `https://www.pinterestcareers.com/jobs/?gh_jid=8213988` | **A Cloudflare managed challenge** ("Just a moment…", Turnstile) in headless Chrome, still there after more than 12 s |
| Toast | `job-boards.greenhouse.io/toast/jobs/8233156` | Redirected to `careers.toasttab.com/homepage`. The job is likely closed |

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_5e30dcd530c6d323603541c83143cffe98ad1ecf98c71a8f4d4554b75677c790` | inspect-web-page on the Sparksoft job board, with scripts, JSON shapes and accessibility |
| `ev_2ee6b92c57c56ebe4634eaf14cfa2893cfc69750c46542c46f876bb5d1ef94a3` | export-web-scripts: `assets/entry.client-ClhIqXFJ.js` (161 KB, the form logic), `vendor-Ann2cuF6.js` (941 KB) and `manifest-dc5ec512.js` (Remix routes) from `job-boards.cdn.greenhouse.io` |
| `ev_597aed016ef4a98311c5b41d611fbc302778da99edcb38a1e3d1394c90759f3a` | inspect-web-page on the Asana employer page (`iframe#grnhse_iframe`, loader `boards.greenhouse.io/embed/job_board/js?for=asana`) |
| `ev_be3708a294ea3eadb6c305ada8d86fc58d8d4e87fdb8e9817e8c4ba3167e5090` | export-web-scripts for the Asana page, including the `Grnhse` embed loader source |

**Other sources**
- Locale JSON: `https://job-boards.cdn.greenhouse.io/locales/en/job_post.<hash>.json` and `common.<hash>.json`. Every
  quoted UI string comes from there.
- Production data, read only. Sparksoft run `c1d68b4d235c4044bf7f07816216a263` and the recent Greenhouse failures.

**Conventions.** **Observed** means seen live, in JSON or in the bundle. **Inferred** means my reading.

---

## 1. Workflow

### Routes (observed in the Remix manifest)

| Route module | URL | Notes |
|---|---|---|
| `routes/$url_token_.jobs_.$job_post_id` | `https://job-boards[.eu].greenhouse.io/{board}/jobs/{id}` | Job description plus the form on one page. `boards.greenhouse.io/{board}/jobs/{id}?gh_jid=` redirects here, or to the employer site |
| `routes/$url_token_.jobs_.$job_post_id_.confirmation` | `/{board}/jobs/{id}/confirmation` | Success |
| `routes/embed.job_app` | `/embed/job_app?for={board}&token={id}`, or `?for={board}&validityToken={embedToken}` | The iframe form |
| `routes/embed.job_app_.confirmation` | `/embed/job_app/confirmation?for={board}&token={id}` | Embed success |
| `routes/$url_token_.job_application_requests.$token` (+ `…confirmation`) | `/{board}/job_application_requests/{token}` | Request-token flow. Submit sends `request_token` |

### No account
- The form is one page and there is no account. "Autofill my application" offers a MyGreenhouse profile:
  `GET https://my.greenhouse.io/users/self?job_post_id=…` returns 401 when signed out.
- The confirmation page offers "Track your application. Sign in to keep tabs on your application…". That is optional.

### Resume
- "Attach" opens the file input. Dropbox, Google Drive and "Enter manually" are the other options.
- Uploads go **directly to S3** through presigned fields. `GET https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields[]=resume&fields[]=cover_letter`
  returns `{"url":"https://grnhse-prod-jben-us-east-1.s3.amazonaws.com","resume":{"fields":{policy, x-amz-…, success_action_status:"201"}}}`.
  The policy's key prefix is `stash/applications/resumes`, with `content-length-range` 1–104857600.
- Greenhouse does no resume parsing into fields. MyGreenhouse autofill is separate.

### Embeds on employer sites (observed, Asana)
1. The employer page loads **`https://boards.greenhouse.io/embed/job_board/js?for={board}`**, the `Grnhse` loader.
2. The loader renders **`div#grnhse_app > iframe#grnhse_iframe`**, with `title="Greenhouse Job Board"`,
   `scrolling="no"`, `allow="geolocation"`, `width=100%` and a set `height`. Asana's iframe measured 738×3994.
3. The iframe's `src` is `https://job-boards.greenhouse.io/embed/job_app?for=asana&validityToken=<Grnhse.Settings.embedToken>`.
   The job comes from `gh_jid` in the parent URL.
4. **The validityToken URL does not work outside its parent.** Opened directly, it redirected to
   **`/embed/job_board?for=asana&error=true`**, the job list.
5. The classic URL **`/embed/job_app?for={board}&token={jobId}`** does open the same form on its own. It has one
   submit button, `form#application-form` with `action="/embed/job_app?for=asana&token=8194522"`, and the EEO block.

### How the loader and iframe talk (loader source, observed)
- **The loader listens for:**
  - **`message`** events. A numeric `e.data` sets the iframe height, which comes from the embed posting
    `document.body.scrollHeight`.
  - `{scrollTo}`, which runs `window.scrollTo(e.data.scrollTo)`.
  - autofill status messages.
  - It only accepts these from `Grnhse.Settings.targetDomain`.
  - It has **no handler for "greenhouse.confirmation"**. Employer code may have one.
- **The embed, on success** (`entry.client`):
  1. `window.parent.postMessage("greenhouse.confirmation","*")`;
  2. `window.location.assign(<confirmation URL>)`.

  The parent page may close or replace the iframe. That is why some employer pages show no receipt (10-18 diagnosis:
  Toast).

---

## 2. Page tech (observed)
- **Remix** (`window.__remixContext`, `__remixRouteModules`) on React 18.
- Assets come from `job-boards.cdn.greenhouse.io`, and **source maps are public** (`*.js.map`).
- i18next loads locales from `/locales/{lng}/{ns}.json`.
- Snowplow analytics (`c.spl.greenhouse.io`) and Google APIs (gapi for the Drive picker).
- Location search goes through `api-geocode-earth-proxy.greenhouse.io`.
- No shadow DOM. Iframes appear only in embeds and for reCAPTCHA.
- **Hidden ARIA popups are always in the DOM**: the intl-tel-input `div.iti__dropdown-content[role=dialog].iti__hide`
  and `ul.iti__country-list[role=listbox]`.

---

## 3. Fields and widgets

### The standard fields (Sparksoft, observed)

| Field | DOM |
|---|---|
| First / Last / Email | `input#first_name`, `#last_name`, `#email` (type=text, required, `aria-label` = label, maxlength 255, autocomplete set) |
| Country | `input#country[role=combobox]`, react-select |
| Phone | `input#phone[type=tel][data-intl-tel-input-id]` inside `.iti.iti--allow-dropdown.iti--show-flags.iti--inline-dropdown`. The flag button is `button.iti__selected-country[aria-haspopup=dialog][aria-controls="iti-0__dropdown-content"][aria-label="Select country"]` |
| Location (City) | `input#candidate-location[role=combobox]`, an async react-select, plus a **"Locate me"** button |
| Resume / Cover letter | `input#resume`, `input#cover_letter` (type=file, `accept=".pdf,.doc,.docx,.txt,.rtf"`), with Attach / Dropbox / Google Drive / Enter manually |
| Custom questions | `question_{id}`: either `input[type=text]` or a react-select `input[role=combobox]` |
| Checkbox groups | `input[type=checkbox][name="question_{id}[]"][id="question_{id}[]_{optionId}"]`, e.g. "I Acknowledge" |
| EEO / demographic (embed, Asana) | Numeric ids `1478` (sex), `1479` (race), `57037` (ethnicity) and `57038` (veteran), all required react-selects, plus `input#gdpr_demographic_data_consent_given_1[type=checkbox]` |

### react-select (observed)
- The input is `input[role=combobox][aria-haspopup=true][aria-autocomplete=list][aria-expanded]`, with
  `aria-describedby` reading "Select...".
- Open, it shows the listbox **`#react-select-{inputId}-listbox`** (`.select__menu-list`). Options are
  **`#react-select-{inputId}-option-{n}`**.
- **Options exist only while the menu is open.**
- Each required select also renders a **hidden shim**: `input[required][tabindex=-1][aria-hidden=true].remix-css-…-requiredInput`
  with `value=""`. It is used for native validity and is not a field to fill.

### Location typeahead (observed)
- Before typing, there are **0 options**.
- Typing "Chic" fired exactly one `GET https://api-geocode-earth-proxy.greenhouse.io/v1/autocomplete?api_key=…&layers=locality&lang=en&text=Chic`.
- Options then appeared: "Chicago, Illinois, United States", "City of Chicago, Illinois, United States", "Chiclayo,
  Lambayeque, Peru", and so on.
- If the service is down: "Our location service is temporarily unavailable. You can submit your application without
  a location.".

### Submit buttons on hosted job boards (observed)

| Button | Markup | Where | Effect |
|---|---|---|---|
| Header | `<button type="button" class="btn btn--pill" aria-label="Apply">Submit Application</button>` | `div.job__header`, outside the form | Navigation or scroll only. **Its accessible name is "Apply"** |
| Autofill | `<button type="button" class="btn btn--pill btn--secondary">Autofill my application</button>` | `.application--header`, outside the form | MyGreenhouse |
| **Real submit** | `<button type="submit" class="btn btn--pill" aria-disabled="false">Submit application</button>` | `form#application-form.application--form > div.application--submit` | Submits |

- **Embeds** (`/embed/job_app`) have **only** the real submit.

---

## 4. Validation (locale strings)
- **Field errors:**
  - "{{ fieldName }} is required." and "This field is required.";
  - "Please enter a valid email address (example@domain.com).";
  - phone: "Phone number is invalid", "Phone number is too short", "Phone number is too long";
  - "Please enter your location";
  - "Please enter a valid {{ attribute }}." (server `invalid-attributes`).
- **History rows:**
  - "Company name is required.", "Title is required.", "Start date month is required.", …, "Year must be a number
    between 1900 and 2100";
  - "School is required.", "Degree is required.", "Discipline is required.", ….
- **Files:**
  - "{{ file_size }} MB maximum file size.";
  - "Invalid file format selected.";
  - "Attachment has an invalid file format. Please check acceptable file types and try again.";
  - "Accepted file types: {{ file_types }}".
- **Submit failures:**
  - "There was an error processing your application. Please try again." (`application.errors.generic_failure`);
  - "Please complete the reCAPTCHA and resubmit your application." (`application.errors.captcha_message`).
- **Closed jobs:**
  - "The job you are looking for is no longer open.";
  - "The job board you were viewing is no longer active.".

---

## 5. Bot protection
- **reCAPTCHA Enterprise, invisible.** It loads from `www.recaptcha.net/recaptcha/enterprise` with
  `render=<invisibleKey>`; the key seen was `6LfmcbcpAAAAAChNTbh…`.
- **Submit runs `grecaptcha.enterprise.execute(invisibleKey, {action:"apply_to_job"})`.** If the call throws, the
  token sent is `""`.
- **The server may escalate to an emailed security code.** Response `code:"captcha-failed"` with
  `security_code_recipient` makes the form show a **"Security code"** input: "A verification code was sent to {{ email }}.
  To submit your application, enter the 8-character code to confirm you're a human." The next submit carries
  `security_code` instead of a token.
- **Production already pre-arms** an email wait just before submit ("Waiting for a matching employer verification
  email"). Seen in the confirmed Coherent Solutions and Datacor runs.
- **Cloudflare managed challenge on some employer sites.** Pinterest (`www.pinterestcareers.com`) served "Just a
  moment…" / "Performing security verification" (Turnstile) to headless Chrome. *Inferred:* the worker's headful
  Chrome passed it in earlier runs.

---

## 6. Submit (`entry.client`, function `to()` and handler `jn()`)
- **Request:** `fetch(submitPath, {method:"POST", headers:{"Content-Type":"application/json", …}})`, where submitPath
  is the route's own path.
  - Body: `{job_application:{…}, [csrf.key]:csrf.value, security_code | "g-recaptcha-enterprise-token", captcha_retried?:true, request_token?}`.
- **OK:**
  - an embed posts `greenhouse.confirmation` to its parent;
  - then `location.assign(confirmation URL)`, which can add `seek-token`.
- **Not OK:** the JSON `code` decides.
  - `captcha-failed` with a recipient: the security-code step.
  - `captcha-failed` without one: "Please complete the reCAPTCHA and resubmit your application.".
  - `captcha-retry`: the generic failure, and the next submit adds `captcha_retried`.
  - Security-code `expired` or `exceeded`: a message.
  - `invalid-attributes`: field errors.
- **Confirmation (observed):**
  - The document title is **always "Thank you for applying"**.
  - The h1 is the board's `confirmation_message`. The default is "**Thank you for applying.** Your application has been
    received. If there is a fit, someone will be getting back to you."
  - Asana's h1 is "**Task complete!** Thank you for sharing your information with us at Asana…".
  - Job boards add "View more jobs at …", "Back to job post" and "Track your application".

---

## 7. Engine risks
1. **Two "Submit application" buttons on hosted boards. This is the current failure cause.**
   - *Production:* Sparksoft run `c1d68b4d…` logged "Final submit control was not unique" four times. The candidates
     were "Submit Application" and "Submit application". It ended "did not advance".
   - *Inferred:* the same pattern explains the recent cluster of "did not advance" runs: Coherent Solutions ×5,
     Datacor ×4, Prolific ×3, Parallel Systems ×3, Reformation ×2 and Authentic ×2. I checked only Coherent Solutions
     and Datacor: their latest runs were confirmed.
   - The header button is `type=button`, outside the form, with `aria-label="Apply"`. Choose
     `form#application-form button[type=submit]`.
2. **Employer-site embeds are cross-origin iframes** (`iframe#grnhse_iframe`).
   - Fill inside the frame, but scroll the parent page; the iframe is about 4000 px tall with `scrolling=no`.
   - The success signal is the `greenhouse.confirmation` postMessage, or the frame navigating to
     `/embed/job_app/confirmation`. The parent's text may never change.
3. **Don't reload the iframe's validityToken URL outside the parent page.** It falls back to the job list
   (`error=true`). If the frame must be opened directly, use `/embed/job_app?for={board}&token={gh_jid}`.
4. **The emailed security code** (8 characters) appears only after a failed reCAPTCHA assessment. Arm the mail wait at
   submit time, as production does, and treat the code step as part of submit.
5. **The location field is search-only.** Type, wait for the geocode request and then pick.
6. **react-select hidden shims** (`[aria-hidden=true][tabindex=-1][required]`) look like empty required inputs. Ignore
   them.
7. **intl-tel-input** keeps a hidden `[role=dialog]` and `[role=listbox]` in the DOM permanently. Scope popup checks to
   the control's own `aria-controls` (the same class of bug as the Dayforce navbar).
8. **The Cloudflare challenge** on some employer career sites (Pinterest) needs a headful, human-like browser. Treat
   "Just a moment…" as a challenge state, not as an unknown page.
9. **Required demographic questions with no decline option** (Asana, Pinterest, Roblox) are a policy question for the
   owner, not a widget problem.

---

## Engine checklist (testable)
- [ ] On a hosted job board, the final-submit resolver returns exactly one control:
      `form#application-form .application--submit button[type=submit]` (text "Submit application"). The header
      `button[type=button][aria-label="Apply"]` must never count as a submit candidate. Unit test on the saved
      Sparksoft DOM.
- [ ] On an employer page with `iframe#grnhse_iframe`, run the form scan inside that frame. Clicking submit must
      produce `POST {iframe origin}{path}` with `job_application` in the body.
- [ ] Success means any one of:
      - a `message` event "greenhouse.confirmation" from `https://job-boards.greenhouse.io`;
      - a navigation (top level or frame) to `…/confirmation`;
      - the document title "Thank you for applying".
      Record the h1 text, since it is board-specific (e.g. "Task complete!").
- [ ] When a non-2xx JSON submit response (`!response.ok`) carries `code:"captcha-failed"` and `security_code_recipient`, the engine waits for
      the 8-character code email, fills the "Security code" field and resubmits once.
- [ ] For `#candidate-location`: type at least 3 characters, wait for
      `GET api-geocode-earth-proxy.greenhouse.io/v1/autocomplete` to return 200, then choose
      `#react-select-candidate-location-option-0`, or the best match.
- [ ] For each react-select, the chosen option id matches `^react-select-{inputId}-option-\d+$` and comes from
      `#react-select-{inputId}-listbox`.
- [ ] A "Just a moment…" page with h2 "Performing security verification" is classed as a Cloudflare challenge.
