# Dayforce (jobs.dayforcehcm.com + Dayforce ID) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9342), never the worker Chrome.
- Nothing was submitted and no account was created.
- The only values typed were dummy passwords ("aaab", "Abcdefg1", "Abcdef=1") on an empty "Create Your Account" form.
  They were typed to read the live password rules, then cleared.
- I clicked "Next" once on that empty form, and once on the empty guest form. Client validation stopped both: no
  application request left the page, and the only POST was LaunchDarkly analytics.

**Tenants probed**
- **Imperial Brady** (`imperialdade`, job 11166): needs an account. This is the tenant whose run stalled after
  "Create one now.". I walked it to "Create Your Account" and to the Dayforce ID Sign In page.
- **NCMEC** (`ncmec`, job 3438): allows guests. I walked it to the guest "Manual Application".
- **Security Benefit** (`hcmportal`): only through production events.

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_0c1ef9011b14876c50df3e69b0278528aa5d42ef5561007c969a1b3fd09f717b` | list-browser-targets on 9342 |
| `ev_c5170059f3da1dbf71cb3eabe8ce57b5272fb23f72e480aa1e5bcad4d6a97d5d` | inspect-web-page on `dfid.dayforcehcm.com` "Create Your Account" (Imperial Brady returnUrl, `action=register`), with scripts, JSON shapes, accessibility and storage keys |
| `ev_d8acc66cbf2829c04a39343e207204b5a8c48b90e84b851fca3f9c2d2849eee3` | export-web-scripts of that page. Dayforce ID app `GlobalIdentityClientApp/static/js/main.js?v=v1` (4.7 MB), `StaticFiles/js/Validator.js`, jquery-validation, signalr, recaptcha, inline config |
| `ev_3824eda5a5a9b54552b7d1d8e96ae4f55347d56201617ad0b04a00e4b7162990` | inspect-web-page on NCMEC guest `/apply/manualApplication`, with scripts, JSON shapes and accessibility |
| `ev_90e3e20030d6b453e23fd0e4661fd7208fbfdbd2a7f9e1cea30ef560e61646aa` | analyze-web-bundle on the same page. It found Next.js; its endpoint candidates are low-signal |
| `ev_1b4a05bd5d6f21d36a2bc0c8e58aff02fb4aeaf208fb8382755d0edb99195b24` | export-web-scripts of the jobs.dayforcehcm.com Next.js chunks (`_app-*.js`, `2484-*.js`, `5895-*.js`, page chunk) |

**Other sources**
- **Playwright network log** with JSON bodies:
  - `/api/geo/{ns}/sitecontext/...`, `/api/geo/{ns}/jobposting/...`, `/api/geo/{ns}/jobapplication/...`;
  - LaunchDarkly `evalx` flags;
  - `_next/data/{build}/…/apply.json` (the i18n store);
  - Dayforce ID locales `/GlobalIdentityClientApp/locales/en-US/{register,verification,validation,security-questions,mfa,common,login}.json?v=v3`;
  - `GET /GlobalIdentity/SecretQuestions`.
- **Production, read only:** runs/events for Imperial Brady `3865dd579ba640fea65eb1a8afd75697` and Security Benefit
  `7d44f1af10224cb5a2430a747d43d60b` (10-16 23:52–23:54), Dayforce account events since 10-07, and mail_rules.
- **Engine source, read only:** `v3/jobpilot/accounts.py` and `forms.py`. Read to explain the stall; not changed.

**Conventions.** **Observed** means seen live, in a JSON response or in the bundle source. **Inferred** means my reading,
not verified end to end.

---

## 1. Workflow

### Routes

| Step | URL | Notes |
|---|---|---|
| Job page | `https://jobs.dayforcehcm.com/{culture}/{clientNamespace}/{jobBoardCode}/jobs/{jobId}` | `jobBoardCode` case varies: `CANDIDATEPORTAL`, `candidateportal`, `jobopenings`, `emrusacareers` |
| Flow selection | `…/jobs/{jobId}/apply?flowSelection=true` | Observed after "Apply". Production classifies it as **"Dayforce: unknown"** |
| Guest form | `…/jobs/{jobId}/apply/manualApplication?applicationSource=Manual` | Observed. Steps are carried in a `step=N` query (bundle: `router.push({query:{…,step}})`) |
| Signed-in apply | `…/jobs/{jobId}/apply` | The next-auth `callbackUrl` (observed in the sign-in POST body) |
| Dayforce ID: register | `https://dfid.dayforcehcm.com/globalidentity/account/login?returnUrl=…action%3Dregister…` | **The path says `login` even when registering** |
| Dayforce ID: register (SPA link) | `https://dfid.dayforcehcm.com/globalidentity/account/register` | From the Sign In page's "Create one now." |
| Security questions | `/globalidentity/account/register/securityquestions` | Client route |
| Email code | `/globalidentity/users/verification` | Client route. It works **only with router state**; a direct GET shows "Oops, Page Not Found!" |
| MFA choice | `/globalidentity/account/register/mfasetup` | Client route |
| Sign In | `/globalidentity/account/login` (`action=login` in returnUrl, or none) | |
| OIDC callback | `https://jobs.dayforcehcm.com/api/auth/callback/dfid` | `redirect_uri` in the authorize URL (observed) |

### Account required or not: `sitecontext.enableApplyWithoutAccount` (observed)
`GET /api/geo/{ns}/sitecontext/{ns}/{board}/en-US` (also in `apply.json` `dehydratedState`):

| Flag | Imperial Brady | NCMEC |
|---|---|---|
| `enableApplyWithoutAccount` | **false** | **true** |
| `enablePrivacyPolicy` | true | false |
| `hasActiveSMSProvider` | true | false |
| `enableDraftApplications` | true | false |
| `enableApplyWithPhoneNumber` | false | false |
| `clientCorrespondanceEmailAddress` | `notify@imperialdade.com` | `donotreply@ncmec.org` |

The global LaunchDarkly flags are the same for every tenant: `anonymous-applications-enabled: true`,
`authentication-enabled: true`, `captcha-enabled: true`, `block-user-creation: false`,
`apply-with-phone-number-enabled: false`. The tenant flag above decides.

### Flow-selection page (observed)
- Title "Apply | Dayforce Jobs". The only h2 is **"Already Have an Account?"**.
- **Guest tenants** (NCMEC) show a button **"Apply without an Account"** (`button.ant-btn.ant-btn-primary`, type=button).
- **All tenants** show:
  - a **"Sign In"** button (`button.ant-btn.ant-btn-primary.ant-btn-lg`, type=button);
  - the text "Don't have an account? **Create one now.**", where the link is `<a href="#">` (i18n
    `logged-out-content-register-text`).
- **Account tenants** (Imperial Brady) have no guest button.
- i18n also has "Use my profile" (`apply.apply-with-an-account`), shown when the candidate is signed in.

### "Create one now." is a cross-origin OIDC hop (observed network chain, Imperial Brady)
1. The click adds `#` to the URL. The page then calls `GET /api/auth/providers` and `GET /api/auth/csrf`.
2. It sends `POST https://jobs.dayforcehcm.com/api/auth/signin/dfid?acr_values=idp%3Aglobal_identity+api-version%3A2.0&clientNamespace=imperialdade&action=register&isoCultureCode=en-US&legalBaseUrl=…`
   with body `callbackUrl=/en-US/imperialdade/candidateportal/jobs/11166/apply&csrfToken=…&json=true`.
3. It loads the document `https://dfid.dayforcehcm.com/connect/authorize?client_id=DFJobs.UI&scope=openid profile offline_access&response_type=code&redirect_uri=https://jobs.dayforcehcm.com/api/auth/callback/dfid&…&action=register&…&code_challenge_method=S256`.
4. That redirects to `/Account/Login?ReturnUrl=…`.
5. That redirects to **`/globalidentity/account/login?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3F…action%3Dregister…`**.

**How the page decides login vs register (bundle `DT`):** `new URLSearchParams(new URLSearchParams(location.search).get("returnUrl")).get("action")`,
or router `state.action`. `"register"` renders `ET`, the registration page. Anything else renders the Sign In page.

### Dayforce ID registration: full chain (bundle, with Observed DOM per step)

| # | Page | Leave it by | Network | Result |
|---|---|---|---|---|
| 1 | **Create Your Account** (§3) | "Next" | Invisible reCAPTCHA `executeAsync()`, then `POST /globalidentity/users/verifyregistration` (header `RequestVerificationToken`) with `{FirstName, LastName, Email, ConfirmEmail, Password, ConfirmPassword}` | A pre-check only. **No account exists yet.** Success routes to `/register/securityquestions`. Personal info is kept in session state `register:personalInfo`, password included |
| 1b | Optional modal **"It looks like you have an existing account"**, testId `existingAccountMigration` | Its action | `POST /globalidentity/account/verifyshouldmigrateuser {Email, CompanyId}`, gated by LD `VerifyShouldMigrateUserEndpointEnabled` | Body: "We found your previous account on our older career site. After you register on our new portal, we'll move your old profile…". Continues to step 2 |
| 2 | **Security Questions** (§3) | "Next" (also "Back") | Questions from `GET /GlobalIdentity/SecretQuestions`. Then invisible reCAPTCHA again, then **`POST /globalidentity/users`** with `{FirstName, LastName, EmailAddress, Password, QuestionAnswerPairs:[{QuestionKey, Answer}], CaptchaToken, CompanyId:<clientNamespace>}` | **The account is created here.** Routes to `/globalidentity/users/verification` with state `{verificationEmail, queryParams}`, where `queryParams` is the search string with `action%3Dregister` changed to `action%3Dlogin` |
| 3 | **Enter Verification Code** (§3) | "Continue" | `POST /globalidentity/users/verifyemail {emailAddress, code}`. Resend: `POST /globalidentity/api/users/sendemailverification {email}` | Success routes to `/globalidentity/account/register/mfasetup{queryParams}` |
| 4 | **Multifactor Authentication** (§3) | "Save" | `POST /globalidentity/api/mfasetup`; phone options use `verifyphonenumber` | Then an account-linking check, then **`/globalidentity/account/login`**, with or without the modal "Your new account has been created. We found existing information associated with this email in one or more organizations, and we're linking it to your account now…" |
| 5 | **Sign In** (§3) | "Sign In" | Invisible reCAPTCHA, then the login POST, then the OIDC callback to `jobs.dayforcehcm.com/api/auth/callback/dfid` | Back to the job's `/apply` (the `callbackUrl`) as a signed-in candidate |

*Inferred, from i18n and sitecontext:* after the first sign-in, an account tenant like Imperial Brady (privacy policy and
SMS provider on) shows a privacy dialog, "I agree to the Privacy Statement" (`privacy-policy-agree-dialog-checkbox`),
and an SMS consent dialog, "Short Message Service (SMS) Consent" with "I consent to receive SMS messages" / "I do not
consent to receive SMS messages". Then comes the application flow with "Use my profile".

**Dayforce ID is one identity across all Dayforce clients** (register text: "…create your account, which will allow
you to apply directly with Dayforce's clients using the same credentials…"). After one tenant completes the chain,
every account-only tenant should sign in with the same email and password.

### Guest form (NCMEC, observed)
- A 3-step Ant Design wizard: **Candidate Info → Questionnaire → Submit**. It uses `.ant-steps-item-process` and
  `-wait`.
- Candidate Info sections: Personal Information, Resume Upload, Cover Letter, Additional Documents, Education History,
  Employment History, References, Work Hours Availability.
- **The form is server-defined.** `GET /api/geo/{ns}/jobapplication/{ns}/en-US/1/{jobId}` returns
  `{jobApplicationTemplateId, sections:[{sectionId, xRefCode, displayName, isRequired, sequence, fields:[{fieldId, fieldName, displayName, isRequired, isVisible, sequence}], questionnaire:{questions:[{questionId, xRefCode, displayName, isRequired, answerType, options}]}}], candidateAcknowledgement, candidateSourceStatement}`.
  - NCMEC sections: PERSONALINFORMATION (required), RESUMEUPLOAD, EDUCATIONHISTORY, WORKHISTORY, REFERENCES,
    DISABILITYQUESTIONNAIRE, VETERANQUESTIONNAIRE, AVAILABILITY, five custom questionnaires, and SUBMIT (sequence
    32767).
  - Disability requires "Candidate Name" and "Answered Date" (`answerType` 3). That matches the HH Global
    waiting_answer "Disability Questionnaire Answered Date".
  - `answerType` in the observed questions:
    - 1: display text;
    - 2: veteran radio, whose options are HTML descriptions;
    - 3: free text;
    - 4: dropdown, e.g. "Desired Salary" ranges;
    - 5: Yes/No;
    - 7: YES/NO/DECLINE.
    *Inferred* from observed questions; not a documented enum.
- **Option sources:**
  - `GET /api/geo/{ns}/applicationformfieldsoptions/{ns}/en-US` gives prefix and suffix.
  - `GET /api/geo/{ns}/candidatesource/{ns}/en-US` gives `candidateSources[{displayName, candidateSourceCode, requiresAdditionalExplanation}]`.
    Ten NCMEC sources; 9 of 10 require an explanation, so **a follow-up field appears after "How did you hear about
    this job?"**.
- **Resume import:** `POST /api/geo/{ns}/FileService/uploadresume/{ns}`, multipart field `fileToUpload`, parses and
  prefills. "Import Resume" appears twice: at the top and in Resume Upload.
- **Server step validation:** `POST /api/geo/{ns}/JobApplication/validatesections` returns
  `{success, validationMessages[], requestId}`.

---

## 2. Page tech (observed)
- **jobs.dayforcehcm.com:**
  - Next.js with `_next/data` JSON, React, Ant Design 5 (`ant-*`, `css-*` hashes) and next-auth (`/api/auth/*`).
  - LaunchDarkly client ID `62a0a637cc384b1581554ac5`, Google Maps, Azure App Insights.
  - i18n is shipped in `pageProps._nextI18Next.initialI18nStore["en-US"]`: namespaces common (264), apply (137),
    cookies (95).
- **dfid.dayforcehcm.com:**
  - An ASP.NET server page plus a React app (`GlobalIdentityClientApp/static/js/main.js`).
  - Uses Mantine-style `useForm` (`getInputProps`, `setFieldValue`, `validateInputOnChange:true`) and Dayforce's
    "Everest" web components `<evr-button>` and `<evr-checkbox>`, both **open shadow DOM**.
  - Scripts: jQuery 4.0.0, Bootstrap 5.3.6, jquery-validation 1.21.0 plus unobtrusive 4.0.0, `@microsoft/signalr`
    3.1.3, `StaticFiles/js/Validator.js`, Azure App Insights.
  - Inline config: `window.Dayforce = {GoogleRecaptchaSiteKey:'6LefHBYnAAAAADeXPZsiYH6oWbixfkQ2Te8pkJt7', LaunchDarklyDFJobsClientSideId:'62a0a637cc384b1581554ac5', AntiforgeryToken}`,
    plus `window.Errors = null` (server errors are injected here) and a hidden `__RequestVerificationToken`.
- **No iframes** except reCAPTCHA. No shadow DOM on jobs.dayforcehcm.com.
- **The permanent navbar is `ul.ant-menu.ant-menu-root.ant-menu-horizontal.navbar-menu[role=menu]`**, visible on every
  jobs.dayforcehcm.com page (see REA_FINDINGS 10-17).

---

## 3. Fields and widgets

### Job page (observed)
- Apply is `button[test-id="apply-button"][aria-label="Apply for {jobTitle}"]` with visible text "Apply".
  **A role lookup by name `^Apply$` fails**, because the accessible name is the aria-label.
- Cookie banner: h2 "Cookie Preferences", with `button[test-id="cookie-preferences-banner-accept-button"]` "Accept",
  `…-reject-button` "Reject" and "Cookie Settings". It is not modal and did not block clicks.

### Create Your Account (`dfid`, observed live and in the bundle)
**Identity signals.** Use these together:
- host `dfid.dayforcehcm.com`;
- document title **"Create Your Account | Dayforce Identity"**;
- `h1` **"Create Your Account"**;
- **`form#registrationForm[novalidate]`**;
- `returnUrl` contains `action%3Dregister`, or the path is `/globalidentity/account/register`.

| Element | DOM | Notes |
|---|---|---|
| First Name | `input#firstName[type=text][aria-label="First Name"][required][maxlength=128]` | **No `name` attribute on any field.** Labels are visual "First Name*" plus aria-label |
| Last Name | `input#lastName` (same shape) | |
| Email | `input#emailAddress[aria-label="Email Address (Required)"][autocomplete=email]` | `disabled` when the email is prefilled through router state |
| Confirm Email | `input#confirmEmailAddress[autocomplete=off]` | **copy, cut, paste, drop and drag are `preventDefault()`ed.** Observed: a synthetic paste event came back `defaultPrevented=true` |
| Password | `input#password[type=password][autocomplete=new-password]` | Typing re-validates Confirm Password |
| Confirm Password | `input#confirmPassword[type=password][autocomplete=off]` | Case-sensitive match |
| Agreement text | "Dayforce will use the personal information collected on this form to create your account… subject to Dayforce's Global Privacy Statement and Terms of Use." | Links `a[href="https://www.dayforce.com/privacy"][target=_blank]` and `a[href="https://www.dayforce.com/terms"][target=_blank]` |
| Consent | `<evr-checkbox checkedstate="unchecked|checked" status="default|error" arialabelcheckbox="I agree to Dayforce's Global Privacy Statement and to the Terms of Use, I clicked the links above and reviewed these documents.">`, with a shadow `input[type=checkbox][role=checkbox]` | Read state from the host's `checkedstate`. The engine already toggles the host (`forms.py set_checked`) |
| Next | `<evr-button>`, shadow `button[type=submit].primary.medium` "Next" | Not form-associated across the shadow boundary. A light-DOM `<input type="submit" hidden>` exists for Enter. **A Playwright click on `evr-button button` worked** (validation fired) |
| Have an account? | `a[href="/globalidentity/account/login"]` "Sign In" | **A sign-in-labelled control on the registration page** (see §7) |

**Hidden consent state (bundle).**
- The privacy link's `onClick` sets `privacyClicked=true`, and the terms link's sets `touClicked=true`.
- The checkbox validator fails unless **all three** are true: `policyAccepted`, `privacyClicked` and `touClicked`.
- Opening the URLs any other way, such as a new tab by URL or `window.open`, leaves them false.
- *Engine:* `forms.py open_agreement_links` clicks `target=_blank` anchors, which satisfies this. The production
  events show both "agreement_link" opens.

**Password rules (bundle `pT`, confirmed live).** Each failed rule adds a bullet; bullets are joined with `\r\n`:
- length ≥ 8: "• Password must be at least 8 characters";
- `[A-Z]`: "• Password must contain at least one uppercase character";
- `[a-z]`: "• Password must contain at least one lowercase character";
- **no character repeated 3+ times in a row** (`/(.)\1{2,}/`): "• Password cannot contain more than 2 consecutive
  characters";
- **a special character from `[ !\/"#$%&'()*+,-.:;<>?@[]^_\`{|}~ ]`**: "• Password must contain at least 1 special
  character: […]".
  - **`=` does not count.** Live: `Abcdef=1` failed only this rule.
  - **Digits are not required.**
  - Note that the regex in the bundle has no space, despite the message.

### Security Questions (observed by a direct GET of the client route)
- Title "Security Questions | Dayforce Identity", `h1` "Security Questions". Text: "If we need to confirm your identity,
  we'll ask you to answer the questions you choose."
- Three pickers `div[role=combobox][aria-haspopup=listbox][tabindex=0]`, with random ids (`1038d94qto`) and
  `aria-controls="<id>-dropdown-listbox"`. Labels "Security Question #1*" to "#3*".
- Each picker is followed by **`input#answer[aria-label="Answer"]`**. **All three answer inputs share `id="answer"`.**
- "Back" and "Next" are both `<evr-button>`s.
- Options: `GET https://dfid.dayforcehcm.com/GlobalIdentity/SecretQuestions` returns
  `{questions:[{key:<uuid>, active, value:"What is the name of your favorite childhood friend?"}, …]}`, 13+ entries.
- Validation (locale): "Question is required", "Answer is required", "**Each security answer must be unique**".

### Enter Verification Code (bundle plus locales; needs router state)
- Page title "Enter Verification Code". Text: "We've sent your verification code to **{email}**".
- **`form#emailVerificationForm`** contains **`input#verificationCode`** (label "Verification code", autocomplete off,
  required).
- The link-styled button "Didn't get it? **Resend Code**" sends `POST /globalidentity/api/users/sendemailverification`
  (see §4 for its toasts).
- "Continue" is the submit.

### Multifactor Authentication (observed by a direct GET)
- Title "Multifactor Authentication | Dayforce Identity", `h1` "Multifactor Authentication".
- **`div#mfaOptions[role=radiogroup]`** "Select your multifactor authentication method" holds native radios
  `name=mfaOptions`:
  - **"Email"**, checked by default, with a disabled `input#emailAddress`;
  - "Text Message";
  - "Phone Call";
  - **"I do not want to use multifactor authentication"**.
- "Save" is `<evr-button>` submit, `id=save` in the bundle.

### Sign In (observed)
- Title "Sign In | Dayforce Identity", `h1` "Sign In".
- **`form#loginForm[novalidate]`** holds `input#email[type=text][aria-label="Email"]` and `input#password` (no
  autocomplete attribute).
- "Sign In" is `<evr-button>` submit.
- Links: "Forgot Password?" (`/globalidentity/account/forgotpassword`) and "Don't have an account? **Create one
  now.**" (`/globalidentity/account/register`).
- Invisible reCAPTCHA.

### Guest form widgets (NCMEC, observed)
- **Ids are `jobPostingApplication_{section}_{field}`**, e.g. `_personalInfo_email`, `_educationHistory_0_degreeName`,
  `_workHistory_0_effectiveStart`, `_reference_0_mobilePhone`.
- **Each section is its own `<form>`**: `personalInfo`, `educationHistory-0`, `workHistory-0`, `reference-0`,
  `workHoursAvailability`. Each has its own **"Update" (type=submit)** and "Cancel" (type=reset). The wizard's
  **"Next" is type=button outside every form**.
- **Ant Select:** `input[type=search][role=combobox][aria-controls="{id}_list"]`.
  - Read-only (not searchable): Prefix, Suffix, Preferred Contact Method, How did you hear.
  - Searchable: Country, and State/Province, which is **disabled until Country is chosen**.
  - Phone country codes are `rc_select_4` / `rc_select_5`, **dynamic ids**.
- Dates are **native `input[type=date]`** (Start Date / End Date). GPA is Ant `InputNumber` (`role=spinbutton`).
- **Files:**
  - **Two inputs share `id="jobPostingApplication_files_resume"`**: Import Resume and Resume Upload. They accept
    `doc,docx,pdf,rtf` (no dots).
  - Cover letter takes the same types.
  - Additional documents take `doc,docx,jpg,pdf,png,rtf,tif,tiff,xls,xlsx`.
  - Additional-document upload goes to `/FileService/uploadadditionaldocument/{ns}/{fileType}`.
- **Optional history sections open with an empty editor.** Their required sub-fields block "Next" until filled or
  cancelled.

---

## 4. Validation (exact wording)

**Dayforce ID (observed live and in locales)**
- **Inline.** Each field gets `aria-invalid=true`, and `aria-describedby` points at the message. The message renders
  in `.validation-text-module__evrValidationText…`.
- Messages:
  - "First name is required", "Last name is required", "Email is required", "Email is invalid", "Confirm email is
    required", "Emails do not match", "Password is required", "Confirm password is required", "Passwords do not
    match";
  - consent: "**You must click on the links to review the policy statement and the terms and conditions, and then
    click the checkbox to indicate you agree to them.**".
- **Toast.** `div.toast.bg-danger.show[role=alert]` "**You must complete all required fields.**".
- **Server-side outcomes of "Next"** (bundle):
  - a `ValidationText` under the checkbox: "**Registration failed. Try again using a different email address and/or
    password.**";
  - or a danger toast: "**Your request can't be completed right now. Try again in a few minutes.**".
  - Either way the reCAPTCHA is reset.
- **Verification code:**
  - "Code is required";
  - toast "**Code is not valid or has expired**";
  - resend success: "New verification code sent";
  - resend rate limit: "**We've already sent a code to your email.**";
  - resend failure: "Failed to send verification code".
- **Sign In:** "You used an incorrect email address or password. If you don't have an account, click create one now".
  MFA: "This multi-factor authorization code isn't correct. Try again.".

**jobs.dayforcehcm.com (observed and i18n)**
- **Ant inline errors.** `.ant-form-item-explain-error` sits inside `.ant-form-item-explain[role=alert]`, and the item
  gets `ant-form-item-has-error`.
  - Wording is "'{Label}' is required.", e.g. "'Email Address' is required.".
  - "'Mobile Phone Number' is required" has **no period**.
- Uploads:
  - "Your document wasn't uploaded. Try again later.";
  - "Your document wasn't uploaded. Remove extra dots (.) from the file name and try again.";
  - "The system can't read the file you selected. Try again with a different file." with "Supported file formats are
    DOC, DOCX, PDF, or RTF.";
  - "Your resume was uploaded. However, the application couldn't use it to fill in your profile. Please complete the
    fields below.".
- Other: "Please enter the correct email address", "Phone number is invalid", "The end date must be after the start
  date.".

---

## 5. Bot protection
- **reCAPTCHA v2, invisible, sitekey `6LefHBYnAAAAADeXPZsiYH6oWbixfkQ2Te8pkJt7`**, on both hosts.
  - Bundle fallback key: `6LdS6lkUAAAAALh4_zk9chaM_ndK4flAZkWhBDXj`.
  - Gated by LD `captcha-enabled` (true).
  - Dayforce ID loads `https://www.google.com/recaptcha/api.js?onload=onloadcallback&render=explicit`. The React
    component is `size:"invisible", type:"image"`.
  - **It gates (bundle):**
    - registration "Next";
    - security-questions "Next" (`CaptchaToken` in `POST /globalidentity/users`);
    - Sign In;
    - the **guest** application submit (`captchaToken` in `jobpostingapplication`; `useCaptcha("guest"===mode)`).
  - **Observed at rest:** the anchor iframe is the 256×60 badge, and the `bframe` iframe is viewport-sized (1440×913)
    but `visibility:hidden` inside a container at `top:-10000px`.
  - *Inferred:* when Google escalates, that container becomes visible. The image challenge then covers the page, and
    clicks underneath do nothing.
- **Cloudflare JSD oneshot** on jobs.dayforcehcm.com (`/cdn-cgi/challenge-platform/h/b/jsd/oneshot/…`). No challenge
  page was seen.
- **Anti-phishing beacon** on dfid: an inline script reports to a third-party host if the hostname is not
  `*.dayforcehcm.com`. Irrelevant to the filler.
- No hCaptcha, DataDome or PerimeterX.

---

## 6. Submit
- **Guest submit (bundle):**
  1. Invisible reCAPTCHA `executeAsync()`.
  2. `POST /api/geo/{ns}/JobApplication/jobpostingapplication` with
     `{captchaToken, anonymousUserPrivacyPolicyInfo?, clientNamespace, isoCultureCode, jobBoardId, jobPostingId, jobPostingApplication:{personalInfo, files, sections, jobApplicationSource:"manual"}, seekToken, …}`.
- **Success (i18n):**
  - "**Congratulations, your application has been submitted!**";
  - or "Congratulations, your application has been submitted. The confirmation number is {{confirmationNumber}}. You
    must save this confirmation number now…";
  - plus profile prompts ("Your information has been saved to your profile for future applications") and assessment
    variants ("The assessment will open in a new tab…").
- **Failure:** toast "Your application wasn't submitted. Try again later." with an optional "Error Code: {requestId}".
- **Duplicates:** "You've already applied for this job" / "Our records indicate that you already applied to this job
  posting.".
- The final step's acknowledgement is "**I agree to the Candidate Acknowledgement**", plus an optional "(Optional) I
  agree to be considered for other opportunities relevant to my profile".
- The candidate's acknowledgement email comes from the tenant's `clientCorrespondanceEmailAddress`, e.g.
  `notify@imperialdade.com`.

---

## 7. Engine risks, and why Imperial Brady and Security Benefit never got through

### What production shows (observed)
**The two stalled runs follow the same sequence** (10-16 23:52–23:54):
1. The job page opens. Then `…/apply?flowSelection=true` is classed "Dayforce: unknown".
2. The navigator clicks "Create one now.". The page changes to the 7-field Create form.
3. Generic fill. Both agreement links are opened by clicking, and the read-back is OK.
4. **"Creating an employer account"**, which is the click on "Next".
5. **8–9 s later: "Signing in with the saved account".**
6. "Sign In" clicked three times. Every time: `page_changed:false, validation_changed:false`.
7. "Create one now." clicked. Delta: **+6 fields, −1 field**.
8. Repeated "fill" decisions follow, **never "Next"**, until "The controller repeated refused strategies".

**Account history (observed).**
- From 10-07 (Sarnova) to 10-16 23:30, **every** Dayforce account run logged "Signing in with the saved account".
- No run ever logged "Answered the account's security questions".
- No Dayforce ID code was ever matched; every mail poll has `candidate_count: 0`.
- **No Dayforce ID account for this email has ever been created by the engine.** It has been signing in with locally
  saved credentials that Dayforce never accepted.

### Why (inferred, ranked)
1. **The account state machine counts the click, not the result.**
   - `accounts.py` saves `created_locally` *before* clicking.
   - It marks `authenticated` as soon as **no password field remains**. That happens at Security Questions, two steps
     before the account exists.
   - On the next pass, any page with one password field is treated as sign-in.
   - `accounts.py`: `creating = len(passwords) > 1 or autocomplete=="new-password"`, and the run logged "Signing in"
     at 23:53:30. So the page then had **one** password field without `new-password`, which is the **Dayforce ID Sign
     In page**.
   - The later +6/−1 delta on "Create one now." is exactly Sign In (`#email`, `#password`) becoming register.
2. **The outcome of "Next" was never read.** It has six possible outcomes:
   - a. the route moves to `/register/securityquestions`, whose h1 is "Security Questions";
   - b. a visible reCAPTCHA image challenge, where the bframe container leaves `top:-10000px`;
   - c. "Registration failed. Try again using a different email address and/or password." under the checkbox;
   - d. the danger toast "Your request can't be completed right now…";
   - e. inline errors plus the toast "You must complete all required fields.";
   - f. the existing-account modal.

   The run logged no URL and no message for any of these. `report_unchanged` only fires when the path is unchanged
   *and* password fields remain. Either way the next pass flipped into sign-in mode.
3. **The registration page carries a "Sign In" link** ("Have an account? Sign In", `a[href="/globalidentity/account/login"]`).
   A controller in sign-in mode that matches `sign in` navigates to the Sign In page. That is how the run reached it
   without an account.
4. **Signing in could never succeed.** There was no account behind the credentials. "Sign In" changing nothing fits
   either a visible invisible-reCAPTCHA challenge, which intercepts clicks, or an unread error. *Not determined*: no
   URL, captcha state or toast was logged.

### Other risks
5. **The flow-selection page is not recognised** ("Dayforce: unknown"). Decide from `sitecontext.enableApplyWithoutAccount`
   (and the "Apply without an Account" button). The guest path avoids Dayforce ID entirely.
6. **Path ≠ page.** `/globalidentity/account/login` serves both registration and sign-in. Classify by `h1` / `form#…`
   / `action`.
7. **Shadow-DOM controls.** "Next", "Sign In", "Save" and "Back" live inside `<evr-button>`, and the consent box inside
   `<evr-checkbox>`. A DOM scan that does not pierce open shadow roots will not see them as buttons.
8. **Duplicate ids**: `#answer` ×3 on Security Questions, and `#jobPostingApplication_files_resume` ×2 on the guest
   form.
9. **The email-code page cannot be reloaded.** `/globalidentity/users/verification` needs router state, and a reload
   or reopen gives "Oops, Page Not Found!". Do not "reopen the saved route" between POST `/globalidentity/users` and
   `verifyemail`.
10. **Mail.**
    - The Dayforce mail rule has `link_origins ["https://jobs.dayforcehcm.com"]`.
    - The Dayforce ID message carries a **code**, sent while the page is on `dfid.dayforcehcm.com`.
    - `mail.py` lists `dayforcehcm.com` and `dayforce.com` as Dayforce ID senders. The exact sender address was not
      observed, since no email was sent.
    - Resend is rate-limited: "We've already sent a code to your email.".
11. **The permanent Ant navbar `[role=menu]`** breaks "any open menu" checks (already known).
12. **Section forms.** Clicking a section's "Update" submits only that section, and "Cancel" (type=reset) clears it.
    The wizard advances only through "Next" (type=button). Optional sections open with an empty editor that blocks
    "Next".
13. **Accessible name ≠ text.** For example "Apply" vs `aria-label="Apply for {title}"`.

---

## Engine checklist (testable)

**Routing**
- [ ] Read `GET /api/geo/{ns}/sitecontext/{ns}/{board}/en-US` (or `apply.json` `dehydratedState`) before Apply. If
      `enableApplyWithoutAccount` is true, the run must click "Apply without an Account" and never reach `dfid`.
      Expected: NCMEC true, Imperial Brady false.
- [ ] Recognise `…/apply?flowSelection=true` as "Dayforce: flow selection", with h2 "Already Have an Account?". It
      must never be "unknown".

**Recognise each Dayforce ID page.** Each of these is a unit test on saved DOM:
- [ ] **register**: host `dfid.dayforcehcm.com`, title "Create Your Account | Dayforce Identity", `form#registrationForm`,
      and the inputs `#firstName #lastName #emailAddress #confirmEmailAddress #password #confirmPassword`.
- [ ] **security questions**: path `/globalidentity/account/register/securityquestions`, h1 "Security Questions",
      3× `div[role=combobox][aria-controls$="-dropdown-listbox"]`, and 3× `input#answer`.
- [ ] **code**: `form#emailVerificationForm` with `#verificationCode`, title "Enter Verification Code".
- [ ] **mfa**: path `/register/mfasetup` and `div#mfaOptions[role=radiogroup]`.
- [ ] **sign-in**: title "Sign In | Dayforce Identity", `form#loginForm`, `#email` and `#password`.

**Create Your Account**
- [ ] Before "Next", all of these hold:
      - both agreement anchors were **clicked**;
      - `evr-checkbox[checkedstate=checked]`;
      - password passes the five rules: ≥8 characters, upper, lower, a special from the list (**not `=`**), and no
        triple repeat;
      - Confirm Email was typed, not pasted.
- [ ] Find "Next" through the shadow root (`evr-button >>> button[type=submit]`). A Playwright click must make either a
      `POST /globalidentity/users/verifyregistration` or visible validation.
- [ ] After "Next", wait for **one** outcome and log it with the URL:
      - a. a 200 response from `verifyregistration`, and then the route `/register/securityquestions`;
      - b. a visible reCAPTCHA challenge (the bframe container `visibility:visible` and not at `top:-10000px`) → the
        captcha path;
      - c. "Registration failed…" → stop, and say the email may already have a Dayforce ID; use sign-in or reset, and
        never re-create;
      - d. "Your request can't be completed right now…" → retry later;
      - e. inline errors → repair;
      - f. the modal `existingAccountMigration` → accept.
- [ ] The run must never switch to sign-in because the registration page stayed in place. Saved credentials count as
      an account only after `POST /globalidentity/users` returns 2xx or the "Enter Verification Code" page appears.

**Security questions**
- [ ] Pick 3 **distinct** questions from `GET /GlobalIdentity/SecretQuestions` or from the listbox. Type 3 **unique**
      answers into the three `#answer` inputs **by index**, and store them with the account before clicking "Next".
- [ ] After "Next", expect `POST /globalidentity/users` with 2xx, then "Enter Verification Code".

**Email code**
- [ ] Do not reload this page. Arm the mail wait **before** clicking "Next" on Security Questions. Accept a Dayforce
      code email while the page origin is `https://dfid.dayforcehcm.com`.
- [ ] Type the code into `#verificationCode` and press "Continue". Expect `POST /globalidentity/users/verifyemail` with
      2xx and the route `/register/mfasetup`.
- [ ] On "Code is not valid or has expired", resend once at most. "We've already sent a code to your email." means
      wait; do not resend again.

**MFA and sign-in**
- [ ] Choose the radio "I do not want to use multifactor authentication", then "Save". Expect `/globalidentity/account/login`.
- [ ] Sign in with the same credentials. Expect a navigation to `jobs.dayforcehcm.com/api/auth/callback/dfid` and then
      `…/jobs/{id}/apply`.
- [ ] Store the account once for **Dayforce ID globally**. A second account-only tenant must reuse it.

**Guest form**
- [ ] Fill using the `jobapplication` definition, keyed by `jobPostingApplication_*` ids. Choose Country before
      State. Expect the extra "explanation" field after a source with `requiresAdditionalExplanation`.
- [ ] Advance only with the wizard "Next" (type=button). Never click a section's "Cancel". For each optional empty
      editor, either fill its required fields or press its own "Cancel".
- [ ] Success means the text "Congratulations, your application has been submitted" (with or without a confirmation
      number). Failure is the toast "Your application wasn't submitted. Try again later.".
