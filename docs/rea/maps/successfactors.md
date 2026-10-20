# SAP SuccessFactors Recruiting (RMK career sites + RCM apply) — behaviour map

Date: 2026-10-18. Read-only.
- **Nothing was submitted** and no account was created. I never clicked `#fbqa_apply`, `#fbclc_createAccountButton`
  or "Sign In".
- **The only text typed** was the filter "United St" into one picklist. I cleared it afterwards and selected nothing
  (`tor__fcustcountry` stayed empty).
- **Password policies** were read through the page's own read-only DWR call.

**Tenants**

| Employer | Career site (RMK) | SF tenant | Flow variant |
|---|---|---|---|
| Capgemini | careers.capgemini.com | `career5.successfactors.eu`, company `capgemitecP3`, req 576849 (RMK id 1448252033) | Embedded: account and application on one page |
| Boehringer Ingelheim | jobs.boehringer-ingelheim.com | `career5.successfactors.eu`, `BoehringerPRD`, req 33178 | Classic sign-in page, then Create Account |
| SAP | careers.sap.com | `career5.successfactors.eu`, `SAP`, req 459078 | Classic, with **email OTP** before the account is created |
| Westinghouse | careers.westinghousenuclear.com (`/WECTEC_Staffing/job/…/1370271500`) | `career4.successfactors.com`, `WestinghouseP` | Classic sign-in page; no OTP |

Production also uses `career4.successfactors.com` (Acuity, Corning, Westinghouse, Hexion) and
`career2.successfactors.eu` (ASSA ABLOY, Cargill).

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_906ba9b83387f949fe1a56971435c3ab747de695af5d5c4ddbea1a33ed20b24f` | inspect-web-page, Capgemini apply page on career5 (282 scripts, accessibility, JSON shapes) |
| `ev_5fd918a3815b534cb3bee151983785120bd183e838a482f4bd419c502934762f` | export-web-scripts of the above. Key files: `ui/rcmcommon/js/RCMPaginatedPicklistSingleSelect_461657a6….js`, `ui/rcmcareer/js/careersite/RCMPwdPolicy_386a8821….js`, `PasswordSmartIndicator_7dab9273….js`, `verp/…/sfReCaptcha_9fdee65b….js`, `rcmQuickApplyLogin_875d06e7….js`, `rcmOTPAuthentication_ec3c8ebb….js` |
| `ev_e9a393cf613bc52d22a923c55411cffc9c39bed4eb42d5faeda2c1d013d42784` | inspect-web-page, SAP "Create an Account" page. Its inline script `scr_0b404d3f5427d88c0a26…` contains the OTP switch |

**Conventions.** **Observed** means seen live or in source. **Inferred** means my reading, not verified end to end.

---

## 1. Workflow

### A. From the career site (RMK) to SF (observed)
1. **Job page.**
   - On an employer domain: `/job/{slug}/{rmkJobId}`.
   - Or on `rmk-map-*.jobs2web.com`.
2. **"Apply".**
   - Capgemini: a `button[aria-haspopup=true]` "Apply now" opens a menu, then `[role=menuitem]` "Apply Now".
   - Boehringer and SAP: a link `href="/talentcommunity/apply/{rmkJobId}/?locale=en_US"`.
3. **XHR.** `POST https://{rmk-host}/services/cas/createpayload/` with
   `{"context":{"action":"apply","locale":"en_US","sourceId":"JATS-{companyId}","jobID":…,"email":"","signedToken":""},…}`.
4. **Hand-off.** A form POST (a document navigation) to
   `https://career{N}.successfactors.{eu|com}/careers?company={companyId}`, with body
   `career_ns=job_application&career_job_req_id={sfReqId}&clientId=jobs2web&jobPipeline=Direct&socialApply=false…`.
   - **Observed:** a direct GET of `/talentcommunity/apply/{id}` without going through the job page redirected to the
     site home (SAP). The handoff needs the createpayload step.

### B. Variant 1: embedded account and application on one page (Capgemini, observed)
The page is "Career Opportunities: Software Engineer (576849)", with "Already a registered user? Please sign in". It
contains, in order:
- Resume and cover-letter upload: "Accepted file types: DOCX, PDF, Image and Text (MSG, PPT and XLS file types are not
  accepted for resume or cover letters)".
- Account fields: `fbclc_userName` (Valid Email), `fbclc_emailConf`, `fbclc_pwd`, `fbclc_pwdConf`.
- Names: `fbclc_fName`, `fbclc_mName`, `fbclc_lName` (maxlength 40).
- Phone: a native select `fbclc_ituCode` (for example "United States (+1)"), plus `fbclc_phoneNumber`.
- 13 screening and EEO questions as paginated picklists (§3).
- Country of Application: `fbclc_country` (native select).
- Notification checkboxes.
- "Privacy Notice:*" link `#dataPrivacyId`.
- **One** submit button: `button#fbqa_apply` "Apply".
- *Inferred:* that one click creates the account and submits the application.

**Existing users.** "Please sign in" calls `openSignInModal()`, which opens a dialog with `#username`, `#password`,
`button#fbqa_signin` "Sign In" and "Forgot your password?".

### C. Variant 2: classic (Boehringer, SAP, observed)
- **Sign-in page.** "Career Opportunities: Sign In", "Already have an account? Enter your email address and password
  (Credentials are case sensitive).", with `#username`, `#password` and "Sign In" (`onclick="return
  validateFields();"`). It ends with "Not a registered user yet? Create an account to apply for our career
  opportunities."
- **"Create an account"** links to `/career?company=…&login_ns=register&career_ns=job_application&career_job_req_id=…`.
  The page is "Career Opportunities: Create an Account" and has:
  - `fbclc_userName`, `fbclc_emailConf`, `fbclc_pwd`, `fbclc_pwdConf`, `fbclc_fName`, `fbclc_lName`;
  - `fbclc_country` (Country/Region of Residence);
  - a required radio `fbclc_searPref`: "I agree that my data can be used: By all recruiters… / Only by recruiters
    that are responsible for requisitions I applied to…";
  - notification checkboxes;
  - "Terms of Use:*" (privacy dialog);
  - `button#fbclc_createAccountButton` "Create Account", with
    `onclick="setField('userMsgReq','false');return submitCreateAccount();"`.

### D. Email OTP before the account exists (SAP, observed in inline source)
- **The switch.** `submitCreateAccount()` is templated per tenant: `if(true){ hideAccountFields(); verifyOtp(); return
  false; } else { setFieldAndShowOverLay(); return true; }`. SAP renders `if(true)`; Boehringer renders `if(false)`.
- **SAP's OTP settings:** `verifyOtp()` uses `maxAllowedOTPAttempts: 3` and `waitingTimeForResendOTP: 120` seconds.
  On `"OtpSuccessful"` it calls `submitCareerForm`.
- **Strings** (`MSGS`, read from the page):
  - "Verify Email Address";
  - "We’ve sent a one-time password to your email address. In case you don’t find this email in your primary inbox,
    please check your spam or bulk email folders.";
  - field "Passcode";
  - "The passcode entered is incorrect. Please try again.";
  - "You can request for a new passcode in {0} minutes.";
  - "Resend passcode";
  - "You’ve exceeded the maximum number of attempts to verify the passcode. Please refresh the page and try again.";
  - "You’ve exceeded the maximum number of attempts to request a new passcode. …".
- **Production:** SAP 10-16, "The page is a one-time passcode (OTP) verification step: a required '* Passcode:'
  field is empty…".

### E. Application, review and submit
- After sign-in or account creation, the full application page loads. I did not observe it, because it needs an
  account. Production events show the same `rcmpaginatedselect` questions as Capgemini's embedded page.
- **Final success (from production receipts):** URL `https://career{N}.successfactors.{eu|com}/portalcareer…`, with
  either:
  - `#success_message` "**Your application has been sent. Thank you!**" (ASSA ABLOY, Capgemini). Other receipts
    quote "Your application has been sent" (Capgemini, Corning, Westinghouse);
  - or tenant text in **`#applyConfirmMsg`**. Cargill: "Process completed  Thank you for your job application. If
    your profile meets the requirements, a member of our team will contact you." Hexion: "Thank you for your
    interest in Hexion. We have received your application and resume. …".
- **Already applied (production):** "You already applied for this position." on `/portalcareer`.

### F. Sign-in vs create-account decision (Westinghouse; observed live 10-18 plus production events)

**How each page identifies itself (observed)**

| Page | Signals |
|---|---|
| Classic sign-in | Title "Career Opportunities: Sign In". Text "Already have an account?" and "Not a registered user yet? Create an account to apply for our career opportunities." Visible `#username` and `#password` inside `#careerform`, a "Sign In" button (`onclick="return validateFields();"`), "Forgot your password?" (`href` contains `login_ns=forgot`) and "Create an account" (`href` contains `login_ns=register`). |
| Create account | Title "Career Opportunities: Create an Account". `#fbclc_createAccountButton`, plus "Already a registered user? Please sign in". |
| Embedded (Capgemini) | `#fbqa_apply` together with the `fbclc_*` account fields on the job page. Signing in goes through the "Please sign in" link, which runs `openSignInModal()` (§3). |

**What "A password is required." means on Westinghouse (observed source)**
- Westinghouse's sign-in page has **one** `[name=password]`: the visible `#password` inside `#careerform`. It has no
  hidden duplicate, unlike the embedded modal.
- `validateFields()` checks `password.value.trim() == ""` **in the browser**. If the field is empty it:
  - shows "A password is required." in `#signIn_PasswordError`;
  - adds a dynamic error to `#dynamicErrorMessages` / `#uiErrorContainer_2`;
  - shows the banner "Please complete all required fields and re-submit.";
  - **returns false, so nothing is sent to the server.**
- An email-only click shows "An email address is required.".
- The same function holds a reCAPTCHA branch, "Confirm you are not a robot:", which is disabled here
  (`isCaptchaDisable = true`).
- **So the saved final screen with "A password is required." proves the engine clicked Sign In with the password box
  empty.** The account and password were never tested.

**Production sequence (events)**

| When | What happened |
|---|---|
| 10-07 | "Signing in with the saved account" ended in "The employer did not accept the saved account credentials" (×2). |
| 10-08 | "Creating an employer account". Then "The Create Account button … failed to execute twice with no page change…". Then "Stuck on the Westinghouse SuccessFactors sign-in page… clicking Sign In repeatedly (3 attempts)". |
| 10-16 04:06 (run `dcd506`) | "Signing in with the saved account", then "Website confirmation captured". **The saved account works.** |
| 10-16 13:30 (run `7fa9da`) | Sign-in, then **17 s later "Creating an employer account"**, then a loop of "Repairing account controls…" and a stall. |
| 10-17 04:57 (run `214fef`) | Sign-in, then **46 s later "Creating an employer account"**. Then "Opened the employer sign-in from its career landing page", "Acknowledged the employer's data privacy statement" and a stall. The repair agent: "…tried to create an account but the password it supplied failed the site's pass[word policy]". |

**Westinghouse's policy (DWR, observed):** `minLength 8, maxLength 18, mixedCase true, alphaNumeric true`. It is
also **printed inline before Create** in `#rcmPwdPolicyAnchor` ("Password must not be longer than 18 characters." …).
On this tenant the rules are visible before Create, not only after a refusal.

**Inferred root cause.** Two decision errors stack:
1. **Sign In was clicked with no password typed.** Either no secret was resolved for `company=WestinghouseP`, or the
   fill didn't happen.
2. **After a failed sign-in, the engine switched to Create Account.** That email already has an account here, as the
   10-16 confirmation shows. A second registration for the same email cannot be the fix.
   - *Not observed:* SF's duplicate-email wording, because I did not submit.
   - On top of that, the generated password did not meet the 18-character, mixed-case policy.

**Correct decision procedure**
- **Key credentials by the SF tenant**: host plus the `company=` parameter, for example
  `career4.successfactors.com|WestinghouseP`. Do **not** key them by the RMK domain. One SF company serves several
  RMK sites, and Capgemini's `careers.capgemini.com` maps to `capgemitecP3`.
- **If a credential exists:** type the email and password into the visible fields. Assert both are non-empty before
  clicking Sign In.
  - Success leaves the "Sign In" page. Either the application page loads, or `/portalcareer` / candidate profile
    loads (production 10-08: "The current page is the Candidate Profile (My Profile) rather than the job application
    entry").
  - Failure keeps the title "Career Opportunities: Sign In" with a server error. Then use "Forgot your password?"
    (`login_ns=forgot`; production has a mailbox reset path, "Employer password reset requested"). **Never** Create
    Account for that email.
- **If no credential exists and the email has never registered on this tenant:** go to "Create an account", generate
  the password from the DWR policy, and store it keyed to the tenant **before** clicking Create.
- **If "Sign In" was opened from the RMK landing page header** (production: "Opened the employer sign-in from its
  career landing page"), SF lands on the profile, not the job. Re-open the job's Apply link afterwards.
- **Some tenants use SAP IAS login instead.** The message bundle has `RCM_IAS_EXTERNAL_CANDIDATE_LOGIN_HEADER`
  "Candidate Login" and `RCM_IAS_INTERNAL_CANDIDATE_LOGIN_HEADER` "Employee Login". *Inferred:* the employee
  choice must never be taken.

---

## 2. Page tech

**SF tenant pages (observed)**
- The pages are server-rendered JSP. The legacy **JUIC** component framework puts inline handlers on elements
  (`juic.fire("9:","_click",event)`).
- Libraries: jQuery 3.5.1, **SAPUI5 1.142.5** (SURJ shell), CKEditor, and Fundamental-styles (`fd-*` classes). The
  Capgemini page loaded 282 scripts.
- **AJAX is DWR:** `POST /xi/ajax/remoting/call/plaincall/{controller}Proxy.{method}.dwr`. Observed calls:
  - `pwdPolicyControllerProxy.getPasswordPolicy` (page load, twice);
  - `picklistControllerProxy.getPicklistByPageWithExcludeOption` (picklists);
  - `rcmFireRuleOnChangeControllerProxy`, `rcm2AttachmentControllerProxy` and `otpServiceControllerProxy` (loaded).
- **Inline scripts carry the tenant switches.** Examples: `isCaptchaDisabledForLogin = true;`, `if(false){…verifyOtp()`,
  and `isCaptchaDisabled = true` inside `validateFields`.
- **Component ids are counters.** For example `9:_input`, `10:_listSelect` and `92:_attachIcon`. They change between
  page loads. The **stable** names are `fbclc_*`, `fbja_*` and the hidden value inputs `tor__fcust*`.

**RMK career sites (observed)**
- Bootstrap 3, `j2w` scripts and a cookie manager iframe (`/widgets/cookiemanageriframe/`).
- **Boehringer uses shadow DOM.** Its RMK theme renders the job inside custom elements with open shadow roots:
  `bi-job-posting`, `bi-menu`, `bi-application-process`, `bi-benefits`, `csb-block-container`.
  - `document.body.innerText` returned only the cookie banner, and `document.querySelectorAll('a')` did not find
    "Apply Now".
  - Playwright locators, which pierce shadow DOM, did.

---

## 3. Fields and widgets

### Paginated picklist, `rcmpaginatedselect` (observed live and in `RCMPaginatedPicklistSingleSelect.js`)
- **DOM.**
  - Visible `input#{n}:_input`: `role=combobox`, `aria-owns="{n+1}:_listSelect"`, placeholder "No Selection",
    `onclick`/`onkeyup`/`onkeydown` call `juic.fire(…,"_click")`, `onblur` calls `_onBlur`.
  - A button `#{n}:_selectButton`.
  - A **hidden value input** with a stable name. For example, `input#tor__fcustcountry` has class
    `appFieldRequired` and `aria-hidden=true`. It holds the **option id**, not the label.
- **Popup.** `ul#{n+1}:_listSelect[role=listbox]` with `li[role=option]` ids `{n+1}:item{k}`.
- **Paging.** **100 options per page**, fetched over DWR `getPicklistByPageWithExcludeOption` with
  `pageNumber/startIndex/endIndex`. Country has **253** options. Opening the list loaded pages 1 and 2 (A–Saudi
  Arabia). "United States" is on page 3, **so it is not in the DOM after opening**.
- **Search.** Typing makes a server-side search: the same DWR call with `filterType=contains` and
  `filter="United%20St"`. **One request fired 1.4 s after typing started**, so it is debounced. The list then held
  exactly "United States" and "United States Minor Outlying Islands".
- **Commit rules (source).**
  - Only an `itemSelected` event (click, or Enter on an option) runs `setValue()`. That writes the label into the
    visible input and the id into the hidden input, then dispatches `_picklistChanged` (cascades) and `change` (rules).
  - **`_onBlur`:** if the popup is closed and the typed text ≠ the committed label, it **reverts to the previous
    value**, or clears if the text is empty.
  - If the first page has exactly one real option, it is auto-selected.
  - A required field without a value gets class `invalidInput`.
- **Dependent fields.**
  - "How do you identify?" (`45:_input`) is `disabled` until the gender-consent picklist is answered.
  - Rules (`RCMOnChangeRuleTrigger`, `rcmFireRuleOnChangeControllerProxy`) can show, hide or enable fields after any
    change.

### Native selects
- `fbclc_ituCode`, "Country/Region Code": options look like "United States (+1)".
- `fbclc_country`, "Country of Application" or "Country/Region of Residence": 247–253 options.

### Text inputs
- Email and name fields `maxlength` are 99 and 40.
- `fbclc_emailConf` has **`onpaste="SFDOMEvent.preventDefault(event)"`**, so paste into "Retype Email" is blocked. It
  is validated on blur (`validateEmail()`).
- `fbclc_fName` is validated on blur by `isValidName`.

### Password (observed)
- `fbclc_pwd` validates on `onkeyup` and `onblur` with `checkPasswordPolicy(event, fbclc_pwdConf, true)`. That drives
  a strength indicator in `#rcmPwdPolicyProgressBar`.
- `validateFields()` fails the password if that indicator has an error class. **The indicator updates only on keyup
  or blur.**
- The policy list is shown inline in `#rcmPwdPolicyAnchor`, or as an info popup on other themes.

### Per-tenant password policy (observed)
The page itself loads the policy via DWR `pwdPolicyController.getPasswordPolicy`. In page context:
`AjaxService.getMBeanInstance('pwdPolicyController').getPasswordPolicy(null,{callback})`.

| Tenant | min | max | mixedCase | alphaNumeric | Text shown |
|---|---|---|---|---|---|
| Capgemini | 8 | **18** | true | true | "Password must be at least 8 characters long." / "Password must not be longer than 18 characters." / "Password must contain at least one upper case and one lower case letter." / "Password must contain at least one number or punctuation character." / "Password must not contain space or unicode characters." |
| Boehringer | 8 | **32** | false | true | Same, without the case rule, and max 32 |
| SAP | 8 | **20** | true | true | Same as Capgemini, with max 20 |
| Westinghouse | 8 | **18** | true | true | Same as Capgemini. Shown inline on Create Account before typing |

Indicator strings:
- "Password accepted"
- "{0} more character(s)"
- "Your password is too long"
- "1 upper case letter" / "1 lower case letter" / "1 non-alpha character"
- "’{0}’ is not a valid character"
- "Passwords don’t match" / "Password matches"

### Resume and cover letter
- `span[role=button]#{n}:_attachIcon`, "Upload a Resume Opens a dialog". The dialog is `rcmFileUploadPopup`, with
  local file, Google Drive (`googleDriveFilePicker`, `apis.google.com`) and Dropbox (`dropins.js`).
- Hidden inputs: `paramResumeFileName`, `paramResumeContent` and `paramResumeCloudUrl`. The cover letter has the same
  set.
- *Inferred:* the file is staged client-side and posted with the form.
- Messages: "File uploaded successfully." / "Invalid attachment type." / "The file type of {0} is not supported for
  attachments".

### Privacy statement (DPCS)
- `a#dataPrivacyId` calls `validateAndOpenDpcsDialog(true)`. **The dialog does not open until "Country of
  Application" is chosen.** Without a country it runs `checkforMandatory("fbclc_country", …)` and focuses the select.
  - **Observed:** "Country of Application is required".
- **Changing the country clears the acceptance** (`fbclc_country onchange="clearDpcsInfo()"`).
- Missing acceptance shows "Privacy Notice is required" (`#fbclc_dpcsId_error`). On Boehringer the label is "Terms of
  Use:*".

### Sign-in modal on the embedded page: duplicate names
- The `#careerform` holds **hidden** `input#qausername[name=username]`, `input#qapassword[name=password]`,
  `input#fbqa_signin[name=fbqa_signin]` and `input#recaptcha_response_field_signin_modal`.
- The **visible** dialog fields `#username[name=username]` and `#password[name=password]`, and
  `button#fbqa_signin`, are outside any form.
- So `[name=password]`, `[name=username]` and `#fbqa_signin` each match the **hidden** element first.
- The visible button runs `_validateFields`, which reads the visible inputs, copies them into the hidden ones and
  submits `#careerform`.

---

## 4. Validation

**Inline errors** appear in `div#{fieldId}_error`. The field gets class `invalidInput` and `aria-invalid="true"`.
Observed wording:
- "Country of Application is required"
- "Country of Residence is required"
- "Privacy Notice is required"

The template is `COMMON_ERR_DATA_REQUIRED` = "{0} is required".

**Other `MSGS` strings read from the page**
- "Please complete all required fields and re-submit." (`APPL_APP_ERROR_MESSAGE`, top banner)
- "{0} section has errors" / "Section has errors"
- "Invalid email."
- "An email address is required."
- "A password is required." (`RCM_PASSWORD_REQUIRED`)
- "This is an invalid entry. This field can only contain numeric values and these special characters: ‘+’ ‘(’ ‘)’
  ‘-’ ‘.’ and SPACE." (`RECRUITING_INVALID_PHONE_NUMBER`)
- "This is an invalid entry. This field should contain nine numeric characters." (SSN)
- "Invalid URL ( Valid: http, https, ftp )"

**Errors**
- Generic: "An application error occurred. Please try again later, or notify support if the issue keeps occurring."
- WAF: "There was an error processing your request. Please contact Technical Support. Error Code: {0}"
  (`COMMON_WAF_BLOCK_EXCEPTION_CONVERTER_ERROR`).
- Security scan: "This content was rejected by the Security Scan of User Inputs feature because it might contain
  malicious content…".
- Session lost (production, AMETEK): an error page with "An error occurred while processing your request. Please go
  back to your original page and check the URL." and no controls.

---

## 5. Bot protection

**Imperva (Incapsula + Advanced Bot Protection), on some RMK domains (observed on Boehringer)**
- Cookies on `.boehringer-ingelheim.com`: `visid_incap_2932291`, `incap_ses_1011_2932291` and `nlbi_2932291`.
- Cookie `reese84` on `.jobs.boehringer-ingelheim.com`.
- A fetch `POST https://jobs.boehringer-ingelheim.com/mrose-was-me-his-not-so-morie-on-councanny-make-?d=career5.successfactors.eu`
  with body `{"solution":{"interrogation":{"p":"…`. That is the reese84 interrogation, a device fingerprint.
- **Not present** on careers.capgemini.com or on `career5.successfactors.eu`, which only set `JSESSIONID`, `route` and
  `careerSiteCompanyId`. So it depends on the employer's domain.

**Google reCAPTCHA v2 (configurable; not active on these tenants)**
- `sfReCaptcha` loads `https://www.google.com/recaptcha/api.js?onload=sfReCaptchaRenderCallback&render=explicit` and
  renders `type:"image"` with sitekey `6LcIRvssAAAAABQFcFPxVEwioK9odHeHybgtzHjz`, but only when the tenant flag
  enables it.
- Capgemini's sign-in modal has `isCaptchaDisabledForLogin = true`, and Boehringer's `validateFields` has
  `isCaptchaDisabled = true`.
- No `google.com/recaptcha` request was seen on any page I opened.

**Cookie banners that intercept clicks**
- **SAP (TrustArc).** `div#trustarc-banner-overlay` covered "Create an account". Playwright's click timed out after
  15 s, and `elementFromPoint` returned the overlay.
- Capgemini: "Reject All Cookies / Accept All Cookies".
- Boehringer: "Accept All / Select Cookie Preferences / Reject All".

**Not seen:** hCaptcha, Cloudflare, DataDome or PerimeterX.

---

## 6. Submit

- **Embedded variant.** `button#fbqa_apply` "Apply" (`name=fbqa_apply`) submits `#careerform`. Its hidden fields
  include `fbja_action`, `fbja_reSubmit`, `career_job_req_sec_key`, `career_job_app_sec_key`, `RCM_IS_AUTO_SAVE` and
  `pvt_hash_key/value`.
  - *Inferred:* this is a classic full-page POST, not XHR.
- **Classic variant.** `#fbclc_createAccountButton` calls `submitCreateAccount()` → `validateFields()` →
  `setFieldAndShowOverLay()`, then the form is submitted. With OTP it calls `verifyOtp()` first and submits after
  `OtpSuccessful`.
- **Success** is a page navigation to `/portalcareer…`. The text is in `#success_message` ("Your application has been
  sent. Thank you!") or in tenant-specific `#applyConfirmMsg` (§1E).
- **Failure.** The page re-renders with the banner "Please complete all required fields and re-submit." plus
  per-field `_error` divs, or shows the generic or WAF error text (§4).

---

## 7. Engine risks

1. **The picklist reverts on blur.** Typing a label and pressing Tab never commits. You must pick the `li[role=option]`
   (click or Enter) in the owned `#{id}:_listSelect`. Otherwise `_onBlur` restores the old value, often "No
   Selection".
   - *Production:* `field_error` "Could not commit …" from `widgets.py _rcm_paginated`, with the detail "The website
     did not commit the observed choice". Since 10-14: Country of Residence ×8, several yes/no screening questions
     ×6 each, and University Name, Ethnicity, Gender, Veteran status, How did you hear, State.
2. **Pagination hides options.** Options past the loaded pages (for example "United States", at position >200) are
   not in the DOM. Use the server "contains" search: type part of the label, wait for the DWR response, then pick.
3. **Dynamic ids.** `9:_input` and the like change between loads. Key fields on the hidden `tor__fcust*` names or on
   the label, never on `N:` ids.
4. **Sign-in vs create decision** (§1F).
   - Westinghouse's "A password is required." is a client-side block: the Sign In click happened with an empty
     `#password`.
   - The engine then switched to Create Account for an email that already has a working account there (confirmed
     10-16 04:06).
   - Credentials must be keyed by SF tenant (`host|company`). A failed sign-in leads to "Forgot your password?",
     never to Create Account.
   - **Duplicate `name`/`id` on the embedded sign-in modal** (Capgemini, not Westinghouse): `[name=password]` first
     finds the hidden `#qapassword`.
5. **Password policy differs per tenant** (max 18 / 18 / 20 / 32; mixed case or not).
   - The client check runs only on keyup/blur. A `fill()` that sets the value without key events can pass client
     validation and then be **refused by the server**.
   - On Westinghouse, Capgemini, Boehringer and SAP the rules are printed **before** Create (`#rcmPwdPolicyAnchor`).
     The same sentences then serve as both instructions and violation text, so don't detect a violation by matching
     them. Use `#rcmPwdPolicyProgressBar` and the `_error` containers instead.
6. **Paste into Retype Email is blocked** by `onpaste` preventDefault.
7. **The privacy dialog depends on the country.** Choose Country of Application or Residence first. Changing it later
   silently clears the acceptance ("Privacy Notice is required").
8. **OTP before the account exists** (SAP): 3 attempts and a 120 s resend wait. It is per tenant and visible in the
   inline `submitCreateAccount` source.
9. **Shadow-DOM career sites** (Boehringer). The Apply link is not visible to `document.querySelector` or innerText.
10. **Cookie overlays block clicks.** TrustArc on SAP blocks the "Create an account" link.
11. **Imperva fingerprinting** on some employer domains (`reese84`). Repeated fast navigations from one browser
    profile may be challenged.
    - *Inferred:* not observed failing here.
12. **The direct `/talentcommunity/apply/{id}` URL bounces to the home page.** Always start from the job page.
13. **A single "Apply" creates the account and submits** in the embedded variant. Nothing in between can be reviewed.
14. **Session loss** gives a generic error page with no controls (AMETEK, production).

---

## Engine checklist (testable)

**Before acting**
- [ ] On the SF tenant page, before any account action, capture the DWR response of
      `pwdPolicyControllerProxy.getPasswordPolicy`. The page calls it on load; or call
      `AjaxService.getMBeanInstance('pwdPolicyController').getPasswordPolicy(null,{callback})`. Generate the password
      from `{minLength,maxLength,mixedCase,alphaNumeric}`. Expected: Capgemini max 18, SAP max 20, Boehringer max 32.
- [ ] Read the inline `submitCreateAccount` source. If it contains `if(true)` before `verifyOtp()`, expect an email
      Passcode step: the header "Verify Email Address", field "Passcode", 3 attempts, 120 s before resend.

**Sign-in vs create**
- [ ] Look up the credential by `host|company` (for example `career4.successfactors.com|WestinghouseP`), not by the
      RMK domain.
- [ ] Never click "Sign In" while `#password` is empty. A test should assert that the engine refuses, because the
      page would only show "A password is required." client-side.
- [ ] After Sign In, either the title leaves "Career Opportunities: Sign In" within 15 s, or the run reads the error
      and goes to "Forgot your password?" (`login_ns=forgot`). A test should fail if the engine goes to Create Account
      after a sign-in attempt for an email that has a stored credential.
- [ ] Before Create, store the generated password keyed to the tenant. It must satisfy the DWR policy (Westinghouse:
      8–18, mixed case, a number or punctuation, no spaces).

**Typing and identifying fields**
- [ ] Type passwords with key events (`press_sequentially`). After blur, `#rcmPwdPolicyProgressBar` must show "Password
      accepted", and the retype must show "Password matches".
- [ ] Never paste into `#fbclc_emailConf`. Type it.
- [ ] Locate sign-in inputs by **visible** role and label inside the dialog (`#username`, `#password` under
      `.dialogBoxWrapper` or the sign-in page form). Click the visible `button#fbqa_signin` / "Sign In". A test should
      fail if any write targets `#qausername` or `#qapassword`.

**Picklists**
- [ ] For each `rcmpaginatedselect`: type 3+ characters of the target label. Wait for the
      `getPicklistByPageWithExcludeOption` response with `filterType=contains`. Click the `li[role=option]` whose text
      equals the target.
- [ ] Verify: (a) the visible input value equals the label; (b) the hidden `tor__…` input is non-empty; (c) the input
      lacks `invalidInput`. Re-check after blur, because the value must survive `_onBlur`.

**Ordering**
- [ ] Choose `fbclc_country` before clicking `#dataPrivacyId`. After accepting, assert that `#fbclc_dpcsId` is
      non-empty and that `#fbclc_dpcsId_error` is hidden. Never change the country afterwards.

**Navigation and overlays**
- [ ] Start from the RMK job page. Expect `POST /services/cas/createpayload/`, then a document POST to
      `career{N}.successfactors.*/careers?company=…`.
- [ ] Before clicking, check `elementFromPoint` at the target centre. If it hits `#trustarc-banner-overlay` or another
      cookie overlay, resolve the banner first.

**Success and failure**
- [ ] Success: the URL path is `/portalcareer` **and** `#success_message` contains "Your application has been sent",
      **or** `#applyConfirmMsg` is non-empty. "You already applied for this position." means already applied.
- [ ] Failure: "Please complete all required fields and re-submit.", or any visible `[id$="_error"]:not(.hide)` text.
      Return the field labels for repair.
