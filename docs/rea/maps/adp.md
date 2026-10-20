# ADP Workforce Now career center (workforcenow.adp.com/mascsr/default/mdf/recruitment) — behaviour map

Date: 2026-10-19. Read-only REA on a dedicated headless Chrome (port 9341), never the worker Chrome. Nothing was typed
into ADP from the probe and no account was created; the identity evidence comes from production runs' saved pages and
screenshots (Avalon `d58823e4`, `98d88b6e`; ZincFive `78f8ebc8`).

**Evidence**
- Live probe of Avalon's job page (`cid=47dbbe30-…&jobId=524393`): 128 scripts, 19 XHRs, the recruitment bundle
  `recruitment.80c11cc83cffc4d7.js` (saved under the scratchpad `rea-adp/js/`).
- Production final screenshots of the registration page after "Continue" (three runs, two email forms).

**Conventions.** **Observed** = seen live or in a saved production page. **Inferred** = my reading of the bundle.

---

## 1. Workflow (observed)

1. Job page `…/recruitment.html?cid={tenant}&jobId={req}` → **Apply**.
2. **Sign In** panel: one field, "Email or Mobile Number", then a one-time code (OTP) to that identifier.
   Calls `GET …/candidate/login/allow-login?cid=` (whether the login form is enabled) and
   `POST …/candidate/login/signin`.
3. Sign-in error codes (bundle `handleErrorCodes`):
   - `4010`, `4041`, `4040` (Person Not Found) → `reDirectToApply`: guest apply ("Tell us about yourself").
   - `4011` → account locked dialog.
   - `4084`, `4008` → masked-identifier dialog ("we found your profile", verify by code to a masked email/phone).
   - `8090` → retry; `RATE_LIMIT_EXCEED` → "@@TAL_REC_ECC_OTP_LIMIT_EXCEED".
   - anything else → the server's own `errorMsg` in a red banner.
4. **Guest registration** ("Tell us about yourself"): First Name, Last Name, Email, Mobile Number (country picker),
   **Continue** → `POST …/v2/candidate/login/person-otp-account.register` (bundle name `RegisterGuest`). Success opens
   the OTP dialog; the code goes to the email (or phone).
5. A classic **ADP ID + password** login also exists (`adpId`, `adpPass`, `renderClassicADPLogin`, "What's an ADP ID").
6. After verification: the application form (not reached in production yet).

## 2. Identity model (observed + inferred)

- The info banner on registration: "the mobile number or email associated with this verification process is intended
  to be unique to you. Any subsequent use of the same mobile number or email by another user will become associated
  with the new user and override the first name, last name, work history, education, or skills associated with your
  profile."
- **Observed:** registration with `local@gmail.com`, `local+adp@gmail.com` **and** `local@googlemail.com` (same phone)
  all answer `40014`: "Another user account already exists with the provided email address. Login using an alternate
  mobile number or an email address."
- **Inferred:** ADP canonicalizes Gmail addresses (plus tags, googlemail.com) onto one mailbox, and that mailbox already
  belongs to an ADP identity (likely a classic ADP account from an earlier employer or school). Sign-in at a new
  tenant answers `4040` because that identity has no person record there, and guest registration then refuses the
  canonical email. **No Gmail variant can register a second ADP identity.**

## 3. Bot protection (observed)

- F5 Distributed Cloud Bot Defense (Shape): `ponos.zeronaught.com` and `POST …zeronaught.com/v1.0/web/get-cse` on page
  load. No production run has been refused by it yet.
- OneTrust cookie banner (`cdn.cookielaw.org`), Pendo analytics.

## 4. Engine status and risks

1. **Identity (blocks both open jobs, Avalon and ZincFive).** Needs the owner: either a non-Gmail email to apply on
   ADP (then `ats_email_aliases`-style per-platform address), or the classic ADP ID/password for the existing account.
   The engine already stops cleanly on `signin 4040` + `register 40014` (`forms.py account_conflict`) instead of
   repeating registration.
2. **The masked-identifier dialog (`4084`/`4008`)** is not handled yet; once an identity resolves, expect "Verify it's
   you" with a masked email or phone and an OTP. Email OTPs work through the mailbox; phone OTPs cannot.
3. **OTP rate limit.** `RATE_LIMIT_EXCEED` shows "@@TAL_REC_ECC_OTP_LIMIT_EXCEED"; never loop code requests.

## Engine checklist (testable)
- [ ] `signin` 4040 → registration form, never a second sign-in.
- [ ] `register` 40014 → stop with an identity-conflict reason naming the owner's options (non-Gmail email or ADP ID).
- [ ] `4084`/`4008` → read the masked identifier; choose the email option; enter the emailed code.
- [ ] Never request more than one OTP per minute per tenant.
