# The license key

Off by default. A build turns it on; nothing changes for anyone on a build that does not. It needs no server and makes no network call.

## How it works

1. Opened for the first time, a Mac whose build asks for a key shows one screen: **this Mac's code**, like `ABCD-EFGH-JKLM`. It is 60 bits of a SHA-256 of the Mac's hardware UUID (`ioreg`, local), so the UUID itself is never shared and cannot be read back from the code.
2. The person sends the code to the owner. The owner runs one command on their own Mac:

   ```sh
   scripts/axiom-license sign ABCD-EFGH-JKLM --days 30 --name "Sam"
   scripts/axiom-license sign ABCD-EFGH-JKLM --until 2027-01-31
   ```

   It prints a key (`AXIOM1-...`, a few hundred characters). The first run creates the owner's key pair in `~/.config/axiom-license/` (`AXIOM_LICENSE_HOME` to use another folder): the folder is `0700`, `private.key` is `0600`, it is never inside the repository, and the command warns to back it up. A private key that other users can read is refused.
3. The person pastes the key. The app checks, in this order: the Ed25519 signature (against the public key bundled in the build), that the key's Mac code is this Mac's, and that its date has not passed. A refusal says which, in plain words, and keeps nothing.

Other commands: `mac` (this Mac's own code, to try it yourself), `public` (the public key a build embeds), `show KEY`, `init`.

## Turning it on

```sh
scripts/axiom-license init                 # once: makes the key pair, prints the public key
AXIOM_LICENSE=on desktop/build.sh          # embeds the public key (or AXIOM_LICENSE_PUBLIC_KEY=... when the private key is elsewhere)
```

`AXIOM_LICENSE=on` writes `jobpilot/license_build.json` (`{"required": true, "public_key": ...}`) into the built app. **The file's presence is the build setting.** A build without it, and every checkout, behaves exactly as before; the file is in `.gitignore`. The environment cannot switch off a build that carries the file. For development only, `AXIOM_LICENSE_BUILD=/path/to/file.json` supplies one when the build has none.

## What a key does and does not stop

- An expired key, or none, stops **new** applications from starting: queue admission (`Service._admit`) refuses with a deferred hold (what asked keeps its place and starts once a key is entered, as with Stop), and the claim of an already queued fresh run waits, holding no browser, and says why in Queue.
- It never touches an application that is already running, its commit, or anything that continues an application already started (a retry of it, an answered question, a matched email, the check of an earlier submission). The demo is never held.
- Everything else keeps working and nothing is deleted: the board, profile, resumes, history, discovery.
- A copy of the data folder on another Mac has a key for the wrong Mac: new applications are held there, the app opens.
- The latest moment the Mac has seen is kept in `DATA_DIR/license/seen.json` (written at most every ten minutes), and the key's days are counted from the later of that and the clock. Setting the clock back does not bring an expired key back; the screen says the clock is behind. A key is valid through its last day, by the Mac's local date.
- The state is cached for 20 seconds, so checking it every second at the claim costs nothing.

## What it cannot do

- **It cannot revoke a key.** Nothing is checked online, so a key works until its date. Give short dates and issue new keys.
- **It cannot stop someone who edits the Python**, deletes `license_build.json` from their own copy, or clears `DATA_DIR/license/`. The repository is public (AGPL) and the check runs on the person's own Mac. It keeps honest copies honest; it is not copy protection.
- A key copied to another Mac is refused, but a key for a Mac whose hardware UUID is changed by software on the Mac itself is not distinguishable.
- A clock moved far *forward* by mistake is also remembered; correcting the clock does not undo that. Delete `DATA_DIR/license/seen.json` to forget it.
