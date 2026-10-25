"""Resolver methods: intent_rules."""

from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import re
from ..answer_context import status_defined_answer
from ..schemas import Answer
from .consent import CHECK_WILLINGNESS
from .consent import NEGATED
from .consent import RECORD_STATEMENT
from .consent import negation_text
from .eligibility import COUNTRY_SCOPED
from .eligibility import NOT_AVAILABILITY
from .eligibility import NOT_DESIRED
from .eligibility import OTHER_COUNTRY
from .eligibility import no_fact
from .intents import INTENTS
from .text import boolean_detail
from .text import yes_no


class IntentRulesMixin:
    def _intent_answer(self, f, label):
        if f.get("type") != "checkbox" and status_defined_answer(f["label"], self._facts()):
            # The question's own definition decides, never the stored sponsorship flag.
            return self.status_answer(f)
        if re.search(r"currently (?:based|located|living|residing)", label):
            return None  # Current residence and willingness to relocate are different facts.
        facts = self._facts()
        if (
            "requires_relocation_assistance" not in facts
            and yes_no(facts.get("relocation_at_own_expense")) == "Yes"
        ):
            facts = {
                **facts,
                "requires_relocation_assistance": "No",
            }  # Relocating at own expense needs no package.
        p = self.profile
        extra = {
            "application_source": p.application_source,
            "desired_salary": p.salary_expectation,
            "availability": p.availability,
        }
        for pattern, keys in INTENTS:
            if not pattern.search(f["label"]):
                continue
            if f.get("type") == "textarea" and "desired_salary" not in keys:
                # Prose is written from evidence, never a stored yes/no. A compensation box rendered
                # as a textarea still takes the recorded amount (TransUnion Workday, 2026-10-15).
                return None
            if set(keys) & COUNTRY_SCOPED and OTHER_COUNTRY.search(f["label"]):
                return None  # The saved facts are about the US; another country needs interpretation.
            if "desired_salary" in keys and NOT_DESIRED.search(f["label"]):
                return None  # "Current/previous salary" is a different fact.
            if "government_employment_history" in keys and re.search(
                r"family|relative|spouse|dependent", f["label"], re.I
            ):
                # "You or a relative": answerable only when both confirmed facts are No.
                if not (
                    no_fact(facts.get("government_employment_history"))
                    and no_fact(facts.get("has_relatives_at_employer"))
                ):
                    return None  # The applicant's employment history does not answer a relative's history.
            if "has_non_compete" in keys and re.search(
                r"solicit|contact\w*.{0,30}(?:customers|clients)", f["label"], re.I
            ):
                # Both covenants are asked: a recorded non-solicitation agreement is not answered by "no non-compete".
                solicitation = facts.get("has_non_solicitation")
                if solicitation not in (None, "") and not no_fact(solicitation):
                    return None
            if (
                "criminal_conviction" in keys
                and CHECK_WILLINGNESS.search(f["label"])
                and not RECORD_STATEMENT.search(f["label"])
            ):
                return None  # Willingness to undergo a check is consent, not the applicant's record.
            if "previously_employed_here" in keys and self.names_recorded_employer(f["label"]):
                return None  # A recorded employer is named: the owner's "No" policy cannot apply.
            if "previously_employed_here" in keys and re.search(
                r"family member|relative|spouse", f["label"], re.I
            ):
                continue  # A family employment question must use the family fact.
            if "availability" in keys and (
                NOT_AVAILABILITY.search(f["label"])
                # A history row's "Start Date Month" is not the applicant's availability: Oracle education rows got
                # "Immediately" in their month menus and the section was refused (Verisk, Dallas County 2026-10-23).
                or re.fullmatch(r"\s*(?:start|end|from|to) date (?:month|year|day)\s*", f["label"], re.I)
                or re.search(r"experience|education|employment|work history", f.get("group") or "", re.I)
                or f.get("history_row")
            ):
                return None
            key = next((k for k in keys if k in facts and facts[k] not in ("", None)), None)
            value = facts.get(key) if key else None
            if isinstance(value, str) and re.fullmatch(r"(?:Yes|No) \(confirmed by applicant\)", value, re.I):
                value = value.split()[0]
            elif isinstance(value, str) and re.match(r"\s*(?:yes|no)\b[,.:;]", value, re.I):
                # "Yes, authorized to work for any employer in the USA (applicant confirmed)" is a Yes
                # (Target 2026-10-14 asked the owner because this sentence matched no option).
                value = re.match(r"\s*(yes|no)", value, re.I)[1].capitalize()
            if value is None:
                key = next((k for k in keys if extra.get(k)), None)
                value = extra.get(key) if key else None
            if value is None and "requires_relocation_assistance" in keys:
                return None  # Never fall through to the broader willingness-to-relocate fact.
            if value is None:
                if not f["required"]:
                    # Optional screening question with no saved fact: leave it blank, no model call.
                    return Answer(
                        field_id=f["id"],
                        disposition="skip",
                        reason="No saved fact for this optional question",
                    )
                continue  # A broader matching intent must not hide a later confirmed fact.
            if (
                "desired_salary" in keys
                and f.get("type") in {"text", "textarea"}
                and not f.get("options")
                and re.search(r"\bcurrency\b", f["label"], re.I)
                # Michelin (Workday) 2026-10-15: "... in local currency:" wants the number; "80,000 USD" was refused.
                and not re.search(r"\bin (?:local|your(?: local)?|the local) currency\b", f["label"], re.I)
                and re.fullmatch(r"\d{4,7}", str(value).strip())
            ):
                # TransUnion 2026-10-15: "Please specify the amount and currency."
                return self._ans(
                    f, f"{int(str(value).strip()):,} USD", [f"fact:{key}" if key else "fact:desired_salary"]
                )
            if "availability" in keys and str(value).casefold() == "immediately":
                part = re.search(r"\b(month|day|year)\s*$", f["label"], re.I)
                if part and f.get("type") in {"select", "combobox", "text", "number"}:
                    # A start date split into Month / Day / Year menus (Oracle's "earliest possible start date",
                    # Legrand 2026-10-23: "0 days" went into Day and the date was refused): today's part.
                    today = datetime.now(ZoneInfo(self.config.timezone)).date()
                    piece = {"month": f"{today.month:02d}", "day": str(today.day), "year": str(today.year)}[
                        part[1].lower()
                    ]
                    if part[1].lower() == "month":
                        # A month menu offers names (HEXAWARE's "Can Join By Month": January ... December, 2026-10-24).
                        from calendar import month_abbr, month_name

                        spellings = {month_name[today.month], month_abbr[today.month], piece, str(today.month)}
                        piece = next(
                            (o for o in f.get("options") or [] if str(o).strip().casefold() in {x.casefold() for x in spellings}),
                            piece,
                        )
                    if answer := self._ans(f, piece, ["fact:availability"]):
                        return answer
                if f.get("type") in {"date", "date_parts", "month"} or f.get("widget") == "date-text":
                    today = datetime.now(ZoneInfo(self.config.timezone)).date()
                    return self._ans(f, today.strftime("%m/%d/%Y"), ["fact:availability"])
                if "notice period" in label:
                    return self._ans(
                        f, "0" if f.get("type") == "number" else "0 days", ["fact:notice_period"]
                    )
            options = f.get("options", [])
            boolean = isinstance(value, bool) or str(value).lower() in {"true", "false", "yes", "no"}
            if boolean and boolean_detail(f):
                return None  # Retrieve and interpret the requested detail separately from its parent fact.
            if boolean and f.get("type") == "text" and len(f["label"]) > 120:
                return None  # A long free-text prompt is not a yes/no screening question.
            if (
                boolean
                and NEGATED.search(negation_text(f["label"]))
                # A covenant states what the applicant agreed not to do (ABB: "an agreement ... that you will not
                # engage in competition"); the question itself is not negated.
                and not (
                    "has_non_compete" in keys
                    and re.search(r"agree\w*.{0,120}\b(?:not|never)\b", f["label"], re.I)
                )
            ):
                # "Will you NOT require sponsorship?" needs interpretation, not a stored yes/no.
                return None
            if boolean and f.get("type") == "checkbox":
                text = "true" if yes_no(value) == "Yes" else "false"
                return Answer(
                    field_id=f["id"], value=text, disposition="answer", evidence_ids=["fact:" + key]
                )
            answer = yes_no(value) if boolean else str(value)
            if options and boolean:
                yes = [o for o in options if re.match(r"\s*yes\b", o, re.I)]
                no = [o for o in options if re.match(r"\s*no\b", o, re.I)]
                pick = yes if answer == "Yes" else no
                if len(pick) == 1 and len(yes) == 1 and len(no) == 1:
                    return Answer(
                        field_id=f["id"], value=pick[0], disposition="answer", evidence_ids=["fact:" + key]
                    )
                return None
            return self._ans(f, answer, ["fact:" + key], reason="Screening fact")
        return None

    async def written_answer(self, field):
        """Bounded narrative writing uses the same evidence as the submitted PDF."""
        from ..features.knowledge.writing import configured_model, role_brief, write_answer

        packet = self.context_packets[field["id"]]
        rows = self.variant_rows()
        if not packet.items and not rows:
            return None  # The general resolver can explain missing evidence precisely.
        if not hasattr(self, "_brief_lock"):
            self._brief_lock = asyncio.Lock()
        model = configured_model(self.db, self.config, self.run_id)
        try:
            async with self._brief_lock:
                if not hasattr(self, "_writing_brief"):
                    self._writing_brief = await role_brief(self.db, model, self.job)
            result = await write_answer(
                self.db,
                model,
                self.job,
                field["label"],
                packet=packet,
                variant_rows=rows,
                brief=self._writing_brief,
                max_chars=field.get("maxlength") if field.get("maxlength", -1) > 0 else None,
            )
        except Exception as exc:
            from ..models import BudgetExceeded

            if isinstance(exc, BudgetExceeded):
                raise
            return self._invalid(field, "Narrative writing needs internal retry", "site_transient")
        if result.needs_fact:
            return self._invalid(field, result.needs_fact, "missing_fact")
        if result.needs_review:
            return self._invalid(field, "Narrative writing needs repair: " + "; ".join(result.problems)[:240])
        references = {item.fact_id: "knowledge:" + item.fact_id for item in packet.items}
        references.update({row["unit_id"]: "resume:selected" for row in rows if row.get("unit_id")})
        if (
            not result.text
            or not result.evidence_ids
            or any(ref not in references for ref in result.evidence_ids)
        ):
            return self._invalid(field, "Narrative writing returned unsupported evidence references")
        facts = {
            "knowledge:" + item.fact_id: item.value
            for item in packet.items
            if item.kind in {"fact", "history_row"}
        }
        evidence = {
            "knowledge:" + item.fact_id: str(item.value)
            for item in packet.items
            if item.kind not in {"fact", "history_row"}
        }
        evidence["resume:selected"] = self.resume_text
        return self._validate_answer(
            field,
            Answer(
                field_id=field["id"],
                value=result.text,
                disposition="answer",
                evidence_ids=list(dict.fromkeys(references[ref] for ref in result.evidence_ids)),
            ),
            facts,
            evidence,
            {},
        )
