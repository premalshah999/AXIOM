# Eightfold (PCSX career sites: *.eightfold.ai and employer domains) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9342), never the worker Chrome. Nothing was
submitted, no email was entered, no code was requested and no account was created. On Micron's form I opened one
combobox and typed one letter into it, then cleared it.

**Tenants probed**

| Tenant | Job | Apply type (`applyAction.status`) |
|---|---|---|
| Morgan Stanley `morganstanley.eightfold.ai` | 549799201735 | `link_off`, to Workday |
| Qualcomm `qualcomm.eightfold.ai`, which redirects to `careers.qualcomm.com` | 446721594922 | `log_in` |
| Micron `micron.eightfold.ai` | 44832941 | `allowed`, guest. Form walked |
| John Deere `johndeere.eightfold.ai` | 137483945872 | `allowed` |
| PayPal `paypal.eightfold.ai` | 274922740164 | `allowed` |
| Boston Scientific `bostonscientific.eightfold.ai` | 563602813765676 | Closed: "No longer accepting applications." |

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_2bcafd18a7ab1cdee848f6c36a1ccc3e7e9a85b30ec33a10b67a4b3b4b7dcdea` | inspect-web-page on the Morgan Stanley job page, with accessibility and JSON shapes. "Apply Now" is `role=button`, focusable, and is not a link |
| `ev_d27f0afdca17717546ca83d62ea7515d51535e036362e821dc5f22aa8301c59d` | inspect-web-page on the Qualcomm sign-in modal (`/careers/apply?pid=…`), with scripts and accessibility |
| `ev_aab4f79a14970935ec45e38128720c2159de86b5d850d695089c0dd8e03be7a2` | export-web-scripts for Qualcomm. The auth chunk is `gen/js/45910.beaf503d51c503d6.js`, along with `pcsxPwa.*.js` and `navbar.*.js` from `static.vscdn.net` |
| `ev_33f7bf76b7829e38e4fb08dc55aa3094b80aa4d18fc2061f4e6634eec7ba796b` | inspect-web-page on Micron `/careers/apply?pid=44832941` |
| `ev_8ff9b32bf74c55d37ca56091d9e8a1d6765f92a4556df615f72530a846ecd6bf` | export-web-scripts for Micron. The apply chunk is `82790.637c73783cfafacf.js` (submit, upload and success modal) |

**Other sources**
- A Playwright network log with JSON bodies:
  - `/api/pcsx/position_details`;
  - `/api/career_signup/boot-data`;
  - `/api/application/v2/{bootstrap_application,profile,questions}`.
- The full Micron `questions` JSON (364 KB).
- Production data, read only:
  - runs and events, including Morgan Stanley `86a05eb6…` and `957a1624…` (10-14);
  - mail_rules.

**Conventions.** **Observed** means seen live, in JSON or in the bundle. **Inferred** means my reading.

---

## 1. Workflow

### The job page decides the path: `positionUserActions.applyAction` (observed)
`GET /api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en` returns `data.positionUserActions.applyAction`.

| `status` | Tenants | The "Apply Now" control | What happens |
|---|---|---|---|
| `allowed` | Micron, John Deere, PayPal | `<a href="/careers/apply?pid={pid}" target="_self">Apply Now</a>` | Same tab. The guest **Application Form** opens |
| `log_in` | Qualcomm | `<a href="/careers/apply?pid={pid}">` | Same tab. The **Sign in** modal opens |
| `link_off` | Morgan Stanley | `<button class="action-2lm8g button-module_button__WDVlB …">Apply Now</button>`, with no href | **A new tab opens** (see below). The JSON carries `applyUrl` |
| *(none)* | Boston Scientific (closed) | No Apply control | Text: "No longer accepting applications." |

### Morgan Stanley: why the run "never left the job page" (observed)
1. "Apply Now" is a `<button>` (REA accessibility `ev_2bcafd18…`: `role:"button", name:"Apply Now"`). Clicking it opens
   a **popup / new tab**:
   `https://morganstanley.eightfold.ai/r?ns=pcsx_app&evt=click_apply_to_job&pid=549799201735&domain=morganstanley.com&n=https%3A%2F%2Fms.wd5.myworkdayjobs.com%2FExternal%2Fjob%2FNY---2000-Westchester-Ave%2FMachine-Learning--Vice-President_JR041364%2Fapply%3Fsrc%3DEightfold`.
2. **The original tab does nothing.** It does not navigate and sends no XHR (observed: 0 requests after the click).
3. The new tab shows **"Redirect Notice"**, h2 **"You are navigating away from Morgan Stanley"**, and the text "Click
   https://ms.wd5.myworkdayjobs.com/External/job/…/apply?src=Eightfold to continue." It has **one plain `<a>`**, no
   meta refresh and no script. **It does not redirect by itself.**
4. The real application is **Workday** (`ms.wd5.myworkdayjobs.com`, tenant `ms`, site `External`).
   `position_details` gives the same URL before any click:
   `"applyAction":{"status":"link_off","applyUrl":"https://ms.wd5.myworkdayjobs.com/External/job/…/apply?src=Eightfold"}`.

**Production matches.**
- Run `86a05eb6…`: "Eightfold: form", then "Unrecognized page; continuing with the guided flow", then "Refused a
  repeated unchanged strategy" ×3, then "did not advance".
- A sibling Morgan Stanley run reported **Workday's** error "Error - Page Error You already applied to this job.".
  So once a run did reach the Workday tab, it was in the Workday flow.

There is **no SSO gate** and no sign-in modal on the Morgan Stanley job page. The page has an invisible reCAPTCHA
(recaptcha.net, `size=invisible`) and a hidden `base-dialog-module_dialog-back` dialog shell, neither of which blocks.

### Qualcomm `log_in`: email-first sign-in with an emailed one-time code (observed and bundle)
- **`/careers/apply?pid=…`** shows a modal with h1 **"Sign in"** and these controls:
  - `input#auth-entry-email-input[type=email][name=email][data-testid=auth-entry-email-input]`;
  - "Continue";
  - "Sign in using Google";
  - "Create an account";
  - "Close candidate authentication modal".

  It also shows the warning "Current Qualcomm employees must sign in using the Career Hub." with a link to
  `qualcomm.eightfold.ai/careerhub`.
- **Config**: `GET /api/career_signup/boot-data?domain=qualcomm.com&trigger=pcsx-sign-in&force_login_enabled=false&email=&phone_number=`.
  - Login components are `email_login` and `google`.
  - Register components are:
    - `email_signup` and `google`;
    - `contact_consent`. It is required, with error "You must select one choice.";
    - `terms_and_conditions`: "I have read, understood and to the extent requested, consent with the Career Site
      Privacy Notice";
    - `notifs_user_override`: "I would like to receive AI-powered job alerts for roles that match my profile.".
  - `showEmailUnverifiedPage: true`.
- **After "Continue"** (bundle `45910`):
  1. If reCAPTCHA is enabled, a `recaptcha_token` is fetched for action **`account_info`**.
  2. `POST /api/career_signup/account_info {domain, email}` returns `{accountExists, tempUser, invalidEmail}`. A 403 or
     `invalidEmail` shows the server message. An unknown email on login shows "We don't recognize this email. Create a
     new account".
  3. `POST /api/career_signup/send_otp_verification {email, domain, trigger, …}` opens the **"Check your email"**
     step: "We just sent an email to {email}.", legend "Enter the code below", **6 single-digit inputs**
     (react-otp-input, `numInputs:6`, autofocus) and **"Submit"**.
  4. `POST /api/career_signup/confirm_otp {otp, domain, next, email}` succeeds and goes to `next`
     (`/careerhub` by default, or the apply page). MFA SMS may follow.
- Other auth endpoints:
  - `stage_password`, with reCAPTCHA action `stage_password`, then the OTP;
  - `create_candidate_account` (form-urlencoded, with consents);
  - `google_auth`, `microsoft_auth`, `linkedin_auth`, `facebook_auth`;
  - `mfa/sms/*`.

### Guest Application Form (Micron, observed)
- `/careers/apply?pid={pid}`. Title "Submit application for {job}". h1 "Application Form".
- **One page.** Sections: Resume, Contact Information, Application questions, "My Information", "EQUAL EMPLOYMENT
  OPPORTUNITY (EEO) STATEMENT".
- **The data is server-defined:**
  - `GET /api/application/v2/bootstrap_application?domain=micron.com&pids=44832941&user_mode=logged_out_candidate`:
    - `recaptcha:{recaptchaEnabled:true, recaptchaKey:"6LfwboYUAAAAAJb6QcVuRXi7R9pTqgGKF6TnNzia"}`;
    - `successPage:{successMessage:"", successImageUrl:"", trackApplication:{enabled:false}, survey:{url:"/survey/embed/pcs_apply_thank_you_page"}}`;
    - `preSubmitProfileReview:{enabled:false}`;
    - `prepopulateSettings`: a "Save my answers for future applications." checkbox, default **true**, with
      `showClearForm:true` and `doNotPrepopulateQuestionIds:[veteran, disability, gender, race, citizen, visa, ethnicity]`;
    - `hideCancelButton:false`, `phoneNumberFieldV2Enabled:false`.
  - `GET /api/application/v2/profile?…&user_mode=logged_out_candidate`.
  - **`GET /api/application/v2/questions?domain=…&pids=…&user_mode=logged_out_candidate`** returns
    `fields:[{title, questions:[{questionId, type, label, required, parentDependencies:[{questionId, values}], selectOptions:{choices | apiEndpoint}, rules, profileField}]}]`.
- **Micron's question set:**
  - 504 questions: checkbox 245, text 215, select 57, instruction 11, radio 6, date 2, resumeUpload 1, phone 1.
  - **Only 21 questions in the large section are unconditional.** The rest depend on parents, mainly
    `Q---AUTO---COUNTRY` ("Country of Residence") and `previous_employment`. Examples:
    - `Q---AUTO---FIRST_NAME_REQUIRED` for CA/CR/IS/US;
    - the US disability date `Q---AUTO---Self_Identification_of_Disability_Status_ID_English_Date---US`.
  - State uses `apiEndpoint /api/application/v2/location/options?domain=…`.
- **Resume:**
  - `input[type=file][accept=".doc,.docx,.pdf,.txt,.rtf"]` with a "Select file" button.
  - Upload goes to `POST /api/application/v2/resume_upload?domain={domain}`, which prefills the profile.

---

## 2. Page tech (observed)
- **React PCSX ("pcsxPwa") app.** Chunks come from `https://static.vscdn.net/gen/js/…`, with jQuery present and CSS
  modules (`button-module_button__…`, `list-module_…`).
- **Odd h1.** **Every job page's h1 is "Single Position"**. The title is an h2.
- **Employer domains** serve the same app: `qualcomm.eightfold.ai` redirects to `careers.qualcomm.com`. The API paths
  stay the same.
- **No iframes in forms.** The iframes present are reCAPTCHA and analytics (doubleclick, adsrvr, brandcdn).
- **Shadow DOM** shows up only in third-party widgets. Boston Scientific's **Paradox "Olivia"** chat widget lives in an
  `apply-widget` element and offers "Send message" and "Close widget chat".
- **Cookie banners are tenant-specific.** Qualcomm uses OneTrust, with "I Understand" and "Cookies Settings" and
  `ot-group-id-*` switches.
- **Job-page widgets that are *not* the application:**
  - "Upload your resume" / "Find out how well you match with this job" (`/api/pcsx/upload_resume`);
  - "Add to Job Cart";
  - "Favourite this job";
  - "Save {similar job}" buttons.

---

## 3. Fields and widgets (Micron, observed)

| Widget | DOM | Notes |
|---|---|---|
| Text | `input#Contact_Information_email`, `#Contact_Information_firstname`, `_lastname`, `_city`, each with `data-test-id` set to the same id | Phone and City use `autocomplete="one-time-code"`, which suppresses browser autofill |
| Phone | `input#Contact_Information_phone`, plus a country-code combobox `input[role=combobox][placeholder="Select country code"]` | |
| Select / combobox | `input[type=text][role=combobox][aria-controls="list-N"]`. Ids are **generated** (`input-7`, `input-10`, …) and the placeholder is "Select" | Opening it shows `#list-N[role=listbox]` (`list-module_list-container…`) in a portal, with options `#list-N-option-K[role=option][aria-selected]`. **Typing "N" did not filter** a 4-option list. Option text may be uppercase |
| Radio group | `div[role=radiogroup]#Application_questions_{qid}`, with `input[type=radio]#{qid}-true` / `-false`. Labels are "Yes" / "No"; the aria-label reads "Yes, {question}" | The inputs carry `readonly`. Click the option or its label |
| EEO radios | e.g. `#Application_questions_ethnicity_US-USA_DECLINED_TO_STATE` ("Declined to state") | Not prepopulated (see `doNotPrepopulateQuestionIds`) |
| Checkbox | "Save my answers for future applications.", checked by default, with a random id | Harmless |
| Date | type `date` questions, e.g. the US disability date | The 10-17 analysis found `role=combobox` with `aria-haspopup=dialog`. Use a date driver, not a listbox |
| Buttons | "Select file", "Read more", **"Cancel"**, **"Clear form"**, **"Submit application"** | "Clear form" wipes every answer |

---

## 4. Validation (bundle strings)
- **Form:**
  - "The above field is required";
  - "Error: This field cannot be left blank.";
  - "Please enter a valid phone number";
  - "Phone number must be at least %(minLength)s digits";
  - "Please select a country";
  - "Please select from the options above".
- **Upload:**
  - "File upload failed";
  - "%(fileName)s exceeded maximum file size" with ". Maximum file size allowed is %(fileSize)s MB";
  - "Invalid file formats found for: %(files)s";
  - "File appears empty or could not be read. Please try a different file.";
  - "Could not upload resume, please try again later.".
- **Auth:**
  - "Please enter a valid email in the format example@example.com";
  - "Please enter the 6-digit code";
  - "**You have exceeded the maximum number of attempts. Please try again later.**";
  - "Your account is locked. Please try again later.";
  - "Failed to verify code. Please try again later.";
  - "Verification code resent successfully.";
  - "Could not send verification code, please try again later.".
  - The OTP error renders as `p[role=alert]`.
- **Duplicates:**
  - "You have already applied for this position.";
  - "This candidate has already been submitted by another source.".

---

## 5. Bot protection
- **Invisible reCAPTCHA via `www.recaptcha.net`, key `6LfwboYUAAAAAJb6QcVuRXi7R9pTqgGKF6TnNzia`.** It is present on job
  pages, sign-in and apply. The anchor uses `size=invisible`.
- **Tokens are requested per action:**
  - `account_info`;
  - `stage_password`;
  - **`{mode}_apply_form`** at submit. This is a `recaptcha_token` FormData field, sent when
    `bootstrap.recaptcha.recaptchaEnabled` is true.
- *Inferred:* named actions mean the key is v3-style and score-based. A v2 checkbox solver token is the wrong kind.
- **The email OTP comes from one shared sender.**
  - *Observed in mail_rules:* every Eightfold tenant uses `sender_domains ["eightfold.ai"]`.
  - *Production:* about 30 Qualcomm and Starbucks runs on 10-14 ended with "Another application kept the same email
    sender busy; nothing was sent.".
- No Cloudflare challenge, hCaptcha, DataDome or PerimeterX was seen.

---

## 6. Submit (bundle `82790`)
- **Submit sends `POST /api/application/v2/submit?domain={domain}&pids={pid}`** as FormData. It carries the
  `recaptcha_token` and optional `referral_token`, `multistage_token`, `tta_token` and `apply_with_linkedin_success`
  fields. A failure returns `{success:false, error}`.
- **Success is a modal, rendered in a portal:**
  - an image `https://static.vscdn.net/images/application-submitted-confirmation.svg`;
  - **`h2#form-submission-success-message[tabindex=-1]`**, which holds `successPage.successMessage` (tenant text with
    profile variables) or the default **"Thank you for your application, %(firstname)s!"**;
  - a close button with aria-label **"Close the application submission success modal"**;
  - a primary button **"Continue"** (`[data-test-id="modal-submit"]`, aria-label "Continue past the application
    submission success modal");
  - focus moves to `#form-submission-success-message`.
  - This is the modal that Boston Scientific showed in production ("Thank you for your application, Alex!", 10-18
    diagnosis).
- **Success variants:**
  - **AI-interview tenants:** `div[role=heading][aria-level=2]` "**Application Submitted**", then "🎉 Congrats!
    You've been fast-tracked to the next step…", then "Start AI Interview".
  - **Employee mode:** `h1` "**Congratulations! Your application is submitted**".
- **Errors:** "Something went wrong. Please try again", and the duplicate messages listed in §4.

---

## 7. Engine risks
1. **`link_off` opens a new tab, and that tab stops at a click-through interstitial.** The original tab never changes.
   This is the root cause of Morgan Stanley's "never left the job page". Fix: read `applyAction` before clicking. When
   it is `link_off`, hand `applyUrl` to the real ATS engine (Workday here) and do not touch the Eightfold page.
2. **`log_in` tenants need an emailed 6-digit OTP from the single sender `eightfold.ai`.** Serialise these runs, and
   don't burn the sender lock before the form is ready. Watch the attempt cap ("You have exceeded the maximum number
   of attempts…"); see the OTP rate-limit memory from 10-14.
3. **The OTP field is six single-character inputs.** Type one digit per box, or paste the whole code into the first
   box, which react-otp-input distributes.
4. **The success modal is in a portal and its text is tenant-defined.** Read `#form-submission-success-message`
   anywhere in the document, and the variants' headings, **before** pressing "Continue" or closing. Production missed
   Boston Scientific's modal.
5. **Conditional explosion.** About 480 questions appear only after their parents are answered. Answer "Country of
   Residence" (and `previous_employment`) first, then re-read the form. Use the `questions` JSON
   (`parentDependencies`) to predict which fields become visible and required.
6. **"Clear form" sits beside "Submit application".** Never click it.
7. **"Upload your resume" on the job page is the match widget**, not the application's resume field.
8. **Ids are generated per render** for comboboxes (`input-N`, `list-N`). Key fields by `questionId` or label, not by
   these ids.
9. **Closed postings** show "No longer accepting applications." and have no Apply control. Stop with "closed".
10. **Domain redirects.** `qualcomm.eightfold.ai` becomes `careers.qualcomm.com`. Origin checks, account keys and mail
    link origins must accept both.
11. **Chat widgets** (Paradox Olivia, in shadow DOM) add a "Send message" textbox. Do not fill it.
12. **h1 "Single Position"** is a template string, not the job title.

---

## Engine checklist (testable)
- [ ] Before clicking Apply, `GET /api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en` and branch:
      - `allowed`: the form opens;
      - `log_in`: the OTP sign-in;
      - `link_off`: open `applyUrl` with that ATS's engine (Morgan Stanley gives
        `ms.wd5.myworkdayjobs.com/External/…/apply?src=Eightfold`);
      - missing applyAction plus "No longer accepting applications.": closed.
- [ ] If an Apply click opens a popup (`context.on("page")`), follow it. A page titled "Redirect Notice" with h2 "You
      are navigating away from …" must lead the engine to its single `<a>` href. The run must not stay on the job tab.
- [ ] For `log_in`: `#auth-entry-email-input`, then "Continue", then expect `POST /api/career_signup/account_info`
      followed by `send_otp_verification`, then the h1 "Check your email" with 6 inputs. Enter the code and press
      "Submit". Expect `confirm_otp` `success:true` before filling. At most one Eightfold OTP run at a time.
- [ ] On the form, answer "Country of Residence" first. Then wait until the dependent required fields are rendered
      (compare with `parentDependencies`) before filling them.
- [ ] Open a combobox and choose only from `#{aria-controls} [role=option]`. Confirm `aria-selected="true"` or that the
      input value equals the option text.
- [ ] Click "Submit application" once. Success means a visible `#form-submission-success-message`, or the role=heading
      "Application Submitted", or the h1 "Congratulations! Your application is submitted". Record the text before
      pressing "Continue".
- [ ] "You have already applied for this position." is recorded as already applied, not as a failure.
- [ ] A unit test: the filler never clicks "Clear form" or "Cancel", and never types into chat widgets.
