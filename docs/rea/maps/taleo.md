# Taleo (Oracle Taleo Enterprise careersection, and Taleo Business Edition) — behaviour map

Date: 2026-10-18. Read-only, on a dedicated headless Chrome (port 9341), never the worker Chrome. Nothing was
submitted, nothing was typed, and no account was created. I opened public pages, pressed "Apply Online" and "New User" (both
only navigate; the registration form was never filled), and read production's saved pages.

**Two products share the host name `taleo.net`**

| Product | URL shape | Tenants in production |
|---|---|---|
| Taleo Enterprise (EE) careersection | `https://{tenant}.taleo.net/careersection/{section}/jobdetail.ftl?job={req}` | Bealls (the one confirmed run), Kearney, Textron, D.R. Horton, Texas Health, Weyerhaeuser |
| Taleo Business Edition (TBE) careers v2 | `https://phf.tbe.taleo.net/phf02/ats/careers/v2/viewRequisition?cws=41&org={ORG}&rid={n}` | Costco (9 jobs) |

**Pages probed live**

| Page | URL | Notes |
|---|---|---|
| Bealls job page | `beallsinc.taleo.net/careersection/bealls+outlet+mobile/jobdetail.ftl?job=260000GI` | EE. Walked: job page, Apply Online, Login, New User Registration |
| Costco job page | `phf.tbe.taleo.net/phf02/ats/careers/v2/viewRequisition?cws=41&org=COSTCO&rid=10880` | TBE. Walked: job page, Apply Now, "Email Registration" |
| Production saved pages | Bealls runs `ae634a32…` (confirmed) and `d92605ed…` (Region error) | Personal Information, Education, Attachments, eSignature, Review, Thank You |

**REA evidence**

| Evidence | What it covers |
|---|---|
| `ev_6ad79f39eb409e54abf4d0b87a31e98c8be4c8d1b5d412bcd44b198057242200` | inspect-web-page on the Bealls EE job page, with scripts, JSON shapes and accessibility. Framework script `careersection/2026PRD.1.4.14.3.0/js/ftlallc.js` (131 KB) and a hidden 4x4 iframe `html/ajax.htm`. REA retained no script text, so I read `ftlallc.js` with a plain GET |

**Other sources**: production data (16 jobs: 1 confirmed, 15 skipped by role policy; 9 submit runs: 1 confirmed, 8 waiting_browser),
saved step JSON, screenshots and HTML, and `adapters/others.py`, `forms.py`, `browser.py`.

**Conventions.** **Observed** means seen live or in a saved production page. **Inferred** means my reading, not verified
end to end.

---

## 1. Workflow

### Taleo Enterprise (observed, Bealls)

| Step | URL | What it is |
|---|---|---|
| Job page | `…/careersection/{section}/jobdetail.ftl?job={req}` | `form#ftlform`; buttons "Apply Online" (top and bottom) and "Add to My Job Cart" |
| Login | `…/careersection/iam/accessmanagement/login.jsf?lang=en&redirectionURI=<application.jss URL>` | Heading "Login": "User Name" and "Password", buttons "Login" and "New User", links "Forgot your user name?", "Forgot your password?", "Use your Talent Exchange Universal Profile to login", Google, Yahoo! |
| Registration | `…/iam/accessmanagement/register.jsf` (form action; the URL bar stays on `login.jsf`) | "New User Registration": User Name*, Password*, Re-enter Password*, Email Address, Re-enter Email Address; buttons "Register" and "Cancel" |
| Application | `…/careersection/application.jss?lang=en&type=1&csNo={n}&portal={n}&reqNo={n}`, then `…/careersection/candidateacquisition/flow.jsf` | Server-session JSF wizard (below) |
| Receipt | same `flow.jsf` URL | "Thank You / Process completed / Thank you for your job application. If your profile meets the requirements, a member of our team will contact you." and a banner "{Name}, you are signed in." |

### The wizard pages (saved scans; the sequence depends on what the draft already holds)
1. **Personal Information**, Source Tracking, Job Posting Notification (one page).
2. **Education** ("Education 1": Institution, Country, "Did you Graduate?", Program, GPA, Education Level; "Add Education",
   "Remove Education").
3. **Attachments** (a file input "Select the file to attach", "Comments about the file", an explicit **"Attach"** button).
4. **eSignature** ("Select a language"; "Please enter your full name: ." required).
5. **Review and Submit** (sections Personal Information, Source Tracking, Job Posting Notification, General Questions and the attached resume
   file name, each with "Edit"; buttons "Submit" and "Save as Draft").
- Every page has "Save and Continue" and "Save as Draft". Each click is a full server postback.

### Account model (observed in production)
- **Per tenant, username plus password, no email code and no captcha** on Bealls.
- 10-17 02:00 first run: "sign in: bad_credentials" -> "Taleo: create account" -> "create: ok" -> straight into the form.
  03:26 the run that confirmed: "sign in: ok" with the saved account, then Education, Attachments, eSignature, Review, Submit, receipt in 42 s.
- The registration form marks only User Name and the passwords required; Email is optional on this tenant.
- There is no guest apply: "Apply Online" always goes to Login first.

### Taleo Business Edition (observed live, Costco; never run by the engine)
- Job page: `a.btn` "Apply Now" -> `…/applyRequisition?org=COSTCO&cws=41&rid=10880`, and "Login Page" ->
  `…/candidateLogin?org=…&cws=41&rid=…`.
- `applyRequisition` is titled "Email Registration" and is **account creation plus the application in one form**, `form#TBE_theForm`
  posting to `…/voluntaryInfo`: Email*, Password*, "Re-type new password"*, First and Last name*, Middle, Primary Phone*, Secondary
  Phone, Street, City, State* (53 options), ZIP*, Country (185), Source* (33), "Other (Specify Source)", Referred By; resume
  and attachment uploads (`resume_template` and `attachment_template` file inputs with companion text inputs `uploaded_resume`, `uploaded_attachment`);
  employer questions `answer_<id>` (e.g. "Salary expectations:*" and "Availability to start:*" textareas, a long checkbox group
  `answer_129`). No captcha or iframe was present.

---

## 2. Page tech

**Enterprise (observed)**
- Server-rendered Faces pages with a hidden-state form: `form#ftlform` carries `ftlpageid`, `ftlcompid`, `ftlinterfaceid`, `ftlcompclass`,
  `ftlhistory`, `ftlPageHistory`, `ftlstate`, `ftlwinscr`, `jsfCmdId`, `ftlerrors`, `portal`, `tz`; the registration and login forms
  carry `javax.faces.ViewState`.
- Buttons are `input[type=button].inputbutton` or `.nav-btn`. Their **accessible names differ from their visible text** ("Login" is "Log in to
  the system"; "New User" is "Access the registration page"; "Register" is "Create an account"). Ids repeat: `requisitionDescriptionInterface.BOTTOM_A` is on both
  "Apply Online" and "Add to My Job Cart".
- **Session management in `ftlallc.js`:** a session timeout and warning timer (`ftlUtil_installSessionBeacon`), a hidden confirm button
  `topNavInterface.answerConfirmBeaconReset` labelled "Reset the timer", and an `onbeforeunload` guard that warns when the session is invalidated by
  leaving the domain (`ftlISWLD`).
- A 4x4 hidden iframe `html/ajax.htm` is the AJAX transport; `jobdetail.ajax` is called on Apply.
- **The wizard lives in the server session.** Per the engine's own note (`browser.py:1380`), reopening a `.jsf` URL gives an inert shell that reads
  as "career section unavailable". Production recorded one such false maintenance (run `0b7ad29a`), and `browser.py:1380` now restarts these saves
  from the posting. I did not re-test it: `application.jss` is untested.
- No bot product was seen (no hCaptcha, reCAPTCHA, DataDome or Cloudflare script on the pages I loaded).

**Business Edition (observed):** Bootstrap pages (`btn btn-primary`, `form-control`), one real form `TBE_theForm`, no iframes.

---

## 3. Fields and widgets (Enterprise, saved scans)

### Personal Information (Bealls)
First Name*, Preferred Name, Middle Name, Last Name*, Street Address (line 1)*, Address (line 2), City*, Zip/Postal Code*, Email Address*
(prefilled), "Primary Number"* (select Home Phone / Cellular Phone), Home Phone Number, Cellular Number, Desired Type of Employment (Full-Time,
Part-Time, Flex), Expected Pay Rate, a free-text "why you are an ideal candidate", "Are you 18 or older?"* (Yes/No),
Source Type* (8 options), "Send an email notification whenever a new position matching this profile is posted." (checkbox, prefilled checked).

### Place of Residence: a three-level dependent picker
- A `fieldset` whose legend reads "Place of Residence . Required" holds three selects with ids ending `…ResidenceLocation-0` (Country),
  `-1` (State/Province) and `-2` (Region). Each has `onchange="MenuHierarchyRenderer.updateMenu(event, '<prefix>ResidenceLocation', true)"`: the
  next level's options are filled by script.
- **Region lists localities (Aberdeen, Annapolis, Baltimore, Bel Air, …) for the chosen state, not the state code.** The scan marks Region
  not required, yet the page rejects the save without it (§4).
- **Source Type -> "Social Networks"**: choosing some source types adds a required select "Social Networks" (6 options: Facebook, Google+, Instagram,
  LinkedIn, …). The page is re-scanned after each change.

### Other widgets
- Radio "Did you Graduate?"; Education Level select; Education Country select. Selects show only a slice of their options in the scan (15 of
  hundreds), so read them from the DOM when needed.
- Education "Add Education" and "Remove Education" buttons open or remove rows in place.
- A visible "Format: 555-555-5555" hint beside the phone inputs, with a pattern check (§4).
- Attachments: `input[type=file]` plus **"Attach"**; the file name appears in a list, and the Review page repeats it.

---

## 4. Validation (exact wording from saved production pages)
- A page banner: "Errors occurred on this page, could not proceed. Please correct the errors before proceeding. Beginning of error list.",
  plus a skip link "The page includes some errors. Go to the error list."
- "**You must select ALL possible values in the section Place of Residence.**" (the Region picker, which is not marked required).
- 'The field "GPA" contains an invalid value' (for "3.9/4.0"; it accepts "3.9").
- "The value does not match the pattern 999-999-9999" (after "(555) 555-0100"); "you must enter a number in the "Cellular Number" field" (the
  chosen Primary Number type has no value).
- Production's other stops: "The browser did not advance within its recovery budget" (4 of 8 failed runs), a false maintenance page (1),
  a widget crash at `widgets.py:896` (1).

---

## 5. Bot protection
- **None observed.** No captcha on the login or registration forms, and no email or SMS code. The only session control is the inactivity beacon in §2.
- TBE's registration page showed no captcha either.

---

## 6. Submit and receipt
- **Submit:** the Review page's "Submit" (an `input[type=button]` inside the JSF form) posts and returns the Thank You page on the same `flow.jsf` URL
  (observed in the confirmed run, 4 s after the click).
- **Receipt (observed):** heading "Thank You", "Process completed", "Thank you for your job application…". The engine's `CONFIRM_TEXT` matches
  "thank you for your job application".
- "Save as Draft" is on every page; it is not a submission.

---

## 7. Engine risks, ranked by jobs recovered

No Taleo job is blocked by an engine fault today: 1 confirmed, 15 skipped by role policy (Textron, D.R. Horton, Costco and others never ran). The risks
below matter for the next batch.

1. **Hierarchical pickers where a page error names a field that is not marked required** (Region, Cellular Number). Two Bealls runs stopped on it
   explicitly, and *inferred*: some of the four "did not advance" runs hit the same banner. `forms.py:3105` and `error_named_fields`
   (`forms.py:3436`) already promote error-named fields. The 03:26 run that confirmed started at Education, because Personal Information was already
   saved in the draft, so the Region choice itself was never re-exercised after that fix.
   - **Residual risk:** the *choice* of Region. The offered localities are Baltimore-area names and the saved state is "MD".
   - **Fix:** in the answer resolver (`answers.py`) treat `…ResidenceLocation-0/-1/-2` as one group: fill Country, then State/Province, then re-scan,
     then pick the offered locality nearest the profile city (fallback to an "Other" option), and never leave a deeper level empty. Detect the
     fieldset by its legend "Place of Residence" or the id suffix.
2. **Session-bound URLs read as maintenance.** Reopening `flow.jsf` shows "career section unavailable". `browser.py:1380` handles `.jsf`
   only. Test `application.jss` the same way and extend the check if it behaves alike, and make the maintenance classifier require the maintenance
   wording, not just "no controls". Never navigate the signed-in tab to another domain (the `ftlISWLD` guard can invalidate the session).
3. **Taleo Business Edition is untested.** 9 Costco jobs sit on `phf.tbe.taleo.net`. Its `applyRequisition` form creates the account and holds the application,
   with a resume upload whose companion text input (`uploaded_resume`) may be filled by script after the file is chosen. Before enabling:
   - classify `/applyRequisition` as FORM with a password pair (not a bare CREATE_ACCOUNT step);
   - wait for `#uploaded_resume` to hold the file name after attaching;
   - read the `answer_<id>` textareas and the `answer_129` checkbox group as one multi-select question;
   - record the receipt text on the first confirmed run.
4. **First sign-in attempt before registration** (run `4a2de68e`: "bad_credentials" then "create"). Harmless on Bealls; if a tenant locks accounts
   after failed logins it costs an account. Prefer "New User" when `AccountStore` has no record for the tenant origin
   (`adapters/others.py` `Taleo.on_sign_in`).
5. **Do not click "Reset the timer"** (`topNavInterface.answerConfirmBeaconReset`) and ignore the hidden 4x4 `ajax.htm` iframe when scanning.
6. **Duplicate ids** (`requisitionDescriptionInterface.BOTTOM_A`) make CSS id locators ambiguous; locate controls by the engine's own handle or by
   label and position.

---

## Engine checklist (testable)

**Job and login**
- [ ] On a `jobdetail.ftl` page the apply resolver returns one "Apply Online" control (top and bottom share a label). Unit test on the saved Bealls job page.
- [ ] After Apply, the URL is `…/iam/accessmanagement/login.jsf` with the fields "User Name" and "Password". With no saved account for the tenant,
      click "New User", expect the "New User Registration" heading and `register.jsf` as the form action, then "Register". After success, the next page is
      the wizard (not an email wait).

**Wizard**
- [ ] On the Personal Information page, fill Country, then State/Province, then Region (nearest locality), then check that no error banner mentions
      "Place of Residence" before "Save and Continue".
- [ ] A phone shown with "Format: 555-555-5555" is written in that mask; GPA is a bare number; Primary Number matches a non-empty number field.
- [ ] Attachments: choose the file, click "Attach", and wait for the file name before "Save and Continue".
- [ ] eSignature: fill "Please enter your full name".

**Submit**
- [ ] Review and Submit shows "Submit"; one click leads to a page with "Thank You" and "Process completed". Record that text as the receipt.
- [ ] A reopened `flow.jsf` or `application.jss` is never classed as maintenance without the maintenance wording; restart from the posting.
