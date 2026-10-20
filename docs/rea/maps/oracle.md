# Oracle Recruiting Cloud (Candidate Experience) — behaviour map

Date: 2026-10-18. Read-only. Nothing was submitted, no email was entered, no verification token was requested, no
account or profile was created.

**Tenants probed**
- Honeywell, `ibqbjb.fa.ocs.oraclecloud.com`, site `Honeywell`, internal `CX_1`, job 159931. This is an hCaptcha
  tenant. I walked it to the email step.
- REST metadata only (public GETs): Ford `efds.fa.em5`, SAIC `eihu.fa.us8`, Uber `iaziqy.fa.ocs`, JPMC `jpmc.fa`,
  Oracle `eeho.fa.us2`, Goldman `hdpc.fa.us2`, Fortinet `edel.fa.us2`.

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_875bf143122d92797ced5482dbe973bb72df5efaf14947a7c1cd57d042142881` | list-browser-targets |
| `ev_4783e2bb427c54457d96a2aa9c8ebce61cd1682e3f17ebc5a024e9f9913649d2` | inspect-web-page on `/job/159931/apply/email`, with scripts, JSON shapes and accessibility |
| `ev_0cac1fb853e71d4b33a4d36cd78d356ed767d8329e504464456a9ab6360ffd3c` | export-web-scripts. The main bundle is `https://static.oracle.com/cdn/fa/oj-hcm-ce/2607.26.262300322/js/main-minimal.js` (6.18 MB, script key `scr_4d924f29…`, source map published next to it), cxVersion `2607.0.73` |
| `ev_75136bbda54f28f927502cd51716a115ce734983c9093a8a9595dc855f17aac4` | analyze-web-bundle. Mostly noise for this Knockout app; listed for completeness |

**Other sources**
- Translations: `GET /hcmRestApi/CandidateExperience/translations?language=en`, with 2,508 strings. All UI strings
  quoted below come from there, under the key shown.
- Production data, read only: runs, events and mail_rules.

**Conventions.** **Observed** means seen live, in the REST JSON or in the bundle source. **Inferred** means my
reading of the code or data, not verified end to end.

---

## 1. Workflow

### Routes (observed in the bundle route table; the email route was observed live)

| Step | URL pattern |
|---|---|
| Job page | `https://{tenant}.fa.{dc}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{siteUrlKey}/job/{reqNumber}` |
| Email / consent step | `…/job/{reqNumber}/apply/email` (observed after clicking "APPLY NOW") |
| Apply-flow sections | `/job/{jobId}/apply/section/{sectionNumber}` |
| Quick-apply variant | `/job/{jobId}/easy-apply/` |
| After submit, "My Applications" | `/my-profile` (candidate-self-service). Tokenised links use `/my-profile/{token}` |

- **Site key vs site number.** The site key in the URL is not the site number. Honeywell's URL says
  `sites/Honeywell`, but `siteSettings` gives `"siteNumber" : "CX_1"`, and every REST call uses `CX_1`.

### Entry: "You don't need to have an account!" (observed, Honeywell)
- The page reads: "Get started right away with your email. Your profile will be created and kept up to date
  automatically for each of your job applications."
- Visible controls:
  - `input#primary-email-0` (`type=email`, required);
  - checkbox `#legal-disclaimer-checkbox`, "I have read the Important Privacy Information". It is required and
    visually hidden (`input-row__hidden-control`), so click its label;
  - "CANCEL" and "NEXT".
- There is no password. Oracle CE has no accounts. Identity is the email plus an emailed 6-digit code.

### NEXT on the email step (bundle, `email-verification` view model)
- If bot protection is on, the app first runs `hcaptcha.execute()` (see §5).
- It then sends `POST /hcmRestApi/resources/latest/recruitingCEVerificationTokens` with
  `{siteNumber, mode, email, requisitionNumber, captchaToken, control}`. `control` carries the honeypot's value
  (see §3), from `mapCandidateToRest`/`mapCreateToRest`.
- The response decides the next step:
  - **New email:** straight to the apply-flow sections.
  - **Known email (challenge):** the start-of-flow PIN. Template `apply-flow-pin-verification`, header
    "Confirm Your Identity" (`apply-flow.candidate-verification.header`), text "The verification code was sent to
    this email address: {EMAIL_ADDRESS}. When you get the code, type the code into the field to confirm your
    identity and complete your job application. …". The form then opens **prefilled from the stored profile**.
  - Optional extra checks for known candidates: "We first need to verify your last name." /
    "…your date of birth."
- **Production matches the known-email path.** Our email is known to every tenant we have applied to. Confirmed
  runs show `email entry → email code → form → submitting → confirmation`. Examples:
  - Ford: 06:20:03 email entry, 06:20:09 code, 06:20:36 form, 06:21:12 confirmation.
  - Honeywell: the captcha is solved between the email step and the code step.

### Apply-flow sections are server-defined (observed, public GET, no session)
`GET /hcmRestApi/resources/latest/recruitingCEApplyFlows?finder=findByRequisitionNumber;RequisitionNumber="{req}"&expand=sections.pages.blocks&onlyData=true`
returns the whole flow:

| Tenant | Sections | Blocks |
|---|---|---|
| Honeywell | Personal Info / Experience / More About You / Summary | PROFILE_IMPORT, NAME_CONTACT_INFO, ADDRESS, TIMELINE, SKILL, LICENSE_CERTIFICATES, LANGUAGE, DIVERSITY, DISABILITY, VETERAN, DOCUMENTS, DQ_QUESTIONS, SUMMARY |
| Ford | + "Job Application Questions" | PROFILE_IMPORT, NAME_CONTACT_INFO, ADDRESS, DQ_QUESTIONS, EXPERIENCE, EDUCATION, DOCUMENTS, LICENSE_CERTIFICATES, SKILL, LANGUAGE, DIVERSITY, JA_EXTRA_INFO×3, DISABILITY, SUMMARY |
| SAIC | 5 sections | …, DQ_QUESTIONS, JA_EXTRA_INFO×2, EXPERIENCE, EDUCATION, DOCUMENTS, SUMMARY |
| Uber | 5 sections | …, DQ_QUESTIONS, EXPERIENCE, EDUCATION, DIVERSITY, DOCUMENTS, SUMMARY |
| JPMC | 5 sections | …, ADDRESS, LOCATION_PREFERENCE, DQ_QUESTIONS, EDUCATION, EXPERIENCE, DOCUMENTS, DISABILITY, DIVERSITY, VETERAN, SUMMARY |

- **Flags.** Every tenant probed has `LegalEnabledFlag` and `EsignEnabledFlag` true, `QuickApplyEnabledFlag` false and
  `ProfileVersion` `V2`.
- **`SingleClickApplyFlag`** is true for Honeywell, Ford, SAIC and Uber, and false for JPMC and Oracle. *Inferred:*
  it switches between `apply-flow-navigation-singlepage` and `-multipage`, both of which are in the bundle.
- **Each block names its metadata and write services.** Examples:
  - `ORA_DQ_QUESTIONS`: `recruitingCEQuestions?finder=RowFinder;RequisitionNumber=<REQ_NUM>&expand=answers&limit=500`
    for metadata, and `recruitingCEJobApplications/<SUBMISSION_ID>/child/questionnaireResponses` for writes.
  - `ORA_DIVERSITY`: `recruitingCERegulatoryConfigs?finder=findAttrByRequistionNumber;…`.
  - `ORA_DISABILITY`: `recruitingCESecuredRegulatoryConfigs…`.
  - `ORA_ADDRESS`: `recruitingCEAddressFormats/<COUNTRY_CODE>`.
  - `ORA_NAME_CONTACT_INFO`: `recruitingCEContactInformationMetadata?finder=findByFlowVersionId;…`.
- **Observed:** these metadata services return **HTTP 401 without the session access code**. I tested
  `recruitingCEQuestions` for Honeywell 159931. Inside a real session the browser fetches them with the
  `Ora-Irc-Access-Code` header.

### Resume import (bundle and translations)
- `recruitingCEProfileImportConfigs` (observed for Honeywell) lists the import providers: `AWLI` (LinkedIn), `INDEED`
  and `RESUME_PARSER`.
- Upload sends `POST /recruitingCECandidateResumeParsers` with `{profileType:"rp", fileTitle, fileContents, …}`.
- Strings:
  - "Your resume is being processed. It will take some time to complete."
    (`apply-flow.section-personal-information.resume-parsing-in-progress-message`);
  - "Profile successfully imported." (`apply-flow.profile-import.success-label`);
  - "Your profile was imported from your resume." (success toast);
  - failure: "Your data can't be imported from your resume, but you can manually complete your job application."

### Review and submit
- The last section is `ORA_SUMMARY` ("Review and Submit").
- The submit sequence is in §6. **The application is created before the final code step**; see "You're almost
  done".

---

## 2. Page tech

**Framework (observed)**
- Knockout.js components and templates (`data-bind`, `<!-- ko … -->`) and Oracle JET (`oj-progress-bar`,
  `oj-textarea`).
- **React islands** are mounted through a Knockout `react:` binding (`cx-select`, `cx-select-pills`,
  `single-select-combobox`).
- There are no iframes in the form itself; the only iframes are hCaptcha's. No shadow DOM was seen.
- Buttons are `type=submit` almost everywhere, including "Copy Link" and "Add Job to My Job Selections".

**Server-driven data (observed)**
- `GET /hcmRestApi/CandidateExperience/globalSettings`:
  - tenant settings;
  - the **captcha config**;
  - the misc-attachment allow-list (`ORA_IRC_MISC_ATTACH_FILE_TYPES` = ".txt, .rtf, .doc, .docx, .pdf, .odt, .htm,
    .html, .jpeg, .png, .zip, .HEIC, .pptx, .msg, .jpg, .eml");
  - `ORA_IRC_AUTO_CONFIRM_CANDIDATE_ENABLED` (Y for Honeywell, Oracle and Fortinet; N for Ford, SAIC, Uber, JPMC and
    Goldman);
  - `ORA_IRC_KEEP_CANDIDATE_SIGNED_IN_ENABLED`.
- `GET /hcmRestApi/CandidateExperience/en/siteSettings/{siteNumber}`: site number, languages, cookie-consent config.
- `GET /hcmRestApi/CandidateExperience/translations?language=en`: every UI string.
- `GET /hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{req}",siteNumber={site}`.

**REST headers in the bundle**
- `Ora-Irc-Access-Code`: the session after the email step.
- `Ora-Irc-Validation-Mode: Y|P`: validate without saving.
- `Ora-Irc-Rest-Action`: an action label per endpoint. For example, `/recruitingCEVerificationTokens` maps to
  `"{Mode}:EMAIL"`, `/recruitingCEJobApplicationDrafts` to `CE_JA_DRAFT`, and `/recruitingCECandidateResumeParsers`
  to `CE_RESUME_PARSE`.
- `Ora-Irc-Language` and `Ora-Irc-Oauth-Token`.

**Drafts.** `recruitingCEJobApplicationDrafts` autosaves. The UI shows "Saving" / "Saved"
(`apply-flow.drafts.progress-indicator.*`).

**CDN.** Responses carry an `akgrn` header (Akamai edge). That is all I saw about Akamai. No bot cookie such as
`_abck` was set.

---

## 3. Fields and widgets (bundle component registry; widget behaviour read from source)

### Element types
The form builder (`Ne.create`) creates checkbox, text, textarea, phone, email, select, pin and file elements.

| Component | What it renders | Engine note |
|---|---|---|
| `cx-select-form-element` | If `options ≤ 10` and no LOV: React **`cx-select-pills`**, a row of pill buttons, single select. Otherwise React **`cx-select`**, a dropdown. | Yes/No and short lists are **pills, not a listbox**. Field container: `[data-qa="{element.name}"]`. |
| `cx-autosuggest-form-element` | React `single-select-combobox` with an async `getOptions`. Placeholder "Start typing to see suggestions". | Search-first: type, wait, then choose. |
| `geo-hierarchy-*-form-element(-v2)` | Address autosuggest / select driven by `recruitingCEAddressFormats/<COUNTRY>`. | The address shape depends on the country. Choose the country first. |
| `cx-datepicker-form-element` | **Three selects**: `day-{id}`, `month-{id}`, `year-{id}`. | "You need to enter the whole date otherwise the date won't be saved." (`validators.incomplete-date-warning-message`). |
| `cx-phone-form-element` | A country-code `prefixField` dropdown plus a number. Options load from a service. The code also calls `navigator.permissions.query({name:"geolocation"})` to pre-pick the local code. | In headless mode, geolocation is not granted, so the prefix is not pre-picked. Set the country code explicitly. "Enter a valid number." (`validators.invalid-phone`). |
| `pin-form-element` | **6 single-digit segment inputs**. Each keeps only its first digit and advances focus. Backspace or Delete clears a digit and moves back. **Paste fills all 6 only if exactly 6 characters are pasted.** The field's value is set only when all 6 digits are present. | Type the digits one per segment, or paste exactly 6. Accessible names: "Enter verification code digit {N} of six." |
| `file-form-element`, `attachment-upload-button`, `resume-upload-button`, `document-types-*`, `misc-attachments` | Uploads. | "Add a file not larger than {FILESIZELIMIT} MB." / "Add a file in these file formats: {ALLOWED_EXTENSIONS}." / "Wait for the file to be uploaded before submitting your job application." |
| `profile-items` / `tile-profile-items` | Experience, education and skill rows as tiles: `data-profile-item-tile="true"`, `data-profile-item-invalid`. | Invalid imported rows must be opened with Edit, fixed and Saved (the engine already does this). |
| `apply-flow-e-signature` | E-signature (full name). | Error `ORA_IRC_ESIGN_INVALID` dispatches `invalidESignature`. |

### Honeypot (observed live; bundle)
- **The control.** `input#honey-pot-1`, `name="honey-pot"`, label "honeypot", `aria-hidden="true"`, `tabindex="-1"`.
  Its row is `div.input-row.input-row--invisible`.
- **Playwright sees it as visible.** The row's computed height is **0px**, but the input itself reports a
  **183×38 rect** with `visibility` visible and opacity 1.
- **How it reaches the server.** The value is sent as `control` in `POST /recruitingCEVerificationTokens`.
- **Inferred:** any non-empty value marks the candidate as a bot.

---

## 4. Validation (where it appears and its exact wording)

**Inline.** Inline errors appear under each field in `<p class="input-row__validation">`. It has `role="alert"` only
while focused, and the row gets `input-row--invalid`. Wording:
- "This info is required." (`validators.field-is-required`)
- "The {LABEL} field is required."
- "Enter a valid number."
- "Enter {FIELDNAME} in the format: {EMAIL_FORMAT}."
- "The country code isn't valid."
- "The postal code isn't valid."
- "Enter a date before today."
- "The end date {ENDDATE} must be later than the start date {STARTDATE}."
- "Enter a maximum of {MAX_VALUE} characters."
- "Select at least {MINSELECTION} answers."
- "You need to agree to the terms and conditions."

**Submit-time fixer**
- "You have {TOTAL_ERROR_COUNT} issues that need to be fixed before you can submit your application." or "You have 1
  issue that needs to be fixed before you can submit your application."
- Navigation buttons: "Go to Next Issue" and "View Error: {NUMBER_OF_ERRORS}".
- Page-level messages: "You need to add or modify some info before submitting your job application." / "You must fix
  missing or incorrect fields to complete your job application." / "Error on the {PAGE_TITLE} page".

**Duplicates.** "This email address is already used. You can log in using that email address to reuse your data or,
you can enter a different email address." The same message exists for a phone number.

**Server errors** become **toasts** (`Ki.A.error`):
- `manageApplicationCreationErrors` maps the failure to one of:
  - "Your application couldn't be submitted. Try again." (`apply-flow.submit-errors.unhandled-error`);
  - "You can't submit your application because this job is no longer available.";
  - "Your attachments couldn't be uploaded. Review your job application and try again.";
  - "Your attachment couldn't be uploaded. Verify the file extension and try again.".
- 400 bodies are turned into "<Section title>: <server message>" (see REA_FINDINGS 10-17).

**Verification code**
- "The code isn't valid. Enter a valid code."
- "You reached the maximum number of attempts. Try again in 30 minutes." with header "Too Many Attempts. Try Again
  Later."
- "You can ask for a new code in {RESEND_COUNTER}", then "You can send a new code now.", "Send New Code" and "A new
  code was sent to your email address."

**Session and status**
- "Your page has expired." with a "Resume Application" button (`apply-flow.section-token-expired`).
- "Your session has expired."
- "You already applied for this job." (`general.error-already-applied.header`; also the `already-applied` template).

**A11y live region (bundle).** `apply-flow-accessibility-messages` renders an always-present hidden
`div.oj-helper-hidden-accessible[role=alert]`. An engine that reads every `[role=alert]` as an error will read noise.

---

## 5. Bot protection

**hCaptcha, invisible, on some tenants only**
- **Config (observed).** `globalSettings` carries a top-level
  `"captcha":{"provider":"hCAPTCHA","settings":{"service_url":"https://js.hcaptcha.com/1/api.js","site_key":…}}`.
  - Honeywell: site key `0d6e3fd2-1cc2-4db2-886a-9a0ea98a4dc6`.
  - JPMC: site key `cb9805ec-cc24-4e02-8978-d9a8a58c515e`.
  - Ford, SAIC, Uber, Oracle, Goldman and Fortinet: `captcha` is **null**.
  - These are exactly the two tenants with captcha events in production.
- **Where it loads (observed).** On `/apply/email` the app loads
  `js.hcaptcha.com/1/api.js?hl=en&onload=onCaptchaLoad&render=explicit`, calls
  `POST api.hcaptcha.com/checksiteconfig?…&sitekey=…&sc=1&swa=1&spst=1`, and fetches `hsw.js`. It renders
  `<div data-size="invisible" data-id="hCaptcha-instance">` inside `captcha.captcha-email-verification`.
- **When it gates (bundle).** NEXT on the email step calls `beforeCaptchaVerify()`, which calls
  `hcaptcha.execute(id)`. The callback `afterCaptchaVerify()` reads `getResponse(id)` into `captchaToken`, and only
  then calls `verify()`, which creates the token.
  - The submit-time "profile created in another tab" dialog also gates its Continue button through
    `beforeCaptchaVerify` (`captcha.apply-flow-summary-captcha`).
  - **Inferred:** when hCaptcha's risk score is high, execute escalates to an image challenge. That matches
    production: "Image challenge needs another round" ×10, "Same-IP hCaptcha not completed" ×10+5+5, "2Captcha:
    SOLVER_REJECTED".

**Honeypot.** See §3.

**Nothing else seen.** No Cloudflare, DataDome, PerimeterX or SEON was observed. The CDN is Akamai, as noted above.

---

## 6. Submit (bundle: `_saveCandidate`, `_onCandidateSubmitted`, `_postProcessApplication`, `redirectToCSS`)

### Sequence
1. **Submit.** The click calls `_saveCandidate()`. That shows the progress overlay and the **submission checklist
   dialog** (`apply-flow-submission-checklist`: `minimal-dialog`, `role="alert"`, an `oj-progress-bar`).
   - Its header is "**You're all set, {first name}!**" (`quick-apply-flow.summary-screen.summary-title`; "You're all
     set!" when there is no name). It shows when the standard apply flow is enabled.
   - The steps are "Creating candidate info", "Saving candidate info" and "**Saving job application info**", plus
     "Confirming job application" (`apply-flow.submit-progress.*`).
   - Overlay headers: "Your application is being submitted" / "Your profile information is being saved".
   - **This dialog is progress, not a receipt.**
2. **Writes.** `saveCandidateAndApplication` writes the candidate to `/recruitingCECandidates` and then sends
   `POST /recruitingCEJobApplications` with `action:"CREATE"`. *Inferred:* this is the moment the application exists
   server-side.
3. **Branch on `isKnownCandidate()`**, which means the PIN was verified at the start or the cookie is known:
   - **Known.** `_postProcessApplication()` sends `POST /recruitingCEJobApplications` with `action:"CONFIRM"`.
     `redirectToCSS()` then routes to **`/my-profile`** ("My Applications") and shows a success toast:
     - "**Thank you for your job application.**" (`candidate-self-service.returning-candidate-confirm-message`);
     - or, with an assessment or tax-credit partner link: "Thank you for submitting your job application. You now
       need to provide additional info to be considered for this position.".
     - If CONFIRM fails, an *error-styled* toast says "**We received your job application for {JOB_TITLE}. Note that
       you still need to confirm your job application.**".
     - If the job closed meanwhile: "We couldn't confirm your application because this job is no longer available.
       …".
   - **Unknown.** `_showPinVerification()` shows the template `pin-code-required` with header "**You're almost
     done**" (`apply-flow.thank-you-dialog.thank-you-for-application-header`) and the 6-digit PIN: "The verification
     code was sent to this email address: {EMAIL_ADDRESS}. When you get the code, type the code into the field to
     confirm your identity and complete your job application."
     - `onPinValid()` then runs CONFIRM and routes to `/my-profile`.
     - **Until the code is entered, the application exists as "Unconfirmed"** (`tile.status-unconfirmed`). The
       browser also tracks it: `addUnverifiedCandidateApplication` and a per-browser unverified-application limit
       (`setHasExceededUnverifiedApplicationsLimit`).

### My Applications tiles (strings)
- Page titles: "My Applications", "Active Job Applications", "Inactive Job Applications".
- "Applied on {SUBMISSION_DATE}".
- "Status" values: "Under Consideration", "Unconfirmed", "Not Retained", "Offer Accepted", plus tenant public statuses.
  The 10-18 diagnosis saw "Application/Resume Received" at Texas Instruments.
- "You need to confirm this job application." with a "Confirm" button.
- "You have {APPLICATIONS_COUNT} unconfirmed job applications." with a "Confirm All" button.

### HTTP 400 at submit is normal
- In the bundle, validation-mode calls (`POST /recruitingCECandidates` with header `Ora-Irc-Validation-Mode: Y`) treat
  **HTTP 400 as success** when the body contains "IRC-PII Validation Successful (IRC--1" or "IRC-Candidate Citizenship
  Validation Successful". `ORA_IRC_CAND_EXISTS` is also accepted.
- `POST /recruitingCERegulatoryResponses` uses `Ora-Irc-Validation-Mode: P`.
- *Inferred:* these explain the "Employer returned HTTP 400 during submission" events, of which there are 62. They
  appear in every confirmed run 1 s after commit, for example Ford, SAIC and Honeywell on 10-18.

### Failure codes in the bundle
- `ORA_JOB_NOT_AVAILABLE`, `ORA_IRC_ESIGN_INVALID`, `ORA_IRC_DUPLICATE_SUBMISSION`. The last shows the
  `already-applied` template.
- The "candidate already created" dialog: "Your application couldn't be submitted." / "You already created your
  profile in another browser tab. To resume your job application, you need to confirm your identity by entering the
  verification code that we will send you." Its Continue button is `#applyFlowSummaryCandidateExists`.

---

## 7. Engine risks

1. **"You're all set, {name}!" is a progress modal**, shown while "Saving job application info". The real end states
   are:
   - `/my-profile` plus the toast "Thank you for your job application.";
   - the "You're almost done" PIN dialog;
   - an error toast.

   *Production:* EXL and Ford were recorded as unknown while still saving (10-18 diagnosis).
2. **"You're almost done" means submitted but unconfirmed.** Leaving there produces an Unconfirmed application.
   Retrying the job later hits "You previously applied for job requisition {REQUISITION_NAME} on {SUBMISSION_DATE}.
   Do you want to confirm your job application?" or "You already applied for this job.".
3. **All tenants share one verification sender.** *Observed in mail_rules:* every Oracle tenant's rule uses
   `sender_domains ["oraclecloud.com","oracle.com"]`. A per-sender lock therefore serialises all Oracle runs.
   *Production:* 57 runs finished with "Another application kept the same email sender busy; nothing was sent."
   - **Update 10-18:** production now makes Oracle starts *wait* while another job holds the sender. That removes
     the early-failure mode.
   - **What still matters, from §6:** the code is requested at two different moments.
     - Known candidates get the start-of-flow "Confirm Your Identity" PIN, right after the email step. A wait there
       is harmless.
     - New tenants get "You're almost done" **after** `CREATE`. If the sender lock is first contended there, the
       application sits Unconfirmed while the run waits.
   - So for a tenant where our email is new, hold the sender lock **before clicking Submit**, and keep it until the
     PIN is entered. Also make sure lock waiting does not consume the run's deadline.
4. **hCaptcha is predictable per tenant.** Read `globalSettings.captcha` before opening the browser. Only Honeywell and
   JPMC have it today.
5. **The honeypot is "visible" to Playwright.** `#honey-pot-1` has a 183×38 rect inside a 0-height row. Exclude it
   by `aria-hidden=true`, `tabindex=-1`, `.input-row--invisible`, `name=honey-pot` or label "honeypot".
6. **Expect HTTP 400 on validation-mode POSTs.** Don't classify any 4xx at commit as failure without checking the
   request header `Ora-Irc-Validation-Mode` and the body ("Validation Successful").
7. **Pills vs dropdowns.** The same `cx-select-form-element` renders pills for 10 options or fewer and a React
   dropdown above that. Detect per field.
8. **Dates are three selects**, and partial dates are not saved.
9. **The PIN is 6 segments.** A paste must be exactly 6 characters. Typing one string into the first box keeps one
   digit.
10. **Route reopen.** Reopening `/apply/section/N` after a tab cleanup loses in-memory state.
    - *Inferred* risk: "Your page has expired." with "Resume Application", or draft restore.
    - *Production:* "Reopened the saved application route after inactive-tab cleanup" ×80.
11. **Imported history rows** can be invalid (`data-profile-item-invalid="true"`). Submit then lands in the fixer
    ("You have N issues…").
12. **A hidden `[role=alert]` live region always exists** (`apply-flow-accessibility-messages`). Don't treat it as an
    error.
13. **Maintenance.** *Production:* "Oracle Cloud Fusion site is showing a scheduled maintenance page with no
    application controls". Treat it as a retry-later outcome, not a failure.
14. **Cookie banner.** A full-width "ACCEPT ALL / REJECT ALL / MANAGE PREFERENCES" banner sits at the bottom (Honeywell
    `cookieConsent`). It didn't block "APPLY NOW" here, but it overlaps lower controls.

---

## Engine checklist (testable)

**Before opening the browser**
- [ ] `GET {origin}/hcmRestApi/CandidateExperience/globalSettings`. If `captcha.provider == "hCAPTCHA"`, schedule the
      job as a captcha tenant: budget for an image challenge and do not burn the shared-sender slot first. Expect
      Honeywell and JPMC to be true and Ford, SAIC, Uber, Oracle, Goldman and Fortinet to be null.
- [ ] `GET …/recruitingCEApplyFlows?finder=findByRequisitionNumber;RequisitionNumber="{req}"&expand=sections.pages.blocks&onlyData=true`.
      Know the sections and blocks up front, for example whether DIVERSITY, DISABILITY or VETERAN exist and whether
      DQ_QUESTIONS is present.

**Email step**
- [ ] The URL ends with `/apply/email`, and the page shows `input#primary-email-0` and `#legal-disclaimer-checkbox`.
      Tick the box by clicking its label, then confirm `checked === true`.
- [ ] `#honey-pot-1` / `[name=honey-pot]` is empty at NEXT. A unit test should fail if the filler ever writes there.
- [ ] After NEXT, exactly one `POST …/recruitingCEVerificationTokens` follows. For captcha tenants the body has a
      non-empty `captchaToken`.

**Code steps**
- [ ] Sender lock (shared `oraclecloud.com`/`oracle.com` sender):
  - For a known-candidate tenant (a code arrives right after the email step), acquire it before email NEXT.
  - For a new tenant (no code at the start), acquire it **before Submit** and hold it through "You're almost done".
  - Time spent waiting for the lock must not count against the run's deadline.
- [ ] If the next screen's header is "Confirm Your Identity", it is the start-of-flow PIN for a known candidate. Six
      inputs are labelled "Enter verification code digit N of six." After entering the code, the form opens
      prefilled.
- [ ] On "The code isn't valid. Enter a valid code.", stop after 2 tries. The cap is "You reached the maximum number
      of attempts. Try again in 30 minutes.".

**Form**
- [ ] Before filling, wait until the block metadata responses have returned 200 (`recruitingCEQuestions`,
      `recruitingCERegulatoryConfigs`, `recruitingCEAddressFormats/…`). Use them as the option source.
- [ ] Choose the country before any address field. Phone needs an explicit country-code choice.

**Submit**
- [ ] After clicking Submit, wait **up to 90 s**. "You're all set, {name}!" with "Saving job application info" may
      stay for tens of seconds. That is progress, not a receipt.
- [ ] Success means `location.pathname` ends with `/my-profile` **and** the toast "Thank you for your job application."
      (or the "provide additional info" variant) appears. Alternatively, a tile shows "Applied on {date}" for this job.
- [ ] If "You're almost done" appears, the run must enter the email-code wait, **not** finish. Entering the 6 digits
      must lead to `/my-profile` and the success toast. Abandoning leaves an "Unconfirmed" application.
- [ ] Treat a 400 on a request with `Ora-Irc-Validation-Mode: Y` whose body contains "Validation Successful" as a
      pass, never as a submission error. Log the endpoint path of every 4xx.
- [ ] The toast "We received your job application for {JOB_TITLE}. Note that you still need to confirm your job
      application." means submitted but unconfirmed. Record it as that, not as a failure.
- [ ] The text "You have N issues that need to be fixed before you can submit your application." means a validation
      rejection. Go to the fixer ("Go to Next Issue").
