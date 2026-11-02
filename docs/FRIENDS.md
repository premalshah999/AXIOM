# Giving AXIOM to a friend

AXIOM is not signed with an Apple Developer ID yet, so the first open takes a few clicks in System Settings. The same steps are in a
text file inside the disk image, beside the app and the Applications folder: `desktop/dmg-readme.txt`.

## Message to paste

> Here is AXIOM: it finds jobs that fit you and applies for you. Download AXIOM.dmg from the link, open it, and drag AXIOM onto
> Applications (use Finder, not Terminal).
>
> The first time you open it, the Mac says "Apple could not verify AXIOM". That is normal: I haven't paid Apple to approve it.
> Click Done. Then open the Apple menu, System Settings, Privacy & Security, scroll down to Security, and click Open Anyway next to
> "AXIOM was blocked". Enter your Mac password, then open AXIOM again and click Open or Open Anyway if it asks. You only do this once
> for each version.
>
> You need a Mac with Apple silicon on macOS 15 or newer, a Gmail account, and an AI key from openrouter.ai (AXIOM walks you through
> both; you pay OpenRouter directly, and $5 of credit usually covers a few hundred applications). If anything goes wrong, choose Help, Copy Diagnostics in AXIOM
> and paste it to me. It has no passwords, keys or emails in it.

## What a friend agrees to

At setup, AXIOM asks whether to send applications for them. Choosing "Send them for me" shows this once, under the choice (the words are
`CONSENT_LEAD`, `CONSENT_ITEMS` and `CONSENT_CLOSE` in `frontend/src/onboarding.tsx`; change both together):

> By choosing this you allow AXIOM to:
>
> - create accounts at employers, in your name
> - accept their terms and privacy notices in your name
> - answer their questions from your profile
> - use your own AI key and your own email to do it
> - submit each application without asking you first
>
> It is on you to check that this is OK with you, and with each employer's rules. You can pause or stop sending at any time.

Say it to a friend before they install, too: it is their name on every account and every application.

## Deleting everything

Settings, Your account, Delete everything stops all work and removes the database, saved pages, resumes, saved accounts and keys, the
cached Chrome profile and the automatic backups from this Mac, after the friend types DELETE EVERYTHING. AXIOM then opens as if just
installed. It cannot remove applications already sent (the employers have them) or accounts AXIOM created at employers (those are
closed on the employers' sites). Copies the friend saved elsewhere by hand, and the log files in `~/Library/Logs/AXIOM`, stay.

## What was and was not checked

- `spctl --assess` on the built app says `rejected` (ad-hoc signed): so Gatekeeper will stop it, as above. Checked on macOS 15.2.
- The click path (Done, Privacy & Security, Open Anyway, password, Open) is Apple's documented one for macOS 15. Nobody has walked it on a
  second Mac here. The wording of the last confirmation may differ.
- A file that never came through a browser or AirDrop (a USB stick) carries no quarantine mark, and opens with no warning at all.

## Publishing an update

The app asks GitHub once a day for the newest release (`https://api.github.com/repos/premalshah999/AXIOM/releases/latest`), and says
nothing unless there is one: a private repository and one with nothing published both answer 404. Help, Check for Updates then says
"No update to offer", because nothing there can make this copy the newest. Publish the
disk image as a release named for the version (`v1.1.0`); the number must be larger than `version` in `pyproject.toml` at build time. A
friend then sees "AXIOM 1.1.0 is available" with a button that opens the release page, once for each version, and Help, Check for
Updates does the same on request. The app never downloads or replaces itself: the friend quits AXIOM, drags the new version over the old one, and their data
stays. An unsigned new version is a new app to Gatekeeper, so the first-open steps may come back once; the update message and the disk
image's text both say so. That has not been walked on a second Mac.

## When a copy is incomplete

`desktop/build.sh` writes `Resources/install-manifest.txt` (every file and its size). At each start the window checks it, and a copy
that stopped part way, the manifest itself included, shows "AXIOM was not copied completely" with what to do, instead of a Python error. A drag in Finder is the
supported way to install; the 2026-11-01 failure came from a script in a terminal, which macOS may treat as a program modifying an app
(App Management). That cause is not proven.
