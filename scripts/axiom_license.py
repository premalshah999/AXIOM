#!/usr/bin/env python3
"""The owner's license command. Run it on your own Mac; the app never contains this key.

    scripts/axiom-license sign ABCD-EFGH-JKLM --days 30 [--name "Sam"]     # prints the key to send back
    scripts/axiom-license sign ABCD-EFGH-JKLM --until 2027-01-31
    scripts/axiom-license mac                                              # this Mac's own code (to try it yourself)
    scripts/axiom-license public                                           # the public key a build embeds
    scripts/axiom-license show AXIOM1-...                                  # what a key says (checks the signature)
    scripts/axiom-license init                                             # create the key pair now (sign does it if missing)

The private key is made once, in ~/.config/axiom-license/ (AXIOM_LICENSE_HOME to use another folder), readable by you only. It
is never inside the repository. Back it up somewhere private: without it you cannot sign keys that builds already out there
accept, and anyone who gets it can sign keys for everyone. (docs/LICENSING.md)
"""

from __future__ import annotations

import argparse
import base64
import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from jobpilot import licensing  # noqa: E402


def home() -> Path:
    return Path(os.environ.get("AXIOM_LICENSE_HOME") or Path.home() / ".config" / "axiom-license")


def _raw_public(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def load_private(create: bool) -> tuple[Ed25519PrivateKey, bool]:
    """(the private key, whether it was just created)."""
    folder, path = home(), home() / "private.key"
    if path.exists():
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SystemExit(f"{path} can be read by other users. Run: chmod 600 {path}")
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text().strip())), False
    if not create:
        raise SystemExit(f"There is no private key at {path}. Run: scripts/axiom-license init")
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(base64.b64encode(seed).decode() + "\n")
    (folder / "public.key").write_text(_raw_public(private) + "\n")
    print(
        f"Created your license key in {folder}\n"
        "  BACK UP private.key NOW, somewhere private (not this repository, not a shared folder).\n"
        "  If you lose it you cannot sign keys that builds already sent out accept. If someone gets it, they can sign keys.\n",
        file=sys.stderr,
    )
    return private, True


def expiry(args) -> date:
    today = date.today()
    if args.until:
        try:
            end = date.fromisoformat(args.until)
        except ValueError:
            raise SystemExit("--until needs a date like 2027-01-31") from None
    else:
        end = today + timedelta(days=args.days)
    if end < today:
        raise SystemExit("That date has already passed.")
    return end


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="axiom-license", description="Sign AXIOM license keys.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the key pair if it does not exist")
    sub.add_parser("mac", help="print this Mac's code")
    sub.add_parser("public", help="print the public key that builds embed")
    sign = sub.add_parser("sign", help="sign a key for one Mac code")
    sign.add_argument("mac", help="the code the person's AXIOM shows, like ABCD-EFGH-JKLM")
    when = sign.add_mutually_exclusive_group(required=True)
    when.add_argument("--days", type=int, help="valid for this many days from today")
    when.add_argument("--until", help="valid through this date (YYYY-MM-DD), your Mac's local date")
    sign.add_argument("--name", default="", help="who it is for, shown in their AXIOM (optional)")
    show = sub.add_parser("show", help="read a key")
    show.add_argument("key")
    args = parser.parse_args(argv)

    if args.command == "mac":
        print(licensing.machine_code())
        return 0
    if args.command == "init":
        private, _ = load_private(create=True)
        print(f"Public key: {_raw_public(private)}")
        return 0
    private, _ = load_private(create=args.command == "sign")
    if args.command == "public":
        print(_raw_public(private))
        return 0
    if args.command == "show":
        try:
            key = licensing.read_key(args.key, private.public_key())
        except licensing.LicenseError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Mac {key.mac}   issued {key.issued}   valid through {key.expires}   name {key.name or '-'}")
        return 0
    try:
        mac = licensing.normalize_mac_code(args.mac)
    except licensing.LicenseError as exc:
        print(exc, file=sys.stderr)
        return 1
    end = expiry(args)
    key = licensing.make_key(private, mac, end, date.today(), args.name)
    print(f"Key for {mac}, valid through {end}:\n", file=sys.stderr)
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
