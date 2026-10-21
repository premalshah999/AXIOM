"""Resolver methods: policy_rules."""

from datetime import datetime
from zoneinfo import ZoneInfo
import re
from ..answer_context import question_head
from ..answer_rules import major_patterns
from ..knowledge import topic
from ..schemas import Answer
from .consent import ACCOMMODATION
from .demographics import DECLINE
from .demographics import DEMOGRAPHIC
from .eligibility import HEALTHCARE_EXCLUSION
from .eligibility import NOT_DESIRED
from .eligibility import NO_OPTION
from .eligibility import OWNER_NO_ACT
from .eligibility import OWNER_NO_EXCLUDE
from .eligibility import OWNER_NO_SUBJECT
from .eligibility import US_STATES
from .eligibility import WOTC
from .eligibility import no_fact
from .history import DEGREE_FALLBACK
from .history import EDUCATION
from .history import degree_level
from .history import degree_status_options
from .history import major_part
from .sources import INTENT_SOURCE
from .sources import source_choice
from .text import first_matching
from .text import normalize
from .text import option_value


class PolicyRulesMixin:
    def owner_policy_answer(self, f):
        """Owner-policy answers that need no model: "No"-class choices worded as sentences
        (Booz Allen, SpaceX) and a GPA dropdown from the recorded GPA."""
        options = [o for o in f.get("options") or [] if str(o).strip()]
        label = str(f.get("label", ""))
        if len(options) < 2 or f.get("type") == "checkbox":
            return None
        employer = re.escape(self.employer) if len(self.employer or "") >= 3 else None
        if ACCOMMODATION.search(question_head(label)):
            # Each profile's own facts decide: an explicit accommodation need, else a confirmed "no disability".
            facts = self.profile.facts or {}
            needs = facts.get("needs_accommodation")
            evidence = ["fact:needs_accommodation"]
            if needs is None and no_fact(facts.get("disability")):
                needs, evidence = False, ["fact:disability"]
            if needs is not None and no_fact(needs):
                no = [o for o in options if NO_OPTION.search(str(o)) and not DECLINE.search(str(o))]
                if len(no) == 1:
                    return self._ans(f, no[0], evidence, "The applicant needs no accommodation")
        subject = OWNER_NO_SUBJECT.search(label) or (employer and re.search(employer, label, re.I))
        if subject and OWNER_NO_ACT.search(label) and not OWNER_NO_EXCLUDE.search(question_head(label)):
            if self.names_recorded_employer(label) or DEMOGRAPHIC.search(label):
                return None
            # Each profile's own confirmed facts decide (the owner's are No); friends' profiles
            # without these facts never get an automatic No.
            facts = self.profile.facts or {}
            if re.search(
                r"family|household|relative|spouse|in.law|close (?:personal )?relationship", label, re.I
            ):
                keys = ["has_relatives_at_employer"]
            elif re.search(
                r"government|public official|politically exposed|anti.?(?:corruption|bribery)|official",
                label,
                re.I,
            ):
                keys = ["government_employment_history", "government_official"]
            else:
                keys = ["previously_employed_here"]
            if all(no_fact(facts.get(k)) for k in keys):
                no = [o for o in options if NO_OPTION.search(str(o))]
                if len(no) == 1:
                    return Answer(
                        field_id=f["id"],
                        value=no[0],
                        disposition="answer",
                        evidence_ids=["fact:" + k for k in keys],
                        reason="Confirmed No",
                    )
        facts = self.profile.facts or {}
        if (
            facts.get("tax_credit_screening_no") is True
            and WOTC.search(label)
            and not DEMOGRAPHIC.search(label)
        ):
            if (no := [o for o in options if NO_OPTION.search(str(o))]) and len(no) == 1:
                return Answer(
                    field_id=f["id"],
                    value=no[0],
                    disposition="answer",
                    evidence_ids=["fact:tax_credit_screening_no"],
                    reason="Confirmed No (tax-credit screener)",
                )
        if facts.get("excluded_from_federal_healthcare_programs") is False and HEALTHCARE_EXCLUSION.search(
            label
        ):
            if (no := [o for o in options if NO_OPTION.search(str(o))]) and len(no) == 1:
                return Answer(
                    field_id=f["id"],
                    value=no[0],
                    disposition="answer",
                    evidence_ids=["fact:excluded_from_federal_healthcare_programs"],
                    reason="Confirmed No",
                )
        if re.search(r"deferred action|\bdaca\b", question_head(label), re.I):
            # Answered by the recorded immigration status (John Deere, 2026-10-13). Only a question about DACA itself:
            # Intel's sponsorship question lists DACA among examples, and this answered it "No" (2026-10-19).
            status = str((self.profile.facts or {}).get("current_immigration_status_US") or "")
            if status and not re.search(r"deferred action|\bdaca\b", status, re.I):
                no = [o for o in options if NO_OPTION.search(str(o))]
                if len(no) == 1:
                    return Answer(
                        field_id=f["id"],
                        value=no[0],
                        disposition="answer",
                        evidence_ids=["fact:current_immigration_status_US"],
                        reason="Recorded status is not DACA",
                    )
        if re.search(r"\bgpa\b|grade point", label, re.I) and self.profile.education:
            graduate = re.search(r"graduate|master|post", label, re.I) and not re.search(
                r"undergrad", label, re.I
            )
            entries = [
                e
                for e in self.profile.education
                if degree_level(e.degree) == ("master" if graduate else "bachelor")
            ]
            if not entries:
                return None
            m = re.match(r"\s*(\d(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?", entries[0].gpa or "")
            if not m:
                return None
            scale = float(m[2] or 4)
            if scale == 4:
                value = str(int(float(m[1]) * 10) / 10)  # Truncate: never overstate.
                hit = next((o for o in options if re.match(rf"\s*{re.escape(value)}(?:0)?\b", str(o))), None)
            else:
                hit = next(
                    (o for o in options if re.search(r"not applicable|do not recall|n/?a\b", str(o), re.I)),
                    None,
                )
            if hit:
                return Answer(
                    field_id=f["id"],
                    value=hit,
                    disposition="answer",
                    evidence_ids=[f"profile:education.{self.profile.education.index(entries[0])}.gpa"],
                )
        return None

    def policy_choice(self, f, options):
        """The offered option for degree, major or source questions under the owner's fallback
        policy: the recorded value when offered, else its owner-approved equivalent."""
        options = [o for o in options or [] if str(o).strip()]
        if not options or any(re.fullmatch(r"yes|no", str(o).strip(), re.I) for o in options):
            return None  # A Yes/No proposition about a degree is not a catalog choice.
        label = str(f.get("label", ""))
        sharing = " ".join([label, *map(str, options)])
        if re.search(
            r"profile data|my data|personal data|data across|share my (?:profile|information)|"
            r"other (?:roles|positions|opportunities)",
            sharing,
            re.I,
        ):
            # No optional marketing or data sharing unless the profile joins talent communities. Qualcomm (Eightfold) 2026-10-14:
            # "ANY ROLES GLOBALLY ... Talent Community" vs "ONLY THE ROLE I AM APPLYING FOR".
            narrow = [
                o
                for o in options
                if re.search(r"\bonly\b.{0,40}(?:role|position|job|requisition)", str(o), re.I)
            ]
            if len(narrow) == 1 and not self.answer_rules.join_talent_communities:
                return narrow[0]
        p = self.profile
        from .. import phone_fields

        desired = p.salary_expectation or p.facts.get("desired_salary")
        if (
            desired
            and re.search(r"salary|compensation|\bpay\b", label, re.I)
            and not NOT_DESIRED.search(label)
        ):
            # CVS Health 2026-10-14: the model chose "$140,000 - $160,000" while citing a recorded
            # $80,000. The recorded amount picks the offered range holding it (low <= x < high).
            from ..widgets import money_range

            try:
                amount = float(re.sub(r"[^\d.]", "", str(desired)))
            except ValueError:
                amount = 0
            holding = [o for o in options if (r := money_range(str(o))) and r[0] <= amount < r[1]]
            holding = holding or [o for o in options if (r := money_range(str(o))) and r[0] <= amount <= r[1]]
            if amount and holding:
                return holding[0]
        if phone_fields.is_phone_type({**f, "options": options}):
            return phone_fields.phone_type_option(options, getattr(p, "phone_type", "") or "")
        if topic(label) == "source" or INTENT_SOURCE.search(label):
            if p.application_source and (hit := option_value(p.application_source, options)):
                return hit
            return source_choice(options, self.answer_rules)
        if (
            re.search(r"\bcity\b|^location$|^location \(city\)|current location", label, re.I)
            and p.address.city
        ):
            # Location pickers list "City, State, Country": the recorded city in the recorded state.
            city = normalize(p.address.city)
            state = p.address.state or ""
            states = {normalize(state), normalize(US_STATES.get(state.upper(), ""))} - {""}
            same = [o for o in options if normalize(o) == city or normalize(o).startswith(city + " ")]
            in_state = [
                o for o in same if any(re.search(rf"\b{re.escape(s)}\b", normalize(o)) for s in states)
            ]
            if in_state or len(same) == 1:
                return (in_state or same)[0]
            return None
        if not p.education:
            return None
        group = f"{f.get('group') or ''} {f.get('section') or ''}"
        row = re.search(r"education\s*(\d+)", group, re.I)
        entry = p.education[int(row[1]) - 1] if row and 0 < int(row[1]) <= len(p.education) else None
        if EDUCATION["field_of_study"].search(label):
            entry = entry or p.education[0]
            if entry.field_of_study and (hit := option_value(entry.field_of_study, options)):
                return hit
            if entry.field_of_study and (hit := major_part(entry.field_of_study, options)):
                return hit
            return first_matching(options, major_patterns(self.answer_rules.major_fallback))
        if EDUCATION["degree"].search(label):
            if entry is None:
                ranks = ["doctor", "master", "bachelor"]
                entry = sorted(
                    p.education,
                    key=lambda e: next(
                        (i for i, r in enumerate(ranks) if r in normalize(e.degree)), len(ranks)
                    ),
                )[0]
            if entry.end:
                options = degree_status_options(
                    options, entry.end, datetime.now(ZoneInfo(self.config.timezone)).date()
                )
            if hit := option_value(entry.degree, options):
                return hit
            level = degree_level(entry.degree)
            return first_matching(options, DEGREE_FALLBACK[level]) if level else None
        return None

    def _policy_ans(self, f, evidence):
        value = self.policy_choice(f, f.get("options") or [])
        return self._ans(f, value, evidence, "Owner policy: offered equivalent") if value else None
