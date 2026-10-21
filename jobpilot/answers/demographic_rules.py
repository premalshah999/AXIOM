"""Resolver methods: demographic_rules."""

import re
from ..answer_context import semantic_text
from ..knowledge import applicable
from .demographics import DECLINE
from .demographics import DEMOGRAPHIC
from .demographics import declines
from .demographics import demographic_topic
from .text import normalize
from .text import yes_no


class DemographicRulesMixin:
    def demographic_rule(self, f):
        return self.answer_rules.demographics[demographic_topic(f.get("label", "")) or "other"]

    def demographic_decline(self, f):
        """The applicant's decline preference wins whenever the form offers a decline.

        A confirmed answer (for example a gender saved once) is used only when the
        form offers no way to decline; it must not override the stated preference.
        """
        from ..widgets import placeholder_option

        if f.get("type") == "checkbox" or not DEMOGRAPHIC.search(f.get("label", "")):
            return None
        if not self.demographic_rule(f).decline_first:
            return None
        decline = [o for o in f.get("options", []) if declines(o) and not placeholder_option(o)]
        if not decline:
            return None
        return self._ans(
            f, decline[0], ["policy:demographics"], "Applicant declines voluntary demographic disclosure"
        )

    def demographic_chain(self, f, options):
        """Owner rule for one self-identification question: decline when offered (if preferred),
        then the owner's own answer, then answers saved in Knowledge, then the rule's fallback
        (closest offered option, Other, or ask). Nothing is inferred from other facts."""
        from ..answer_rules import closest_option, other_option
        from ..widgets import placeholder_option

        # A generic label ("Please Select:") takes its topic from the options it offers.
        topic = (
            demographic_topic(f.get("label", "")) or demographic_topic(" ".join(map(str, options))) or "other"
        )
        rule = self.answer_rules.demographics[topic]
        decline = next((o for o in options if declines(o) and not placeholder_option(o)), None)
        if rule.decline_first and decline:
            return self._ans(
                f, decline, ["policy:demographics"], "Applicant declines voluntary demographic disclosure"
            )
        evidence = [f"rule:demographics.{topic}"]
        if rule.answer and (
            a := self._ans(f, rule.answer, evidence, "Applicant's own answer for this topic")
        ):
            return a
        if not rule.answer and (a := self.confirmed_demographic(f, options)):
            return a
        if rule.when_missing == "closest":
            wanted = (
                [rule.answer]
                if rule.answer
                else [v for v, _ in self.saved_demographics(f) if isinstance(v, str)]
            )
            picks = {closest_option(v, options) for v in wanted if v and not DECLINE.search(v)} - {None}
            if len(picks) == 1:
                return self._ans(f, picks.pop(), evidence, "Closest offered option to the applicant's answer")
        if rule.when_missing == "other" and (other := other_option(options)):
            return self._ans(f, other, evidence, "The applicant's answer is not offered; chose Other")
        return None

    def saved_demographics(self, f):
        """(value, evidence id) answers the applicant saved for this question's topic."""
        name = demographic_topic(f.get("label", ""))
        if not name:
            return []
        saved = []
        packet = self.context_packets.get(f["id"])
        for item in getattr(packet, "items", None) or []:
            if (
                item.kind == "fact"
                and item.category == "demographics"
                and demographic_topic(item.question) == name
            ):
                saved.append((item.value, "knowledge:" + item.fact_id))
        keys = {
            "hispanic": ("hispanic_or_latino", "is_hispanic", "hispanic"),
            "veteran": ("veteran", "is_veteran", "protected_veteran"),
            "disability": ("disability", "has_disability"),
            "gender": ("gender",),
            "race": ("race", "ethnicity"),
            "sexual_orientation": ("sexual_orientation",),
            "gender_identity": ("gender_identity",),
            "transgender": ("transgender",),
            "pronouns": ("pronouns",),
        }.get(name, ())
        facts = self._facts()
        saved += [(facts[k], "fact:" + k) for k in keys if k in facts]
        for review in self.profile.reviewed_answers:
            if applicable(review, self.employer) and demographic_topic(review.question) == name:
                saved += [(part, "reviewed:" + review.id) for part in str(review.answer).splitlines()]
        return saved

    def confirmed_demographic(self, f, options):
        """A form without a decline choice may reuse the applicant's own confirmed
        answer for the same topic. Exactly one offered option must match; nothing
        is inferred, and decline-type values never map onto an identity."""
        name = demographic_topic(f["label"])
        if name and not options and f.get("type") in {"text", "textarea", "combobox"}:
            return self.free_text_demographic(f, name)
        if not name or not options:
            return None
        saved = []  # (value, evidence id)
        packet = self.context_packets.get(f["id"])
        for item in getattr(packet, "items", None) or []:
            if (
                item.kind == "fact"
                and item.category == "demographics"
                and demographic_topic(item.question) == name
            ):
                saved.append((item.value, "knowledge:" + item.fact_id))
        preference = (self._facts() or {}).get("race_preference") if name == "race" else None
        if isinstance(preference, list):
            # Owner decision (2026-10-13): when no decline is offered, Asian, else South Asian.
            for want in preference:
                hit = next(
                    (
                        o
                        for o in options
                        if re.match(rf"\s*{re.escape(str(want))}\b", str(o), re.I)
                        and not re.search(r"pacific|hawaiian", str(o), re.I)
                    ),
                    None,
                )
                if hit:
                    return self._ans(f, hit, ["fact:race_preference"], "Applicant-confirmed race preference")
        keys = {
            "hispanic": ("hispanic_or_latino", "is_hispanic", "hispanic"),
            "veteran": ("veteran", "is_veteran", "protected_veteran"),
            "disability": ("disability", "has_disability"),
            # Used only when no decline choice is offered (the caller checks).
            "gender": ("gender",),
            "sexual_orientation": ("sexual_orientation",),
            "gender_identity": ("gender_identity",),
            "transgender": ("transgender",),
            "pronouns": ("pronouns",),
        }.get(name, ())
        facts = self._facts()
        saved += [(facts[k], "fact:" + k) for k in keys if k in facts]
        # The applicant's own reviewed answers to the same identity question.
        same_employer = set()
        for review in self.profile.reviewed_answers:
            if applicable(review, self.employer) and demographic_topic(review.question) == name:
                for part in str(review.answer).splitlines():
                    saved.append((part, "reviewed:" + review.id))
                if review.employer and review.employer.casefold() == self.employer.casefold():
                    same_employer.add("reviewed:" + review.id)
        picked = self._pick_demographic(name, options, saved)
        if picked is None and same_employer:
            # Answers given for different employers disagree: the applicant's
            # own answer for this employer decides.
            picked = self._pick_demographic(name, options, [x for x in saved if x[1] in same_employer])
        if picked is None:
            return None  # Absent or conflicting confirmed answers stay a question.
        value, refs = picked
        return self._ans(f, value, refs, "Applicant-confirmed answer for this self-identification topic")

    def free_text_demographic(self, f, name):
        """No offered choices (a free-text box, or a list whose options were not readable) offer no decline: the
        owner's own fact for the topic answers it (owner 2026-10-18: use the facts when no decline is offered).
        Sentry (Ashby) 2026-10-19 asked the owner for "Pronouns" although the profile says He/him."""
        facts = self._facts() or {}
        preference = facts.get("race_preference")
        if name == "race" and isinstance(preference, list) and preference:
            return self._ans(
                f, str(preference[0]), ["fact:race_preference"], "Applicant-confirmed race preference"
            )
        keys = {
            "gender": ("gender",),
            "pronouns": ("pronouns",),
            "gender_identity": ("gender_identity",),
            "sexual_orientation": ("sexual_orientation",),
            "transgender": ("transgender",),
            "hispanic": ("hispanic_or_latino", "is_hispanic", "hispanic"),
            "veteran": ("veteran", "is_veteran", "protected_veteran"),
            "disability": ("disability", "has_disability"),
        }.get(name, ())
        key = next((k for k in keys if facts.get(k) not in (None, "")), None)
        if key is None:
            return None
        value = facts[key]
        if isinstance(value, bool) or str(value).strip().lower() in {"true", "false"}:
            value = yes_no(value if isinstance(value, bool) else str(value).strip().lower() == "true")
        if not isinstance(value, str) or not value.strip() or DECLINE.search(value):
            return None
        return self._ans(
            f, value.strip(), ["fact:" + key], "Applicant-confirmed answer for this self-identification topic"
        )

    def _pick_demographic(self, name, options, saved):
        chosen, refs = set(), []
        for value, ref in saved:
            if isinstance(value, str) and value.strip().lower() in {"yes", "no", "true", "false"}:
                value = value.strip().lower() in {"yes", "true"}
            if isinstance(value, bool):
                matches = [o for o in options if normalize(o) == ("yes" if value else "no")]
                if not matches and name == "veteran" and value is False:
                    matches = [o for o in options if re.search(r"^(?:i am )?not a veteran\b", normalize(o))]
                if not matches:
                    # Self-ID wording: "No, I do not have a disability ...", "Not Hispanic or Latino",
                    # "I am not a protected veteran"; decline choices never count as an answer.
                    lead = (
                        r"^(?:no|not|none|i am not|i'?m not|i do not|i don'?t|i have not)\b"
                        if value is False
                        else r"^(?:yes|i am|i have|i identify as)\b"
                    )
                    matches = [
                        o for o in options if re.search(lead, normalize(o)) and not DECLINE.search(str(o))
                    ]
            elif isinstance(value, str) and value.strip() and not DECLINE.search(value):
                matches = [o for o in options if semantic_text(o) == semantic_text(value)]
            else:
                continue
            if len(matches) != 1:
                continue
            chosen.add(matches[0])
            refs.append(ref)
        if len(chosen) != 1:
            return None
        return chosen.pop(), refs
