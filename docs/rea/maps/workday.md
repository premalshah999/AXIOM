# Workday Recruiting (myworkdayjobs.com / myworkdaysite.com) — behaviour map (light pass)

Date: 2026-10-18. Read-only. Nothing was submitted, no account was created, and nothing was typed.

**Tenants**
- Cisco: `cisco.wd5.myworkdayjobs.com/cisco_careers`, job `Software-Engineer_2021849`. Walked to Create Account.
- RTX: `globalhr.wd5.myworkdayjobs.com/rec_rtx_ext_gateway`, job `Platform-Engineer--Remote-_01882296`. Walked to the
  sign-in chooser.

The coordinator called the form engine "already known", so this map adds what is new and points to REA_FINDINGS
(10-17) for the rest.

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_ae8a4a149d9deca75ebe3824892ba05ba52d13cda07158015c86ac2ca5d502ad` | inspect-web-page, Cisco "Create Account" step (accessibility plus JSON shapes) |
| `ev_cf201cb5…` | Prior, from REA_FINDINGS 10-17: `cx-jobs.min.js` from U.S. Bank, the server-metadata form renderer |

**Other sources**
- Network capture through Playwright, my own Chrome on 9341.
- Public i18n bundles.
- Production runs and events.

**Conventions.** **Observed** means seen live or in JSON/source. **Inferred** means my reading.

---

## 1. Workflow

### Routes (observed)

| Step | URL / control |
|---|---|
| Job page | `https://{tenant}.wd{N}.myworkdayjobs.com/{site}/job/{location}/{title}_{reqId}` |
| Apply button | `[data-automation-id=adventureButton]` "Apply", linking to `…/job/…/apply` |
| Choice modal | `autofillWithResume` → `/apply/autofillWithResume`; `applyManually` → `/apply/applyManually`; `useMyLastApplication` → `/apply/useMyLastApplication` |

### Steps come from JSON (observed)
- `GET /wday/calypso/cxs/jobapplication/{tenant}/jobpostings/{jobId}/applyflowpages?applyDetail=applyManually`
  returns `{total:6, data:[{id, descriptor, sections:[{id, hidden, required, label}]}]}`.
- Cisco and RTX both have six steps: "Create Account/Sign In", "My Information", "My Experience", "Application
  Questions", "Voluntary Disclosures", "Review".

### Account step (observed)
- **The sign-in screen depends on `authproviders`.**
  `GET /wday/calypso/cxs/account/{tenant}/{site}/authproviders?successRedirectUri=…`:
  - **Cisco** returns `[{"authProvider":"EMAIL_PASSWORD"}]`. The page goes straight to the Create Account form:
    `email`, `password`, `verifyPassword`, `createAccountSubmitButton`, and the links `signInLink` and
    `forgotPasswordLink`.
  - **RTX** returns `GOOGLE`, `LINKEDIN` and `EMAIL_PASSWORD`. The page shows a **chooser**:
    `GoogleSignInButton` "Sign in with Google", `LinkedInSignInButton` "Sign in with LinkedIn", then "OR",
    `SignInWithEmailButton` "Sign in with email".
  - **This changes the conclusion:** the chooser is predictable from JSON before rendering. Production now clicks
    `SignInWithEmailButton`, which agrees.
  - The Google and LinkedIn buttons redirect to
    `https://wd5-identity.myworkday.com/wday/authgwy/{tenant}/upc/api/oidc/op/authorize?…acr_values=urn:upc:delegated:auth:google|linkedin`,
    a **different origin**.
- **Consent and password rules.** `GET /wday/calypso/cxs/account/{tenant}/{site}/account/configuration` returns:
  - `consentInfo`. Cisco has only an `introMessage` ("By registering, I acknowledge and agree to…"), so **no
    checkbox**. RTX has `message` plus **`label` "Yes, I have read and consent to the terms and conditions"**, so
    **a consent checkbox**, and an `ssoConsentMessage`.
  - `passwordRules`. Cisco and RTX are the same: `MUST_LOWERCASE`, `MUST_UPPERCASE`, `MUST_ALPHABET`, `MUST_NUMERIC`,
    `MUST_SPECIAL` and `MIN_LENGTH 8`. Cisco shows them as "Password Requirements: A lowercase character / An
    alphabetic character / A special character / A minimum of 8 characters / An uppercase character / A numeric
    character".
- **Activation email (production).** Some tenants need it: "Waiting for the Workday account activation email" ×5 and
  "Workday account activated" ×3 since 10-16. Strings: "Resend Account Verification"; "Still can't sign in? Be sure to
  check your spam folder, your account may need verification".
- **Existing applications.**
  `GET /wday/calypso/cxs/candidatehome/{tenant}/{site}/applications?type=active&jobPostingAnchorId=…` returned
  `{"total":0}` when signed out. *Inferred:* when signed in it is the "already applied" oracle.

### Review and submit
- The step list ends with "Review", and the button is "Submit" (`APPLY.BUTTON.Submit`).

---

## 2. Page tech

**Bundles (observed)**
- React SPAs: `wd5.myworkdaycdn.com/wday/asset/candidate-experience-jobs/cx-jobs.min.js` and
  `…/candidate-experience-apply-flow/2026.40.17/candidate-experience-apply-flow.min.js`.
- Analytics: `wd5.myworkday.com/wday/asset/client-analytics/uxInsights.min.js`.

**Data endpoints (observed)**
- Job and site: `/wday/cxs/{tenant}/{site}/approot`, `/wday/cxs/{tenant}/{site}/job/{path}`, `…/sidebar`,
  `…/userprofile`.
- Apply flow: `/wday/calypso/cxs/jobdetails/…/info`, `…/jobapplication/{tenant}/addressdefinition?country={id}`,
  `…/namedefinition?country={id}`, `…/common/{tenant}/countries`, `…/locales/en_US`, `…/smsenablementstatus`.

**i18n is public JSON**, for example
`https://wd5.myworkdaycdn.com/wday/asset/candidate-experience-apply-flow/2026.40.17/compiled-lang/cxs_apply_flow/en-US.json`.
The bundles `cxs_account_creation`, `generic` and `contact_information` follow the same path.

**Form engine** (REA_FINDINGS 10-17, `cx-jobs.min.js`)
- **Server-driven fields:** `formField-<id>`, with `type`, `required`, `formatMask`, `exitValidation`, `promptUrl`,
  `renderPrompt.{dependentOn,isMultiSelect,isSearchTypeAheadEnabled}`, and `items` for row lists.
- **Prompt options** are complete JSON from `promptUrl`.
- **Lists** are react-virtualized with `overscanRowCount: 10`.
- **TextField** is uncontrolled and blur-committed: it is re-keyed, so it remounts on commit, and mask rewriting
  happens on input.

---

## 3. Fields and widgets

*New this pass:* the account-step controls in §1. Everything else is the 10-17 analysis:
- prompts load from `promptUrl`;
- search-typeahead prompts (school, location) have no useful DOM list until you type;
- masked phone (`formField-countryPhoneCode` plus number);
- row repeaters for work and education;
- uploads use `categoryConfig` / `maxFiles`.

**Upload strings**
- "Upload either DOC, DOCX, HTML, PDF, or TXT file types (5MB max)"
- "The size of the file '{0}' ({1}KB) is greater than the maximum permitted file size ({2}KB)…"
- "The max number of files that can be uploaded is '{0}'"
- "Your document wasn't uploaded because a virus was detected…"
- "Successfully Uploaded!"

**Production signal.** Since 10-16, "Could not commit School or University" ×50. The detail is "No matching option is
offered" from `widgets.py prompt`. That is consistent with a typeahead prompt being read before typing.

---

## 4. Validation (i18n, observed)

**Banners and inline errors**
- Banner: "Errors Found" / "Errors and Alerts Found", with counts "{n} Errors", "1 Error and 1 Alert".
- Inline: "The field {0} is required and must have a value." / "Error: {0}" / "Page Error".
- "Invalid email address format. Please use john@example.com"
- "Invalid Date: {0}"
- A warning that is not an error: "Verify that the field {0} is correctly capitalized because it contains more than 2
  capital letters."

**Account errors**
- "Passwords do not match"
- "Password must include:"
- "Please enter your password"
- "You may have entered the wrong email address or password or your account might be locked."
- "Failed to resend verification email"

**Generic errors**
- "An error occurred on the server. Please try again later."
- "The request contains invalid data or incorrect syntax. Please refresh the page and try again."
- "You do not have permission to access this resource. …"
- "Something went wrong. Try again later."

---

## 5. Bot protection

- **Cloudflare (observed cookies).** `__cf_bm` and `_cfuvid` on `.{tenant}.wd5.myworkdayjobs.com`, and `__cflb` on the
  host. Also set: `wd-browser-id`, `CALYPSO_CSRF_TOKEN`, `PLAY_SESSION`, `wday_vps_cookie`. No challenge page was
  seen.
- **Honeypot (observed).** `input[data-automation-id=beecatcher]` on Create Account. Its accessible name is "Enter
  website. This input is for robots only, do not enter if you're human."
  - Rect 1×0 px, `clip-path: polygon(0 0,0 0,0 0,0 0)`.
  - **No `aria-hidden` and no `tabindex=-1`.** It is in the accessibility tree (not ignored, editable) and reachable
    by Tab.
  - Playwright's `is_visible()` is false because the height is 0.
- **Click filter (observed).** `createAccountSubmitButton` is covered by `div[data-automation-id=click_filter]`.
  `elementFromPoint` at the button's centre returns the filter. Click the filter, or the button with the filter as
  the target.
- **No captcha** was seen on these tenants.

---

## 6. Submit

**Success strings (i18n)**
- "Congratulations!" and "Your application has successfully been submitted." (`SUBMITTED.*`)
- "Your information has been submitted."
- "Submitted Application"

**Production dialogs** (REA_DIAGNOSIS 10-18)
- Adobe: "Congratulations! You've successfully submitted your application".
- Chewy: "Congratulations! We have received your application." It was still fading in at 30 s.
- HPE: "Application Submitted … more tasks: Complete Questionnaire".

**Already applied**
- "You've already applied for this job." (i18n)
- Production receipts: "You applied for this job on".

**Maintenance.** Workday redirects to `https://community.workday.com/maintenance-page?d=5&s=1&e=1` (coordinator;
the repair agent saw `community.workday.com/maintenance-page`). Production also logged "Recovering Workday's explicit
server-error page" ×18 since 10-16.

---

## 7. Engine risks

1. **Sign-in chooser (RTX).** Never click Google or LinkedIn, because they leave the tenant origin for
   `wd5-identity.myworkday.com`. Use `SignInWithEmailButton`. Decide from `authproviders` JSON before rendering.
   - *Inferred:* clicking SSO, or any redirect to the identity host, trips the "account origin does not match this
     employer" guard (production: USC 10-17).
2. **The consent checkbox exists only when `consentInfo.label` is present** (RTX yes, Cisco no). Don't search for a
   checkbox on Cisco-style tenants. Do require one on RTX-style tenants.
3. **The `beecatcher` honeypot is Tab-reachable** and in the accessibility tree. An a11y- or Tab-order-driven filler
   could type into it.
4. **`click_filter` overlays Create Account.** A plain `button.click()` actionability check fails, or a forced click
   misses.
5. **The legalNotice cookie banner (RTX)** is `legalNotice` with `legalNoticeAcceptButton` "Accept Cookies" and
   `legalNoticeDeclineButton` "Decline". *Observed:* it is `position: relative` at the top (1440×129), so it **shifts
   layout rather than overlaying**, and "Apply" was clickable with the banner up.
   - **This changes the conclusion:** it is not a click-blocker. Dismissing it is hygiene, not a requirement.
     Production handles it now.
6. **Typeahead prompts** (school, location) must be search-first. Production has 50 school commit failures.
7. **Blur-commit masked text fields remount** on commit (10-17). Read back from the new node.
8. **Late success dialogs** (Chewy at 30 s) and HPE-style "more tasks" dialogs. Read the dialog before any email wait.
9. **Maintenance and server-error pages.** Classify them as retry-later, not as a field failure.

---

## Engine checklist (testable)

**Account step**
- [ ] Before the account step, `GET …/authproviders`. If it lists more than `EMAIL_PASSWORD`, expect the chooser and
      click `[data-automation-id=SignInWithEmailButton]`. Assert that the URL origin is still the tenant host
      afterwards. Expect Cisco to be email-only and RTX to show the chooser.
- [ ] `GET …/account/configuration`. Generate the password from `passwordRules` (`MIN_LENGTH`, `MUST_*`). Tick a
      consent checkbox only if `consentInfo.label` is present.
- [ ] Never focus or type into `[data-automation-id=beecatcher]`. A unit test should fail on any write to it.
- [ ] Create Account: dispatch the click on `[data-automation-id=click_filter]`, or verify that `elementFromPoint` is
      the filter before clicking.

**Prompts**
- [ ] For prompts with `isSearchTypeAheadEnabled`, type, wait for the `promptUrl`/search response, then choose. Do
      not pre-read the DOM list.

**Submit**
- [ ] After Submit, watch up to 90 s for "Congratulations!", "Your application has successfully been submitted.",
      "Application Submitted" or "Submitted Application". Read any dialog before entering an email wait.
- [ ] If the URL host is `community.workday.com` or the page is Workday's server-error page, end the run as
      retry-later and keep the draft.
