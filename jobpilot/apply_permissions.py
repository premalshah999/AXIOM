"""The person's permission to create employer accounts and accept terms in their name: one step for every way of starting to send.

Onboarding's "Send them for me" and the Overview's Start dialog ("Send for me") ask in the same words (frontend permissions.tsx) and
write the same three profile settings here. Without them the account step returns without acting (accounts/flow_handle.py,
flow_gates.py), so every platform that needs an account fails. Never turned on silently: only an explicit "allow" from the dialog
reaches `grant`; an explicit "decline" leaves them as they are.
"""

FLAGS = ("allow_account_creation", "accept_all_application_terms", "allow_application_consents")


def missing(profile):
    """The permission settings that are off."""
    return [name for name in FLAGS if not getattr(profile, name, False)]


def grant(db):
    """Turn the three settings on, keeping every other profile key as stored."""
    from .db import Setting

    with db.exclusive() as s:
        row = s.get(Setting, "profile")
        value = dict(row.value or {}) if row else {}
        value.update({name: True for name in FLAGS})
        if row:
            row.value = value
        else:
            s.add(Setting(key="profile", value=value))
