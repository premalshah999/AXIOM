# Ashby (jobs.ashbyhq.com) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9342), never the worker Chrome. Nothing was typed and
nothing was submitted.

**Page probed**
- Wayve, `https://jobs.ashbyhq.com/wayve/68647f59-7548-4f34-ae2e-80e6cc944f7a/application`. This is a UK-style form:
  Personal Information, Location and Eligibility, and Wayve UK Demographic Questions.
- Failure context comes from production runs, listed below.

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_c312b331b589960c38c3d8aaa627c8ef728d052fceed237c1ca892b9a7136305` | inspect-web-page on the Wayve application, with scripts, JSON shapes and accessibility |
| `ev_68f41af6634cc64ad9295916117147ba46b6297eb0d54b9c325b0dc5766cc9f6` | export-web-scripts. The app bundle is `https://cdn.ashbyprd.com/frontend_non_user/16b35231c2987e4ba1c5d9a0e165a2d03c91a468/assets/index-D0HZ41J-.js` (4.0 MB). GraphQL documents are embedded as AST JSON |

**Other sources**
- A Playwright network log with the GraphQL request and response bodies.
- Production runs and events, read only:
  - Trace Labs `2a823ca5cc9e4e40b41d1013368b87c9`;
  - Snowflake `ec2eeba71ff34bf3a73b96746f633f83`;
  - DailyPay `74edac57f75e4454aede43e30ba21f87`;
  - captcha events across all Ashby runs.

**Conventions.** **Observed** means seen live, in a GraphQL body or in the bundle. **Inferred** means my reading.

---

## 1. Workflow
- **Routes.** The job is `/{org}/{jobPostingId}` and the form is `/{org}/{jobPostingId}/application`. Both are one
  page. There is no account, sign-in or email verification for applying.
- **Every interaction is GraphQL:** `POST https://jobs.ashbyhq.com/api/non-user-graphql?op={Operation}`. Observed at
  load:
  - `ApiOrganizationFromHostedJobsPageName` (twice);
  - `ApiJobPosting {organizationHostedJobsPageName, jobPostingId}`;
  - `ApiAutocompleteGeoLocation {text:"", locationTypes:["Country","Region","City"]}`;
  - **`ApiSetFormValue`**.
- **The form state lives on the server.**
  - Every value change is sent as `mutation ApiSetFormValue {organizationHostedJobsPageName, formRenderIdentifier, path:<field uuid>, value, formDefinitionIdentifier}`.
    For example, at load it was sent with `path:"05fa59e9-…"` (Notice Period) and `value:null`.
  - The response returns the whole form: `formControls` (the "Submit" control), `errorMessages`, `formErrors` and
    `sections` (`fieldEntries`, with conditional visibility).
  - Files use `ApiCreateFileUploadHandle`, `ApiSetFormValueToFile`, `ApiAddManyFilesToFormValue` and
    `ApiRemoveFileFromFormValue`.
- **Resume autofill.** `ApiAutofillApplicationFormWithUploadedResume` runs from the **"Autofill from resume"**
  uploader at the top: "Upload your resume here to autofill key application fields."
- **Submit.** `mutation ApiSubmitSingleApplicationFormAction` (variables include `organizationHostedJobsPageName`,
  `jobPostingId`, the form identifiers and `recaptchaToken`). A sibling operation is `ApiSubmitMultipleFormsAction`.
- **Other operations seen in the bundle:**
  - `ApiSearchSchoolByCanonicalName` (a school typeahead);
  - `ApiSubmitCandidateTextingConsent`, `ApiSubmitCandidateWhatsAppConsent`;
  - `ApiAutomatedProcessingRecaptchaActions` and `ApiGetAutomatedProcessingLegalText` (an AI-processing consent);
  - `ApiRecordCookieConsent`.

---

## 2. Page tech (observed)
- React SPA with `window.__appData` and Apollo-style GraphQL. CSS modules (`_fieldEntry_1e3gg_28`) sit alongside
  stable **`ashby-application-form-*`** class names.
- **There is no `<form>` element.** Submit is a click handler.
- No iframes except reCAPTCHA. No shadow DOM. No cookie banner seen on Wayve. `ApiRecordCookieConsent` exists for
  orgs that have one.

---

## 3. Fields and widgets (observed)

| Widget | DOM | Notes |
|---|---|---|
| Field container | `div.ashby-application-form-field-entry[data-field-path="{uuid}"][data-field-entry-id="{renderId}_{uuid}"]`. Label: `label.ashby-application-form-question-title[for={uuid}]`; required adds the class `_required_…` | Key fields by `data-field-path` |
| System fields | `input#_systemfield_name` ("Preferred Full Name"), `#_systemfield_email` (type=email), `#_systemfield_resume` (type=file) | `name` = `id` |
| Custom text | `input[id=name={uuid}]`, with `type` text, url or number and placeholder "Type here..." | e.g. "Salary Expectations?", "Notice Period (months)" (number) |
| **Yes / No** | `div.ashby-application-form-input-yesno` > `button.ashby-application-form-input-yesno-option[data-option="yes"|"no"][aria-pressed="false|true"]`, plus a hidden `input[type=checkbox][tabindex=-1]` | **Buttons, not radios.** Read the state from `aria-pressed` |
| Radio | `input[type=radio][id="{uuid}_…-labeled-radio-{n}"][name="{uuid}_…"]` | Demographic sets include "Prefer not to say" |
| Checkbox | `input[type=checkbox][id="{uuid}__systemfield_data_consent_ack-labeled-checkbox-0"][name="I agree"]` | Consent |
| Combobox | `input[role=combobox][aria-haspopup=listbox][aria-autocomplete=list][placeholder="Start typing..."]` with **no id, no name and no aria-controls** | Location, age group and religion on Wayve. Location is backed by `ApiAutocompleteGeoLocation` |
| Resume | `#_systemfield_resume[accept="application/pdf,.pdf,application/msword,.doc,…docx,…odt,application/rtf,.rtf,text/plain,.txt,text/markdown,.md"]` | **A second file input** (`tabindex=-1`) belongs to the autofill uploader |
| Submit | `button[type=submit]` "**Submit Application**" (class `_submitButton_…`), outside any form | Exactly one |

---

## 4. Validation
- **Server-side.** `ApiSetFormValue` responses carry `errorMessages` and `formErrors`. Inline errors render per field.
- *Production, DailyPay:*
  - "Employer rejected incomplete fields" for "What Are Your Base Salary Expectations?" and "What State or Territory
    Do You Reside In?";
  - then "Rejected field could not be reasserted (ValueError)";
  - then a second commit, which ended `submission_unknown`.
  - *Inferred:* the DOM showed a value that the server-side form state did not have, because no `ApiSetFormValue`
    landed for that path.
- **Error text helpers (bundle):**
  - "There was a problem verifying this submission with CAPTCHA. Please try turning off your ad blocker or try again
    later.";
  - "There was a problem with the network connection.";
  - fallback "Something went wrong - sorry. We'…".
  - Server messages come from `graphQLErrors[].extensions.message`, or else from `ashbyErrorType`.

---

## 5. Bot protection
- **reCAPTCHA Enterprise, score-based (v3-style).**
  - Script: `https://www.recaptcha.net/recaptcha/enterprise.js?render=6LezdY0tAAAAAEnollNLCAI0z1VDGwM7AzGrU4XZ&onload=onLoadReCaptcha`.
    `render=<siteKey>` means programmatic execute. The anchor iframe uses `size=invisible`.
  - **Not a v2 checkbox.**
- **How the token is made (bundle):**
  - `grecaptcha.enterprise.execute(siteKey, {action})`;
  - the token is **prefixed `ENT===`**, or `UNIVERSAL_ENT===` for universal keys;
  - if `grecaptcha` is missing, the token is the literal `"recaptcha_dummy_token"`.
- **Actions** include `JobApply: "job_apply"`, `magic_link`, `autofill_application_form`, `schedule_meeting` and
  others.
- **Score thresholds** in the bundle: None 0, Low 0.3, Medium 0.5, High 0.9.

---

## 6. Submit and outcomes (bundle; observed DOM classes)
- **Success.** `.ashby-application-form-success-container` holds a box with `role="status"` and
  `aria-live="polite"`. Inside are h2 **"Success"** and a paragraph with the org's
  `theme.applicationSubmittedSuccessMessage`, or the default **"Your application was successfully submitted. We'll
  contact you if there are next steps."**
- **Failure.** `.ashby-application-form-failure-container` with h2 **"We couldn't submit your application"**, plus a
  message.
  - **If any GraphQL error has `extensions.ashbyErrorType === "RECAPTCHA_SCORE_BELOW_THRESHOLD"`**, the page renders
    the "flagged" panel. It has a `role="alert"` summary and these steps:
    - "Try these steps";
    - "**Legitimate applications are occasionally flagged by mistake. Try these in order:**";
    - "Turn off your VPN or proxy";
    - "Pause browser extensions";
    - "Try another browser or device";
    - "Switch networks";
    - "Still blocked?".
  - Otherwise it renders a `role="alert" aria-live="assertive"` box.

### Why production saw "explicitly rejected" (observed plus inferred)
- **Trace Labs** (`2a823ca5…`): "CAPTCHA solver requested" with provider `api.capsolver.com`, type
  **`ReCaptchaV2TaskProxyLess`**. Then "Token delivered; waiting for the website", then commit, then "**Employer
  website explicitly rejected the submission**".
- **Snowflake** (`ec2eeba7…`) followed the same sequence. It also logged "No offered choices were readable" for
  "Location" twice, which is the search-only combobox.
- Across Ashby, there are 20 "CAPTCHA solver requested" events and 17 "Token delivered; waiting for the website".
- *Inferred:*
  - Ashby's key is score-based Enterprise, with action `job_apply`.
  - A **v2** proxyless token is the wrong token type for that key, and has no matching action.
  - Google's assessment fails, and Ashby answers `RECAPTCHA_SCORE_BELOW_THRESHOLD`. That is the "flagged" panel, which
    the engine records as an explicit rejection.
  - The page's own `execute` would produce an `ENT===`-prefixed v3 token without any solver. A "solved" v2 token
    likely makes things worse.

---

## 7. Engine risks
1. **The wrong captcha type.** Don't send Ashby to a v2 solver. Let the page's own
   `grecaptcha.enterprise.execute(..., {action:"job_apply"})` run in a normal, headful profile. If a solver is ever
   used, it must be a reCAPTCHA **v3 Enterprise** task with pageAction `job_apply`, and the token must go through the
   page's execute path so it gets the `ENT===` prefix.
2. **The "flagged" outcome is a bot-score rejection**, not a validation error. Classify it as `captcha_blocked` and back
   off. Do not "repair fields" and resubmit at once.
3. **Server-side form state.** A value counts only after its `ApiSetFormValue` returns. DOM-only writes (setting
   `.value` without React's change event) leave the server state empty, which leads to "rejected incomplete fields".
4. **Yes/No are buttons** with `aria-pressed`. Radio and checkbox logic will miss them.
5. **Comboboxes have no `aria-controls`.** The owned listbox can't be found by id. Use the open popup that appears after
   the click. Location is search-only through `ApiAutocompleteGeoLocation`.
6. **Two file inputs.** Uploading into the **autofill uploader** reruns `ApiAutofillApplicationFormWithUploadedResume`
   and can overwrite answers already given. Upload the resume to `#_systemfield_resume`.
7. **There is no `<form>`.** "Submit Application" is a click handler. A form-based submit resolver must accept the
   single `button[type=submit]`.
8. **The success text can be overridden per org.** Use the container classes, not the default sentence.

---

## Engine checklist (testable)
- [ ] Ashby runs request **no** `ReCaptchaV2TaskProxyLess` solver task. A unit test on the captcha router: the key is
      loaded via `enterprise.js?render=` and no checkbox anchor exists, so the result is "score-based, not solvable as
      v2".
- [ ] After every field write, wait for the matching `POST …non-user-graphql?op=ApiSetFormValue` response whose
      variable `path` equals the field's `data-field-path`. Then check its `formErrors` for that path.
- [ ] Yes/No fields are set by clicking `button[data-option=yes|no]`. The read-back is `aria-pressed="true"`.
- [ ] Location: type, wait for `ApiAutocompleteGeoLocation` with a non-empty `text`, then choose from the popup that
      appeared after typing.
- [ ] The resume goes to `#_systemfield_resume` only, unless autofill is deliberately used **before** any answers.
- [ ] Submit clicks the single `button[type=submit]` "Submit Application". Expect `ApiSubmitSingleApplicationFormAction`.
      - Success means `.ashby-application-form-success-container [role=status]` with h2 "Success".
      - Failure means `.ashby-application-form-failure-container`. Record the h2 and the message.
      - If the failure panel contains "Legitimate applications are occasionally flagged by mistake", record
        `RECAPTCHA_SCORE_BELOW_THRESHOLD` (bot block). It must not be classed as a validation failure.
