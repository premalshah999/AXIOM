"""Resolver methods: consent_rules."""

from datetime import datetime
from zoneinfo import ZoneInfo
import re
from ..answer_context import US_PERSON_CLASS
from ..answer_context import status_defined_answer
from ..answer_context import us_person
from ..answer_context import us_person_question
from ..schemas import Answer
from .consent import AGREEMENT_TEXT
from .consent import CHECK_WILLINGNESS
from .consent import CONTACT_CONSENT
from .consent import EMBEDDED_DISCLOSURE
from .consent import READ_ACKNOWLEDGEMENT
from .consent import RECORD_STATEMENT
from .consent import SELF_ASSERTION
from .consent import SIGNATURE_REQUEST
from .consent import SIGNED_CERTIFICATION
from .text import normalize


class ConsentRulesMixin:
    def signature_block(self, f):
        """An agreement text box that ends asking for "Candidate Name: Date:" is an e-signature
        (Astreya NDA 2026-10-14). The accept-all-terms policy signs with the legal name and today."""
        label = f.get("label", "")
        if f.get("type") not in {"text", "textarea"} or not self.profile.accept_all_application_terms:
            return None
        tail = label[-300:]
        if not SIGNATURE_REQUEST.search(tail) or not AGREEMENT_TEXT.search(label):
            return None
        # Names may live in verified facts or the full name rather than the explicit fields.
        name = " ".join(x for x in self.profile.given_names() if x).strip() or self.profile.name.strip()
        if not name:
            return None
        lines = [("Candidate Name: " if re.search(r"candidate name", tail, re.I) else "Name: ") + name]
        if re.search(r"\bdate\b", tail, re.I):
            lines.append("Date: " + datetime.now(ZoneInfo(self.config.timezone)).strftime("%m/%d/%Y"))
        return Answer(
            field_id=f["id"],
            value="\n".join(lines),
            disposition="answer",
            evidence_ids=["fact:application_consent_policy"],
            reason="E-signature under the terms policy",
        )

    def required_contact_consent(self, f):
        """CVS Health 2026-10-14: a *required* "I authorize CVS Health to communicate with me via
        text" blocks the application. Under the owner's accept-all-application-terms policy a
        required consent is accepted; optional marketing stays declined elsewhere."""
        label = str(f.get("label", ""))
        if not (
            f.get("required") and self.profile.accept_all_application_terms and CONTACT_CONSENT.search(label)
        ):
            return None
        options = [str(o) for o in f.get("options") or []]
        if f.get("type") == "checkbox" and not options:
            value = "true"
        else:
            yes = [
                o
                for o in options
                # SeekWell (Workday) 2026-10-18 offers only "I understand and agree".
                if re.match(
                    r"\s*(?:yes|i (?:understand and )?(?:agree|consent|authori[sz]e|accept))\b", o, re.I
                )
                and not re.search(r"\b(?:not|don.?t|decline|disagree)\b", o, re.I)
            ]
            if len(yes) != 1:
                return None
            value = yes[0]
        return Answer(
            field_id=f["id"],
            value=value,
            disposition="answer",
            evidence_ids=["fact:application_consent_policy"],
            reason="Required contact consent under the terms policy",
        )

    def talent_pool_opt_out(self, f):
        """AVEVA 2026-10-15: "Yes, I agree that AVEVA may keep my data on file to be considered for other
        roles ... Indicate 'YES' to Opt In OR 'NO' to Opt Out" was scoped as work authorization and every
        answer was rejected. It is optional data retention/marketing; opted out unless the profile's
        answer_rules.join_talent_communities says otherwise."""
        label = str(f.get("label", ""))
        if not re.search(
            r"\bopt[ -]?in\b.{0,60}\bopt[ -]?out\b|keep my (?:data|information|details|profile|cv|resume) on file|"
            r"considered for (?:other|future) (?:roles|positions|opportunities|vacancies)",
            label,
            re.I,
        ):
            return None
        join = self.answer_rules.join_talent_communities
        options = [str(o) for o in f.get("options") or []]
        if options:
            wanted = (
                r"\s*(?:yes\b|i agree|i do|opt[ -]?in)"
                if join
                else r"\s*(?:no\b|i do not|i don.t|opt[ -]?out)"
            )
            picked = [o for o in options if re.match(wanted, o, re.I)]
            if len(picked) != 1:
                return None
            value = picked[0]
        elif f.get("type") in {"text", "textarea"}:
            if join:
                value = "YES" if re.search(r"'?\bYES\b'?\s+to opt", label) else "Yes"
            else:
                value = "NO" if re.search(r"'?\bNO\b'?\s+to opt", label) else "No"
        elif f.get("type") == "checkbox":
            value = "true" if join else "false"
        else:
            return None
        return Answer(
            field_id=f["id"],
            value=value,
            disposition="answer",
            evidence_ids=["policy:marketing"],
            reason="Optional talent-pool data retention "
            + ("accepted" if join else "declined")
            + " under the profile's rule",
        )

    def signature_ok(self, field, answer):
        """A typed signature under a certification: the applicant's own first and last name, optionally with a date.

        The signature box of "I certify that the facts set forth in this Application for Employment are true and
        complete ..." was refused as an unsupported disclosure 19 times (2026-10-17/19).
        """
        label = str(field.get("label", ""))
        if field.get("options") or not self.profile.accept_all_application_terms:
            return False
        read_only = bool(READ_ACKNOWLEDGEMENT.search(label)) and not SELF_ASSERTION.search(label)
        if not SIGNED_CERTIFICATION.search(label) or (EMBEDDED_DISCLOSURE.search(label) and not read_only):
            return False
        p = self.profile
        parts = [normalize(p.first_name or ""), normalize(p.last_name or "")]
        if not all(parts):
            parts = normalize(p.name or "").split()[:1] + normalize(p.name or "").split()[-1:]
        own = set(
            " ".join(
                [
                    normalize(p.name or ""),
                    normalize(p.first_name or ""),
                    normalize(p.middle_name or ""),
                    normalize(p.last_name or ""),
                ]
            ).split()
        )
        words = normalize(str(answer.value or "")).split()
        return (
            len(parts) == 2
            and all(part and set(part.split()) <= set(words) for part in parts)
            and all(w in own or re.fullmatch(r"\d{1,4}", w) for w in words)
        )

    def check_consent_answer(self, f):
        """Willingness to undergo a background check or drug screen follows the accept-all-terms policy."""
        options = [str(o) for o in f.get("options") or []]
        label = str(f.get("label", ""))
        if not self.profile.accept_all_application_terms or not CHECK_WILLINGNESS.search(label):
            return None
        if RECORD_STATEMENT.search(label) or EMBEDDED_DISCLOSURE.search(label):
            return None
        if f.get("type") == "checkbox":
            return Answer(
                field_id=f["id"], value="true", disposition="answer", evidence_ids=["policy:consent"]
            )
        yes = [o for o in options if re.match(r"\s*(?:yes|i am willing|i agree|agree)\b", o, re.I)]
        if len(yes) != 1:
            return None
        return Answer(
            field_id=f["id"],
            value=yes[0],
            disposition="answer",
            evidence_ids=["policy:consent"],
            reason="Consent to a hiring check under the accept-all-terms policy",
        )

    def us_person_answer(self, f):
        """A U.S.-person status question, answered from the citizenship and immigration facts.

        Micron 2026-10-18: the model answered "Yes" to "Are you in one of the above five groups?" (U.S. citizen,
        national, permanent resident, refugee, asylee) for an applicant on F-1 OPT.
        """
        options = [str(o) for o in f.get("options") or []]
        if f.get("type") == "checkbox" or not us_person_question(f["label"] + " " + " ".join(options)):
            return None
        known = us_person(self._facts())
        if known is None:
            return None
        truth, evidence = known
        if options and all(re.match(r"\s*(?:yes|no)\b", o, re.I) for o in options):
            pick = [o for o in options if re.match(r"\s*yes\b" if truth else r"\s*no\b", o, re.I)]
        elif options and not truth:
            # Category options: the one naming no U.S.-person class ("Other", "None of the above").
            pick = [
                o
                for o in options
                if not US_PERSON_CLASS.search(o)
                and re.search(r"\bother\b|\bnone\b|\bnot\b|^\s*no\b", o, re.I)
            ]
        else:
            return None  # Which class a U.S. person belongs to needs that specific fact.
        if len(pick) != 1:
            return None
        return Answer(
            field_id=f["id"],
            value=pick[0],
            disposition="answer",
            evidence_ids=evidence,
            reason="U.S.-person status from the citizenship and immigration facts",
        )

    def status_answer(self, f):
        """The question defines its answer for the applicant's recorded immigration status.

        The stored sponsorship flag answers the plain visa question; a question that tells F-1 OPT holders to
        answer Yes (or No) is answered as it says (see answer_context.status_defined_answer).
        """
        defined = status_defined_answer(f["label"], self._facts())
        if not defined or f.get("type") == "checkbox":
            return None
        truth, refs = defined
        word = "yes" if truth else "no"
        options = f.get("options", [])
        hits = [o for o in options if re.match(rf"\s*{word}\b", str(o), re.I)]
        if options and len(hits) != 1:
            return None
        return Answer(
            field_id=f["id"],
            value=hits[0] if hits else word.capitalize(),
            disposition="answer",
            evidence_ids=refs,
            reason="The question defines its answer for the recorded status",
        )
