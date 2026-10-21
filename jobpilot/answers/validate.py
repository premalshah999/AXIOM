"""Resolver methods: validate."""

import re
from ..answer_context import AUTH_KEYS
from ..answer_context import SPONSOR_KEYS
from ..answer_context import US_PERSON_CLASS
from ..answer_context import evaluate_eligibility
from ..answer_context import question_head
from ..answer_context import reference_exists
from ..answer_context import required_topics
from ..answer_context import screening_text
from ..answer_context import status_defined_answer
from ..answer_context import us_person
from ..answer_context import us_person_question
from ..knowledge import relevant_fact
from ..knowledge import topic
from ..schemas import Answer
from .consent import critical_question
from .consent import policy_acknowledgement
from .demographics import strip_fact_annotations
from .eligibility import NOT_DESIRED
from .eligibility import OTHER_COUNTRY
from .text import boolean_detail
from .text import normalize
from .text import option_value


class ValidateMixin:
    def _invalid(self, field, reason, blocker="model_output"):
        return Answer(
            field_id=field["id"],
            disposition="review" if field["required"] else "skip",
            reason=reason,
            blocker_type=blocker,
            retryable=blocker != "missing_fact",
        )

    def _validate_answer(self, field, answer, facts, evidence, learned):
        # Owner policy facts (consent, marketing, source, ...) apply to every question, but the batch
        # path validates against retrieval-scoped facts that can omit them; a model citing
        # fact:application_consent_policy was then rejected (AVEVA data-retention consent 2026-10-14).
        if getattr(self, "_policy_facts", None) is None:
            try:
                self._policy_facts = {
                    k: v
                    for k, v in self.answer_sources()[0].items()
                    if k.startswith("fact:") and k.endswith("_policy")
                }
            except Exception:
                self._policy_facts = {}  # Minimal stores (tests) have no knowledge sources.
        facts = {**self._policy_facts, **facts}
        field = {**field, "label": screening_text(field)}
        options = [o for o in field.get("options") or [] if str(o).strip()]
        if answer and answer.value and options and self.policy_choice(field, options) == answer.value:
            # Exactly the option the applicant's stored facts and owner policy select
            # (Starbucks "Springfield, MD, USA"): no further evidence scoping is needed.
            return answer.model_copy(update={"disposition": "answer", "evidence_ids": ["policy:owner"]})
        if self.context_blockers.get(field["id"]):
            reason = self.context_blockers[field["id"]]
            return self._invalid(
                field, reason, "model_output" if "retrieval budget" in reason else "missing_fact"
            )
        if answer is None:
            return self._invalid(field, "Model omitted this question")
        a = answer.model_copy(deep=True)
        if a.disposition != "answer":
            if not field["required"]:
                return a.model_copy(update={"disposition": "skip"})
            if not a.reason.strip() or a.disposition == "skip":
                return self._invalid(field, "Required question was not resolved")
            # A model's claim that a known screening fact is missing must be repaired.
            direct = any(
                relevant_fact(field["label"], key.removeprefix("fact:"))
                and not (
                    boolean_detail(field)
                    and (isinstance(value, bool) or str(value).casefold() in {"yes", "no", "true", "false"})
                )
                for key, value in facts.items()
                if key.startswith("fact:")
            )
            if direct and not OTHER_COUNTRY.search(field["label"]):
                return self._invalid(field, "A relevant confirmed fact is available; interpret its scope")
            a.blocker_type = a.blocker_type or "missing_fact"
            a.retryable = a.blocker_type != "missing_fact"
            return a
        if a.option_index is not None:
            options = field.get("options", [])
            if not (0 <= a.option_index < len(options)):
                return self._invalid(field, "unoffered_choice")
            a.value = options[a.option_index]
        if boolean_detail(field) and str(a.value).casefold() in {"yes", "no", "true", "false"}:
            return self._invalid(field, "A boolean does not answer the requested amount or explanation")
        if field.get("type") == "number" and not re.fullmatch(
            r"-?(?:\d+(?:\.\d+)?|\.\d+)", str(a.value).strip()
        ):
            return self._invalid(
                field, "Return one supported numeric value, without currency symbols, commas, ranges or prose"
            )
        if field["type"] in {"date", "date_parts", "month"} or field.get("widget") == "date-text":
            from ..widgets import date_text, parse_date

            try:
                if field["type"] == "date_parts" or field.get("widget") == "date-text":
                    date_text(field, a.value)
                else:
                    parse_date(a.value)
            except ValueError:
                return self._invalid(field, "Return one supported date with the observed required date parts")
        policy_evidence = {
            "policy:compensation_choice": "fact:compensation_choice_policy",
            "policy:source": "fact:application_source_policy",
            "policy:consent": "fact:application_consent_policy",
            "policy:application_role_type": "fact:application_role_type_policy",
        }
        a.evidence_ids = list(
            dict.fromkeys(
                policy_evidence[x] if x in policy_evidence and policy_evidence[x] in facts else x
                for x in (a.evidence_ids or a.supporting_facts)
            )
        )
        # Models sometimes return the correct field name without its namespace.
        # Repair only a unique literal reference, never a fuzzy evidence match.
        available_refs = set(facts) | set(evidence) | set(learned)
        for index, ref in enumerate(a.evidence_ids):
            if reference_exists(ref, facts) or ref in evidence or ref in learned:
                continue
            name = ref.removeprefix("fact:").removeprefix("profile:")
            matches = [key for key in available_refs if key in {"fact:" + name, "profile:" + name}]
            if len(matches) == 1:
                a.evidence_ids[index] = matches[0]
        valid_evidence = bool(a.evidence_ids) and all(
            reference_exists(x, facts) or x in evidence or x in learned for x in a.evidence_ids
        )
        packet = self.context_packets.get(field["id"])
        scoped_items = {"knowledge:" + item.fact_id: item for item in packet.items} if packet else {}
        # A knowledge reference must have been shown to the model in this batch. The
        # strict per-question packet rule stays for sensitive disclosures; for ordinary
        # questions it rejected correct answers that cited a fact retrieved for a sibling
        # question (location for "Do you live in Manatee County?", website for "GitHub
        # or portfolio", referral facts for "represented by a recruiting agency").
        strict_scope = critical_question(field["label"])
        if any(
            x.startswith("knowledge:")
            and x not in scoped_items
            and (strict_scope or not (reference_exists(x, facts) or x in evidence))
            for x in a.evidence_ids
        ):
            valid_evidence = False
        if "fact:compensation_choice_policy" in a.evidence_ids and NOT_DESIRED.search(field["label"]):
            valid_evidence = False
        subjects = required_topics(field["label"])

        def supports(subject, references=None):
            refs = a.evidence_ids if references is None else references
            if (
                subject == "prior_employment"
                and "fact:employment_history_complete" in facts
                and not re.search(
                    r"\b(?:clients?|customers?|member sites?|relatives?|family|spouse|referr\w*|government|military)\b",
                    field["label"],
                    re.I,
                )
            ):
                history = [key for key in facts if re.fullmatch(r"profile:work\.\d+", key)]
                # All attested history rows support model interpretation of the
                # employer/affiliate question, not only a same-word Boolean.
                if history and all(
                    any(ref == key or ref.startswith(key + ".") for ref in refs) for key in history
                ):
                    return True
            question = (
                field["label"]
                if len(subjects) <= 1
                else (
                    "Are you authorized to work?"
                    if subject == "authorization"
                    else "Do you require sponsorship?"
                )
            )
            return any(
                (
                    x.startswith("fact:")
                    and (
                        x[5:].casefold() in (AUTH_KEYS if subject == "authorization" else SPONSOR_KEYS)
                        if len(subjects) > 1
                        else relevant_fact(question, x[5:])
                    )
                )
                or (x in learned and learned[x].layer == "fact" and topic(learned[x].question) == subject)
                or (
                    x in scoped_items
                    and scoped_items[x].kind == "fact"
                    and (
                        scoped_items[x].match == "exact"
                        or relevant_fact(question, scoped_items[x].fact_id.removeprefix("m:fact:"))
                        or topic(scoped_items[x].question) == subject
                    )
                )
                for x in refs
            )

        def exact_support():
            # A critical question with no recognized subject is supported only by an
            # answer the applicant confirmed for this same question.
            return any(
                (x in scoped_items and scoped_items[x].kind == "fact" and scoped_items[x].match == "exact")
                or (x in learned and normalize(learned[x].question) == normalize(field["label"]))
                for x in a.evidence_ids
            )

        critical_ok = (
            not critical_question(field["label"])
            or self.signature_ok(field, a)
            or (bool(subjects) and all(supports(subject) for subject in subjects))
            or (not subjects and exact_support())
            # The batch path validates against retrieval-scoped facts; the consent policy is a
            # profile-wide owner setting (AVEVA data-retention consent 2026-10-14), so read it there.
            or policy_acknowledgement(
                field["label"],
                a,
                {**facts, "fact:application_consent_policy": True}
                if self.profile.accept_all_application_terms
                else facts,
                subjects,
            )
        )
        value = None
        if a.value is not None and str(a.value).strip():
            if field["type"] == "checkbox":
                from ..widgets import checkbox_value

                try:
                    value = checkbox_value(a.value)
                except ValueError:
                    pass
            elif field["type"] in {"checkboxgroup", "select-multiple"}:
                parts = [v for v in str(a.value).splitlines() if v.strip()]
                if len(parts) == 1 and not option_value(parts[0], field.get("options", [])):
                    parts = [v for v in re.split(r"[;,]\s*", parts[0]) if v.strip()]
                choices = [option_value(v, field.get("options", [])) for v in parts]
                # "Select all that apply": an unoffered extra is not selectable, but
                # it does not invalidate the offered choices the evidence supports.
                if any(choices):
                    value = "\n".join(dict.fromkeys(c for c in choices if c))
            else:
                value = option_value(a.value, field.get("options", []))
            from ..widgets import placeholder_option

            if (
                value is None
                and field["type"] != "checkbox"
                and field.get("options_partial")
                and not placeholder_option(a.value)
            ):
                value = str(a.value)
        if (
            (not valid_evidence or not critical_ok)
            and value is not None
            and not getattr(self, "_cross_checking", False)
        ):
            # The batch model may cite confirmed knowledge under ids the scope check
            # does not recognize. When the deterministic resolver independently
            # reaches the same value from confirmed facts, adopt its evidence rather
            # than parking a correct authorization/certification answer.
            self._cross_checking = True
            try:
                known = self.local(field)
            except Exception:
                known = None
            finally:
                self._cross_checking = False
            if (
                known is not None
                and known.disposition == "answer"
                and known.evidence_ids
                and normalize(str(known.value)) == normalize(str(value))
            ):
                valid_evidence = critical_ok = True
                a.evidence_ids = list(known.evidence_ids)
        cause = (
            "unsupported_evidence"
            if not valid_evidence
            else "disclosure_scope"
            if not critical_ok
            else "unoffered_choice"
            if value is None
            else ""
        )
        if not cause and not OTHER_COUNTRY.search(field["label"]):
            proposition = status_defined_answer(field["label"], self._facts()) or evaluate_eligibility(
                question_head(field["label"]), self._facts()
            )
            if proposition and value is not None and re.match(r"^(?:yes|no|true|false)\b", value, re.I):
                expected = proposition[0]
                if bool(re.match(r"^(?:yes|true)\b", value, re.I)) != expected:
                    cause = "question_polarity"
        if not cause and value is not None:
            catalog = field["label"] + " " + " ".join(map(str, field.get("options", [])))
            known = us_person(self._facts()) if us_person_question(catalog) else None
            if known is not None and (
                (
                    re.match(r"^(?:yes|no)\b", value, re.I)
                    and bool(re.match(r"^yes\b", value, re.I)) != known[0]
                )
                or (not known[0] and US_PERSON_CLASS.search(value))
            ):
                cause = "question_polarity"
        if not cause and value is not None and self.graduation_choice_valid(field, value) is False:
            cause = "question_time_scope"
        if value is not None and field.get("maxlength", -1) > 0 and len(value) > field["maxlength"]:
            cause = "field_limit"
        if cause:
            if self.run_id:
                self.db.event(
                    self.run_id,
                    "answer_validation",
                    "Answer validation needs repair",
                    {
                        "field": field["label"],
                        "cause": cause,
                        "topic": topic(field["label"]),
                        "option_count": len(field.get("options", [])),
                        "evidence_count": len(a.evidence_ids),
                        # What the model proposed and what the page offered, so the next
                        # run shows whether a catalog lacks the value or the value was wrong.
                        "proposed": ""
                        if field.get("type") in {"textarea", "contenteditable"}
                        else str(a.value or "")[:80],
                        "options": [str(o)[:80] for o in field.get("options", [])[:12]],
                    },
                )
            # Direct callers (including quality repair) need the same missing-fact
            # routing as batch answers. More reasoning cannot establish an unknown
            # personal disclosure. Known facts with a bad reference still get repair.
            available = set(facts) | set(learned) | set(scoped_items)
            missing = (
                cause in {"unsupported_evidence", "disclosure_scope"}
                and critical_question(field["label"])
                and (
                    any(not supports(subject, available) for subject in subjects)
                    # No reasoning can pass a subject-less critical question; ask once.
                    if subjects
                    else cause == "disclosure_scope"
                )
            ) or (
                # Employment at a client, member site or customer of this employer cannot
                # be derived from the applicant's own history (NISC "Member site" looped
                # as a model error on every run). Ask once.
                cause in {"unsupported_evidence", "disclosure_scope"}
                and bool(re.search(r"\bmember sites?\b|\bclients?\b|\bcustomers?\b", field["label"], re.I))
                and bool(re.search(r"employ|work", field["label"], re.I))
            )
            return self._invalid(field, cause, "missing_fact" if missing else "model_output")
        a.value = (
            strip_fact_annotations(value).replace("\u2014", ", ")
            if field["type"] in {"text", "textarea", "contenteditable"}
            else value
        )
        a.supporting_facts = list(a.evidence_ids)
        a.blocker_type, a.retryable = None, False
        return a
