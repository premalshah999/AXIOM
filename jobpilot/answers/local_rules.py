"""Resolver methods: local_rules."""

from datetime import datetime
from urllib.parse import urlsplit
from urllib.parse import urlunsplit
from zoneinfo import ZoneInfo
import re
from ..answer_context import evaluate_eligibility
from ..answer_context import question_head
from ..answer_context import supported_boolean
from ..knowledge import DATA_USE_CONSENT
from ..knowledge import applicable
from ..knowledge import question_text
from ..knowledge import topic
from ..schemas import Answer
from .consent import ACCOMMODATION
from .consent import ACKNOWLEDGEMENT
from .consent import CONSENT_REQUEST
from .consent import DATA_CONSENT
from .consent import ACKNOWLEDGING_ONLY
from .consent import INSTRUCTED_OPTION
from .consent import MARKETING
from .consent import NOT_IDENTITY
from .consent import OTHER_ROLES_CONSENT
from .consent import SIGNATURE
from .consent import STANDARD_CONSENT
from .consent import TODAY
from .contact import ADDRESS
from .contact import CONTACT
from .contact import NAMES
from .contact import address_location_option
from .demographics import DECLINE
from .eligibility import NO_OPTION
from .demographics import DEMOGRAPHIC
from .eligibility import NOT_AVAILABILITY
from .eligibility import NOT_DESIRED
from .eligibility import OTHER_COUNTRY
from .eligibility import US_STATES

CURRENCY = re.compile(
    r"\b(?:dollar|euro|pound|rupee|yen|dirham|real|zloty|franc|krona|krone|peso|yuan|renminbi|won|shekel|rand|ringgit|"
    r"usd|eur|gbp|inr|cad|aud|sgd|jpy|chf|aed)\b",
    re.I,
)
from .history import EDUCATION
from .history import WORK
from .intents import INTENTS
from .sources import UNOFFERED_SOURCE
from .sources import source_choice
from .text import answer_key
from .text import normalize
from .text import option_value
from ..widgets import foreign_fallback
from .text import yes_no

PAGE_LANGUAGE = re.compile(r"(?:select|choose|change) (?:a |your |the )?language|\b(?:rb|ddl|cbo|lst)language\b|"
                           r"(?:display|site|page) language")
ENDONYMS = {"español", "espanol", "français", "francais", "deutsch", "português", "portugues", "italiano", "中文",
            "简体中文", "繁體中文", "日本語", "한국어", "tiếng việt", "polski", "русский", "العربية", "हिन्दी",
            "kreyòl ayisyen"}
# A page-language picker offers only language names (normalized: "español" keeps its letters).
LANGUAGE_NAME = re.compile(
    r"english|espa\w*ol|spanish|fran\w*ais|french|deutsch|german|portugu\w*s|portuguese|italiano|italian|"
    r"chinese|中文|简体中文|繁體中文|日本語|japanese|한국어|korean|tiếng việt|vietnamese|polski|polish|русский|russian|"
    r"العربية|arabic|हिन्दी|hindi|tagalog|kreyòl ayisyen|haitian creole"
)

# A recruiter or staffing agency that presented this employer's opportunities ("Have you spoken to any
# recruiters/agencies...", "Please list recruiter/agency you've spoken with").
RECRUITER_AGENCY = re.compile(
    # Vanguard (Workday 2026-10-27): "Has a search agency submitted your application ... within the last 12 months?"
    r"recruiters?\s*/\s*agenc(?:y|ies)|(?:recruit(?:er|ing)|staffing|search|employment|placement|third.party) "
    r"(?:firm|agenc(?:y|ies))|(?:third.party|external|agency) recruiters?",
    re.I,
)


class LocalRulesMixin:
    def _local(self, f):
        from ..widgets import placeholder_option

        label = normalize(f["label"])
        options = [o for o in f.get("options", []) if not placeholder_option(o)]
        p = self.profile
        if f.get("type") == "checkbox" and re.search(
            r"\bif you (?:are|reside) (?:a |an )?(?:resident (?:of|in) )?(?:the )?"
            r"(?:european union|eu|eea|european economic area|united kingdom|uk)\b",
            label,
        ):
            # "If you are a European Union resident, please check this box" (RoviSys/iCIMS).
            # A conditional box: leave it unchecked when the applicant lives elsewhere.
            residence = str(p.facts.get("country_of_residence") or p.address.country or "")
            if residence and not re.search(
                r"united kingdom|\buk\b|germany|france|spain|italy|netherlands|ireland|belgium|austria|"
                r"sweden|denmark|finland|poland|portugal|greece|czech|hungary|romania|bulgaria|croatia|"
                r"slovakia|slovenia|estonia|latvia|lithuania|luxembourg|malta|cyprus|norway|iceland|liechtenstein",
                residence,
                re.I,
            ):
                return Answer(
                    field_id=f["id"],
                    value="false",
                    disposition="answer",
                    evidence_ids=["fact:country_of_residence"],
                    supporting_facts=["fact:country_of_residence"],
                    reason="Applicant does not reside in the region this box applies to",
                )
        if (
            "language" in label
            and not re.search(
                r"\b(?:speak|spoken|fluen\w*|proficien\w*|read|write|written|native|know|languages|instruction|"
                r"second|other|additional)\b",
                label,
            )
            and not (f.get("group") or f.get("section") or f.get("history_row"))
            and f.get("type") in {"radio", "radiogroup", "pills", "select", "dropdown", "combobox"}
            and options
            and any(normalize(o) == "english" for o in options)
            and all(LANGUAGE_NAME.fullmatch(normalize(o)) for o in options)
            # A picker names itself ("Select a Language", ASP.NET's rbLanguage) or offers languages in their own words.
            and (PAGE_LANGUAGE.search(label) or any(normalize(o) in ENDONYMS for o in options))
        ):
            # The page's own display language ("Select a Language: English / Español", Maximus tax-credit
            # screening after PepsiCo's iCIMS form, 2026-10-22). The application is completed in English.
            english = next(o for o in options if normalize(o) == "english")
            return Answer(field_id=f["id"], value=english, disposition="answer", evidence_ids=["policy:language"],
                          reason="The application is completed in English")
        if re.fullmatch(
            r"(?:are you (?:a |still a |still )?)?(?:still student|current student|currently (?:a )?student|still (?:in school|studying)|currently enrolled)(?: at .+)?",
            label,
        ):
            row = (f.get("group") or "") + " " + (f.get("section") or "")
            m = re.search(r"education\s*(\d+)", row, re.I)
            indices = [int(m[1]) - 1] if m else list(range(len(p.education)))
            entries = [(i, p.education[i]) for i in indices if 0 <= i < len(p.education)]
            month = datetime.now(ZoneInfo(self.config.timezone)).strftime("%Y-%m")
            dated = [(i, e) for i, e in entries if re.fullmatch(r"\d{4}-\d{2}", e.end)]
            if dated and len(dated) == len(entries) and all(e.end < month for _, e in dated):
                value = "false" if f.get("type") == "checkbox" else "No"
                return self._ans(
                    f,
                    value,
                    [f"profile:education.{i}.end" for i, _ in dated],
                    "Confirmed degree end dates are before this application",
                )
        # Combine the actual eligibility propositions before a sponsorship keyword
        # can answer the whole question with an opposite-polarity No.
        if not OTHER_COUNTRY.search(f["label"]) and (
            f.get("type") == "checkbox"
            or (options and all(re.match(r"\s*(?:yes|no)\b", o, re.I) for o in options))
            or (not options and re.match(r"^(?:are|can|will|do) you\b", f["label"], re.I))
        ):
            # Only the question itself: Booz Allen's trailing "If this role is not located in the
            # United States, answer ..." instruction flipped the sponsorship polarity (2026-10-14).
            combined = evaluate_eligibility(question_head(f["label"]), self._facts())
            if combined is not None:
                truth, refs = combined
                value = (
                    ("true" if truth else "false")
                    if f.get("type") == "checkbox"
                    else ("Yes" if truth else "No")
                )
                if answer := self._ans(f, value, refs, "Combined confirmed eligibility facts"):
                    return answer
        # Bare remembered labels cannot override a specific history row.
        row = (f.get("group") or "") + " " + (f.get("section") or "")
        history_row = f.get("history_row") or {}
        if history_row or re.search(r"(?:work experience|experience|employment|education)\s*\d+", row, re.I):
            entry = self._entry_answer(f, label)
            if entry is not None:
                return entry
            patterns = (
                EDUCATION
                if history_row.get("kind") == "education" or re.search(r"education\s*\d+", row, re.I)
                else WORK
            )
            if any(pattern.search(f["label"]) for pattern in patterns.values()):
                return None  # Missing row data is not borrowed from a different entry or an ambiguous review.
        related = f.get("related_question", {})
        # "If yes, ...", "(a) If the answer is Yes, ...", "If you answered yes to the question above, ..."
        condition = re.match(
            r"(?:\(?[a-z0-9]\)?\W*)?if (?:(yes|no|so)|you selected|(?:you answered|the answer is|your answer is) (yes|no))\b",
            label,
        )
        if related.get("value") and condition:
            parent = self.local(
                {
                    "id": "parent",
                    "label": related.get("label", ""),
                    "type": "dropdown",
                    "required": True,
                    "options": related.get("options", []),
                }
            )
            if (
                parent
                and parent.disposition == "answer"
                and normalize(parent.value) == normalize(related["value"])
            ):
                when = condition[1] or condition[2]
                inactive = (
                    (when in {"yes", "so"} and yes_no(parent.value) == "No")
                    or (when == "no" and yes_no(parent.value) == "Yes")
                    or ("if you selected" in label[:20] and "*" in f["label"] and "*" not in related["value"])
                )
                if inactive:
                    asked = ["N/A"] if re.search(r"\b(?:list|enter|write|put) n a\b", label) else []
                    for value in [*asked, "Not applicable", "N/A", "None"]:
                        if answer := self._ans(f, value, parent.evidence_ids):
                            return answer
        if re.search(r"are you applying for (?:a |an )?(?:full.time|internship|co.op)", label):
            job_text = self.job.get("title", "") + " " + self.job.get("description", "")
            internship = bool(re.search(r"\bintern(?:ship)?\b|\bco.op\b", self.job.get("title", ""), re.I))
            full_time = bool(re.search(r"\bfull[ -]?time\b|\bpermanent\b", job_text, re.I))
            if internship or full_time:
                wanted_internship = bool(re.search(r"internship|co.op", label))
                return self._ans(
                    f, "Yes" if internship == wanted_internship else "No", ["policy:application_role_type"]
                )
        if (
            p.autonomous
            and self.answer_rules.salary_stance.strip()
            and re.search(r"desired|expected|expectations?|requirements?|preferred", label)
            and re.search(r"salary|compensation|pay", label)
            and not NOT_DESIRED.search(f["label"])
            and not p.salary_expectation
            and not p.facts.get("desired_salary")
            and f.get("type") in {"text", "textarea"}
            and not options
        ):
            return self._ans(f, self.answer_rules.salary_stance.strip(), ["policy:compensation_choice"])
        if (
            re.search(r"\b(?:full |complete )?legal (?:full )?name\b", label)
            and not re.search(
                r"reference|emergency|spouse|partner|manager|supervisor|referr|parent|guardian|relative|"
                r"company|employer|business|school|maiden|former|previous|other names?|alias",
                label,
            )
            and f.get("type") in {"text", "textarea"}
            and not options
            and (p.first_name and p.last_name or p.name)
        ):
            # Waymo (Greenhouse) 2026-10-18/19: "Please provide your full legal name as it appears on your government ID"
            # failed validation three times because "government" made it a scoped disclosure. It asks for the name.
            legal = (
                " ".join(x for x in (p.first_name, p.middle_name, p.last_name) if x)
                if p.first_name and p.last_name
                else p.name
            )
            if answer := self._ans(f, legal.strip(), ["profile:name"], "The applicant's legal name"):
                return answer
        if (
            re.search(
                r"reasons? for (?:leaving|wanting to leave|departure)|why (?:are|did) you (?:leave|leaving|left)",
                label,
            )
            and f.get("type") in {"text", "textarea"}
            and not options
            and (leaving := self.reason_for_leaving(f))
        ):
            # Crown Innovations (SmartRecruiters) 2026-10-12 asked the owner. The answer states only what the work
            # history records (still in the role, or the month it ended), never a cause it does not record.
            return self._ans(f, leaving[0], [leaving[1]], "Reason for leaving from the recorded work history")
        disclosure = topic(f["label"]) in {
            "restriction",
            "authorization",
            "sponsorship",
            "citizenship",
            "government",
            "referral",
            "conviction",
            "corruption",
            "export_control",
            "clearance",
            "termination",
            "client_relationship",
        } and not STANDARD_CONSENT.search(f["label"])
        # Disciplinary questions keep the disclosure gate even though their
        # "related to ... license" wording no longer classifies as a referral.
        disclosure = disclosure or bool(re.search(r"disciplin", f["label"], re.I))
        # Workday sometimes renders the agreement's only visible label as
        # "Yes *". Its exact observed control name retains the actual meaning.
        # A bare Yes label elsewhere must never imply acceptance or a fact.
        consent_control = any(
            str(f.get(attr, "")).casefold()
            in {"accepttermsandagreements", "termsandconditions--accepttermsandagreements"}
            for attr in ("name", "automation_id", "native_id")
        )
        if (
            p.accept_all_application_terms
            and not MARKETING.search(f["label"])
            and (OTHER_ROLES_CONSENT.search(f["label"]) or CONSENT_REQUEST.search(f["label"]))
        ):
            if f.get("type") == "checkbox":
                value = "true"
            else:
                value = next(
                    (
                        o
                        for o in options
                        if re.match(
                            r"\s*(?:yes\b|i (?:hereby )?(?:consent|agree|accept|authori[sz]e)\b|"
                            r"(?:consent|agree|accept)\b)",
                            str(o),
                            re.I,
                        )
                        and not re.search(r"\b(?:no|not|don.?t|decline|disagree|withdraw)\b", str(o), re.I)
                    ),
                    None,
                )
            if value and (answer := self._ans(f, value, ["policy:consent"])):
                return answer
        if (instructed := INSTRUCTED_OPTION.search(f["label"])) and p.accept_all_application_terms:
            wanted = normalize(instructed["option"])
            value = next((o for o in options if normalize(o) == wanted), None) or (
                instructed["option"] if not options else None
            )
            if value and (
                answer := self._ans(f, value, ["policy:consent"], "The label names the acknowledging option")
            ):
                return answer
        if (
            p.accept_all_application_terms
            and len(options) == 1
            and ACKNOWLEDGING_ONLY.fullmatch(normalize(options[0]))
            and not f["label"].rstrip().endswith("?")
        ):
            # An instruction whose one option only records reading it ("Please enter your employment above using the
            # + Add Another Employment link." offering "Thank you"): it asks nothing about the applicant. The model
            # proposed a job title for it and four applications stalled (2026-10-25).
            if answer := self._ans(f, options[0], ["policy:consent"], "The only option acknowledges an instruction"):
                return answer
        scope = " ".join(str(f.get(k) or "") for k in ("label", "group", "section"))
        if p.application_source and RECRUITER_AGENCY.search(scope) and not re.search(r"\breferr", f["label"], re.I):
            # The engine found this posting itself (the profile's application source); no recruiter or agency
            # presented it. Meijer asked to list them and the owner was asked (2026-10-25).
            no = [o for o in options if NO_OPTION.search(str(o)) and not DECLINE.search(str(o))]
            if len(no) == 1:
                return self._ans(f, no[0], ["policy:source"], "The applicant applied directly, not through an agency")
            if not options and f.get("type") in {"text", "textarea"}:
                return self._ans(f, "N/A", ["policy:source"], "The applicant applied directly, not through an agency")
        if (
            p.accept_all_application_terms
            and DATA_USE_CONSENT.search(f["label"])
            and topic(f["label"]) == "consent"
            and re.match(r"^(?:do you |i )authori[sz]e\b", f["label"], re.I)
            and not (MARKETING.search(f["label"]) and not (f["required"] and p.accept_required_marketing))
        ):
            value = "true" if f.get("type") == "checkbox" else "Yes"
            if answer := self._ans(f, value, ["policy:consent"]):
                return answer
        if (
            p.accept_all_application_terms
            and f.get("type") == "checkbox"
            and consent_control
            and label in {"yes", "agree", "i agree", "accept", "i accept"}
        ):
            return self._ans(
                f, "true", ["policy:consent"], "Recorded consent for the observed agreement control"
            )
        availability_question = any(
            pattern.search(f["label"]) for pattern, keys in INTENTS if "availability" in keys
        )
        history_context = f.get("history_row") or re.search(
            r"experience|employment|education", (f.get("group") or "") + " " + (f.get("section") or ""), re.I
        )
        if (
            availability_question
            and not history_context
            and not NOT_AVAILABILITY.search(f["label"])
            and str(p.facts.get("availability") or p.availability).casefold() == "immediately"
        ):
            # A reviewed "Immediately" must stay dynamic, including before exact KB lookup.
            if (
                f.get("type") in {"date", "date_parts", "month"}
                or f.get("widget") == "date-text"
                or label == "date available to work"
            ):
                today = datetime.now(ZoneInfo(self.config.timezone)).date()
                return self._ans(f, today.strftime("%m/%d/%Y"), ["fact:availability"])
            if "notice period" in label and f.get("type") == "number":
                return self._ans(f, "0", ["fact:notice_period"])
        # Explicit user corrections take priority over an older exact answer.
        if re.search(r"\bveterans?\b", label) and not re.search(
            r"(?:status|definition|describe).{0,30}explain", label
        ):
            fact = (
                "veteran_family" if re.search(r"family|spouse|dependent|child|parent", label) else "veteran"
            )
            if fact in p.facts:
                value = p.facts[fact]
                if value is False:
                    choice = next(
                        (
                            o
                            for o in options
                            if re.match(r"no\b|i am not|not a|non.?veteran", o, re.I)
                            and not DECLINE.search(o)
                        ),
                        None,
                    )
                    if choice:
                        return self._ans(f, choice, ["fact:" + fact])
                    if not options and f.get("type") != "checkbox":
                        return self._ans(f, "No", ["fact:" + fact])
        if re.fullmatch(r"(?:your )?age(?: in years)?|how old are you", label) and "age" in p.facts:
            return self._ans(f, p.facts["age"], ["fact:age"])
        if (
            (
                re.search(r"relocat", label)
                or re.search(
                    r"location preference|preferred (?:office |work |job )?location|office location", label
                )
            )
            and re.search(r"where|which (?:location|office)|locations?|cities|preferred", label)
            and not re.search(r"sponsor|visa|citizen|salary|compensation|consent", label)
            and p.facts.get("relocation_locations") == "Anywhere"
            and options
            and not any(re.match(r"yes\b|no\b", o, re.I) for o in options)
        ):
            # Geographic choices only; never select a refusal or a legal commitment.
            places = [
                o
                for o in options
                if not re.search(
                    r"not |no relocation|unwilling|cannot|can't|remote only|none|decline|prefer not", o, re.I
                )
            ]
            if f.get("type") in {"checkboxgroup", "select-multiple"} and places:
                return Answer(
                    field_id=f["id"],
                    value="\n".join(places),
                    disposition="answer",
                    evidence_ids=["fact:relocation_locations"],
                )
            for preference in [
                "anywhere",
                "any location",
                "all locations",
                *p.facts.get("relocation_preferences", []),
            ]:
                if match := option_value(preference, places):
                    return self._ans(f, match, ["fact:relocation_preferences"])
            if places:
                return self._ans(f, places[0], ["fact:relocation_locations"])
        employer = f.get("employer", self.employer)
        if re.search(r"preferred.{0,60}programming language|programming language.{0,60}interview", label):
            # Interview language is a delegated preference among actual skills.
            for language in ["Python", "Java", "JavaScript", "TypeScript", "C++", "C", "Go", "Rust"]:
                skill_known = any(normalize(s) == normalize(language) for s in p.skills)
                resume_known = bool(
                    re.search(r"(?<!\w)" + re.escape(language) + r"(?!\w)", self.resume_text, re.I)
                )
                if skill_known or resume_known:
                    if answer := self._ans(
                        f, language, ["profile:skills" if skill_known else "resume:selected"]
                    ):
                        return answer
        if (
            f.get("type") == "checkbox"
            and p.accept_all_application_terms
            and re.search(
                r"\bunderstand\b.{0,160}\b(?:application|resume|career profile)\b", f["label"], re.I
            )
            and not MARKETING.search(f["label"])
            and not disclosure
        ):
            # A learned prose acknowledgement ("I understand") must not become an unchecked box.
            return Answer(
                field_id=f["id"], value="true", disposition="answer", evidence_ids=["policy:consent"]
            )
        key = answer_key({**f, "employer": f.get("employer", self.employer)})
        from .validate import BARE_HISTORY_DATE

        # A bare "End Date" names no row: a confirmed answer to one row (an owner's birth date, saved by an earlier
        # build) answered every row, and SuccessFactors kept it as a current role's end date (Supermicro 2026-10-27).
        saved = topic(f["label"]) != "source" and not BARE_HISTORY_DATE.fullmatch(f["label"])
        # Legacy punctuation-stripping hashes are intentionally not reused without
        # their reviewed question. ReviewedAnswer below safely retains that provenance.
        if key in p.approved_answers and saved:
            value = option_value(p.approved_answers[key], options)
            if value is not None:
                return Answer(
                    field_id=f["id"],
                    value=value,
                    evidence_ids=["approved:" + key],
                    disposition="answer",
                    reason="Exact approved question and option set",
                )
        for reviewed in reversed(p.reviewed_answers):
            if (
                saved
                and applicable(reviewed, employer)
                and question_text(reviewed.question) == question_text(f["label"])
                and not foreign_fallback(reviewed.answer, reviewed.options, options)
            ):
                value = option_value(reviewed.answer, options)
                if value is not None:
                    return Answer(
                        field_id=f["id"],
                        value=value,
                        evidence_ids=["reviewed:" + reviewed.id],
                        disposition="answer",
                        reason="Previously confirmed answer",
                    )
        if self.kb and saved:
            # Paraphrases of a confirmed answer (scope, negation and comparators preserved).
            hit = self.kb.lookup(f["label"], options, employer, f.get("type", ""))
            if hit:
                entry, value = hit
                return Answer(
                    field_id=f["id"],
                    value=value,
                    evidence_ids=["reviewed:" + entry.id],
                    disposition="answer",
                    reason="Previously confirmed answer",
                )
        if re.search(
            r"pending or completed disciplinary action|Teaching Regulation Agency", f["label"], re.I
        ) and re.search(r"reputation|disciplin", f["label"], re.I):
            key = "has_circumstances_affecting_employer_reputation"
            value = p.facts.get(key)
            if isinstance(value, bool):
                return self._ans(f, "Yes" if value else "No", ["fact:" + key])
            return self._invalid(
                {**f, "required": True},
                "Confirm whether any circumstances, including professional disciplinary action, require disclosure; narrower criminal or privacy-discipline answers do not establish this",
                "missing_fact",
            )
        if (
            topic(f["label"]) == "source"
            and not p.application_source
            and options
            and not f.get("options_partial")
        ):
            if value := source_choice(options, self.answer_rules):
                return Answer(
                    field_id=f["id"], value=value, evidence_ids=["policy:source"], disposition="answer"
                )
        if topic(f["label"]) == "source" and p.application_source and f.get("options_partial"):
            # Search-driven pickers (Workday prompts) find the leaf ("Job Boards › LinkedIn") by name.
            return Answer(
                field_id=f["id"],
                value=p.application_source,
                evidence_ids=["policy:source"],
                disposition="answer",
            )
        if topic(f["label"]) == "source" and p.application_source:
            value = option_value(p.application_source, options)
            if value is None and label == "source type" and p.application_source.casefold() == "linkedin":
                value = next((o for o in options if normalize(o) == "social media"), None)
            if value is None:
                value = next((o for o in options if p.application_source.casefold() in o.casefold()), None)
            if value is None:
                if p.application_source.casefold() == "linkedin":
                    value = next((o for o in options if normalize(o) in {"job board", "job boards"}), None)
            if value is None:
                value = next(
                    (
                        o
                        for o in options
                        if normalize(o)
                        in {"social media", "social network", "job board", "job boards", "other"}
                    ),
                    None,
                )
            if value is None and p.application_source.casefold() in {
                "linkedin",
                "indeed",
                "glassdoor",
                "ziprecruiter",
            }:
                # The exact source is not offered. A truthful general category is:
                # job board, then social/professional network, then the internet.
                for pattern in (
                    r"job (?:board|site)",
                    r"social (?:media|network)|professional network",
                    r"\b(?:internet|online|web ?site)\b",
                ):
                    value = next((o for o in options if re.search(pattern, o, re.I)), None)
                    if value:
                        break
            if value is None:
                # Owner policy: any listed source (LinkedIn, company website, careers page,
                # ZipRecruiter, Indeed, internet search, ...) is a true answer.
                value = source_choice(options, self.answer_rules)
            if value is not None:
                return Answer(
                    field_id=f["id"], value=value, evidence_ids=["policy:source"], disposition="answer"
                )
            # The applicant's source is already known. A restricted or stale
            # catalog is a control problem, not a request to repeat that fact.
            # Owned recovery can search or navigate categories, but cannot
            # substitute an unrelated offered answer just to advance.
            return self._invalid(
                f,
                UNOFFERED_SOURCE + "; inspect its categories or search",
                "control_failure",
            ).model_copy(update={"supporting_facts": ["policy:source"]})
        # An explicit agreement option on a policy acknowledgement is consent,
        # even when the policy discusses disabilities or background screening.
        if (
            p.accept_all_application_terms
            and not disclosure
            and STANDARD_CONSENT.search(f["label"])
            and f.get("type")
            in {"select", "dropdown", "radiogroup", "radio", "pills", "buttonchoice", "combobox", "prompt"}
        ):
            consent = next(
                (
                    o
                    for o in options
                    if re.match(
                        r"^(?:i )?(?:agree|accept|consent|acknowledge)\b|^i have read and agree\b",
                        normalize(o),
                    )
                    and not re.search(r"\b(?:do not|don't|decline|disagree)\b", o, re.I)
                ),
                None,
            )
            if consent is None and re.search(r"by selecting [\"'“]?continue[\"'”]?", f["label"], re.I):
                consent = next((o for o in options if normalize(o) == "continue"), None)
            if consent:
                return self._ans(f, consent, ["policy:consent"])
        # A mention of a protected reason in an ability/onsite question is not a
        # request to disclose demographic identity.
        generic_disclosure_label = bool(
            re.fullmatch(
                # Acuity (SuccessFactors) 2026-10-12 labels its veteran picklist only "Please Select:".
                r"(?:please )?(?:select|choose|check)(?: (?:one|an option|one option|one of the boxes below|one of the following))?|"
                r"please make a selection|unlabelled field",
                label.strip(" .:*"),
            )
        )
        demographic_question = (
            bool(
                DEMOGRAPHIC.search(label)
                or (generic_disclosure_label and any(DEMOGRAPHIC.search(o) for o in options))
            )
            and not bool(
                re.search(
                    r"\b(?:able to|work onsite|work on site|work in the office|meet this requirement)\b",
                    label,
                )
            )
            and not DATA_CONSENT.search(f["label"])
            and not NOT_IDENTITY.search(f["label"])
            # A form's date stamp ("Disability Questionnaire Answered Date") and an accommodation request are
            # not self-identification questions, even when they mention disability.
            and not (f.get("type") in {"date", "date_parts"} or TODAY.search(label))
            and not ACCOMMODATION.search(question_head(label))
        )
        hispanic_proposition = re.fullmatch(
            r"(?:(?:are you|do you (?:self )?identify as|do you consider yourself|i am) )?"
            r"(?P<negative>not |non )?(?:of )?(?:hispanic(?: (?:or )?latino)?|latino(?: (?:or )?hispanic)?)"
            r"(?: origin| ethnicity| descent)?",
            # "Are you Hispanic or Latino (A person of Cuban, Mexican, ...)?" keeps its
            # definition in parentheses; the proposition is the text outside them.
            normalize(re.sub(r"\([^)]*\)", " ", f["label"])),
        )
        if demographic_question and hispanic_proposition:
            explicit = supported_boolean(self._facts(), ("hispanic_or_latino", "is_hispanic", "hispanic"))
            if explicit is not None:
                truth, refs = explicit
                if hispanic_proposition["negative"]:
                    truth = not truth
                value = ("true" if truth else "false") if f.get("type") == "checkbox" else yes_no(truth)
                if answer := self._ans(f, value, refs, "Explicit applicant-confirmed demographic answer"):
                    return answer
        demo_rule = self.demographic_rule(f) if demographic_question else None
        if (
            demographic_question
            and (demo_rule.decline_first or demo_rule.answer)
            and f.get("type") != "checkbox"
        ):
            if (
                demo_rule.decline_first
                and not options
                and f.get("valid")
                and DECLINE.search(str(f.get("value", "")))
            ):
                # The website already displays the approved decline policy.
                return Answer(
                    field_id=f["id"],
                    value=f["value"],
                    disposition="answer",
                    evidence_ids=["policy:demographics"],
                    reason="Decline disclosure; never infer demographic identity",
                )
            if answer := self.demographic_chain(f, options):
                return answer
            return Answer(
                field_id=f["id"],
                disposition="review" if f["required"] else "skip",
                evidence_ids=["policy:demographics"],
                reason="Decline disclosure; never infer demographic identity",
            )
        if (
            p.decline_demographics
            and f.get("type") == "checkbox"
            and (
                demographic_question
                # A neighbour's ethnicity wording never makes a certification/consent demographic
                # (AbbVie: "I agree that everything submitted is truthful and accurate").
                or (
                    DEMOGRAPHIC.search(f.get("peers", ""))
                    and not ACKNOWLEDGEMENT.search(f["label"])
                    and not DATA_CONSENT.search(f["label"])
                )
            )
        ):
            # Disability checkbox lists: tick only the explicit decline choice.
            if DECLINE.search(f["label"]):
                return Answer(
                    field_id=f["id"], value="true", disposition="answer", evidence_ids=["policy:demographics"]
                )
            return Answer(field_id=f["id"], disposition="skip", reason="Demographic disclosure declined")
        # Rows ("Work Experience 2") own their Location/From/To fields; check them before contact facts.
        entry = self._entry_answer(f, label)
        if entry is not None:
            return entry
        status = str(p.facts.get("current_immigration_status_US") or "")
        if status and re.search(
            r"non ?immigrant (?:category|status|visa)|(?:current )?(?:visa|immigration) (?:status|type|category)|"
            r"type of (?:visa|work authori[sz]ation)|work authori[sz]ation (?:type|category)",
            label,
        ):
            # Asked exactly what the owner's status fact is for (Cruise Planner 2026-10-23 asked the owner).
            if re.search(r"\bOPT\b", status):
                stated = "F-1 OPT"
                if options:
                    hits = [o for o in options if re.search(r"\bOPT\b|\bF-?1\b", str(o), re.I)]
                    if len(hits) == 1:
                        return Answer(field_id=f["id"], value=hits[0], disposition="answer",
                                      evidence_ids=["fact:current_immigration_status_US"])
                elif a := self._ans(f, stated, ["fact:current_immigration_status_US"]):
                    return a
        if (
            re.search(r"(?:clearable|eligible (?:to obtain|for)|able to obtain|obtain and maintain|qualify for).{0,60}"
                      r"(?:clearance|public trust)|(?:clearance|public trust).{0,60}(?:clearable|eligib|obtain)", label)
            and str(p.facts.get("us_citizen")).casefold() in {"false", "no"}
            and re.search(r"\bOPT\b|\bF-?1\b|\bH-?1B\b", status)
            and options
        ):
            # Federal clearances and Public Trust positions require U.S. citizenship (some accept permanent residents);
            # the owner is a foreign national on a student/work visa (Laansu 2026-10-23 asked the owner).
            no = [o for o in options if re.fullmatch(r"\s*no\b.*", str(o), re.I)]
            if len(no) == 1:
                return Answer(field_id=f["id"], value=no[0], disposition="answer",
                              evidence_ids=["fact:us_citizen", "fact:current_immigration_status_US"])
        no_clearance = next(
            (k for k in ("active_security_clearance", "has_security_clearance", "security_clearance")
             if str(p.facts.get(k)).casefold() in {"false", "no"}),
            None,
        )
        if no_clearance and options and re.search(r"\bclearances?\b", label) and not re.search(
            r"clearable|eligib|obtain|willing|able to", label
        ):
            # Credence (Workable 2026-10-27): "Please indicate which active clearances you have." lists the levels and
            # "I currently do not have an active security clearance"; the owner was asked though the profile says none.
            none = [o for o in options if re.search(
                r"(?:do not|don't|not) (?:currently )?(?:have|hold)|\bno (?:active |current )?(?:security )?clearance"
                r"|^\s*none\b|not applicable", str(o), re.I)]
            # Node.Digital (Workable 2026-10-27) asked "Do you have an active security clearance?" with YES / NO.
            nos = [o for o in options if yes_no(str(o)) == "No"]
            if not none and len(nos) == 1 and re.search(r"\b(?:do|does) you (?:currently )?(?:have|hold|possess)\b", label):
                none = nos
            if len(none) == 1:
                return Answer(field_id=f["id"], value=none[0], disposition="answer",
                              evidence_ids=["fact:" + no_clearance], reason="The applicant holds no security clearance")
        numbered = re.fullmatch(
            r"(?:link|url|website|web site|web link|online profile|social (?:media )?(?:link|profile)|other link)\s*#?\s*(\d)",
            label,
        )
        if numbered and not options and f.get("type") in {"text", "url"}:
            # Oracle's "Link 1" / "Link 2" (Citizens, Verisk 2026-10-23: "Link 2" went to the owner as a question while
            # the website was saved). The saved links in order: LinkedIn, website, GitHub.
            links = [(attr, getattr(p, attr)) for attr in ("linkedin", "website", "github") if getattr(p, attr)]
            index = int(numbered[1]) - 1
            if 0 <= index < len(links):
                attr, value = links[index]
                if a := self._ans(f, value, ["profile:" + attr]):
                    return a
        if (
            re.fullmatch(r"(?:title|salutation|prefix|name prefix|honorific|courtesy title)", label)
            and options
            and not (f.get("group") or f.get("history_row"))
            and any(re.fullmatch(r"mr\.?|mister", str(o).strip(), re.I) for o in options)
        ):
            # A salutation menu (Mr./Ms./Mx./Dr.), never a job title: the applicant's saved gender answers it.
            gender = str(p.facts.get("gender") or "").strip().casefold()
            wanted = "mr" if gender in {"male", "man"} else "ms" if gender in {"female", "woman"} else ""
            choice = next((o for o in options if re.sub(r"[^a-z]", "", str(o).casefold()) == wanted), None) if wanted else None
            if choice:
                return Answer(field_id=f["id"], value=choice, disposition="answer", evidence_ids=["fact:gender"])
        if f.get("type") == "url" and f.get("value"):
            for attr in ("website", "linkedin", "github"):
                saved = getattr(p, attr)
                current = str(f["value"]).removeprefix("https://").removeprefix("http://").rstrip("/")
                known = str(saved).removeprefix("https://").removeprefix("http://").rstrip("/")
                if known and current.casefold() == known.casefold():
                    return self._ans(f, saved, ["profile:" + attr])
        for attr, labels in CONTACT.items():
            email_prompt = attr == "email" and re.fullmatch(
                r"(?:(?:enter|type)(?: your)? |your )?email(?: address)?", label
            )
            if attr == "location" and label in labels and f.get("type") == "combobox":
                qualified = address_location_option(options, p.address)
                if qualified:
                    return self._ans(
                        f,
                        qualified,
                        ["profile:address.city", "profile:address.state", "profile:address.country"],
                    )
            phone_prompt = attr == "phone" and f.get("phone_format") == "national"
            if (label in labels or email_prompt or phone_prompt) and (value := getattr(p, attr)):
                if attr == "phone" and options:
                    continue
                if attr == "phone" and f.get("phone_format") == "national":
                    from ..widgets import phone_value

                    value = phone_value(f, value, p.phone_country_code)
                candidates = [value]
                if attr == "linkedin":
                    parsed = urlsplit(value)
                    if parsed.hostname == "linkedin.com" and parsed.path.startswith("/in/"):
                        candidates = [urlunsplit(parsed._replace(netloc="www.linkedin.com"))]
                if label == "city state country" and p.address.country:
                    candidates = [f"{value}, {p.address.country}"]
                target = f
                if attr == "location" and (label in {"location city"} or f.get("type") == "combobox"):
                    # Location type-aheads search by city; "New York, NY" often returns nothing.
                    candidates.insert(0, value.split(",")[0].strip())
                    if f.get("type") == "combobox":
                        # A remote search box: its unsearched list rarely holds the city.
                        # The driver types the city and picks the result in the profile's region.
                        target = {**f, "options_partial": True}
                for candidate in candidates:
                    if a := self._ans(target, candidate, ["profile:" + attr]):
                        return a
        first, last = p.given_names()
        names = {
            "first": first,
            "last": last,
            "middle": p.middle_name,
            "preferred": p.preferred_name or first,
        }
        for part, labels in NAMES.items():
            if label in labels and names[part]:
                return Answer(
                    field_id=f["id"],
                    value=names[part],
                    disposition="answer",
                    evidence_ids=["fact:" + part + "_name"],
                )
        # Dayforce (HH Global 2026-10-18) renders "Disability Questionnaire Answered Date" as a text input.
        strict_today = re.search(r"today|signed|signature|answered|completed|completion", label)
        # S&P Global / Kensho (Workday) 2026-10-16: the consent's own date input carries the whole consent text.
        consent_date = bool(
            (CONSENT_REQUEST.search(f["label"]) or ACKNOWLEDGEMENT.search(f["label"]))
            # A date of employment is not the consent's own date; "for employment purposes" in the consent text is
            # (EPIC's Consumer Report authorization dated itself, 2026-10-25).
            and not re.search(
                r"birth|start|graduat|availab|expir|end date|dates? of employment|employment (?:start|end|date)",
                label,
            )
        )
        if part := re.fullmatch(r"today s date (month|day|year)", label):
            # A self-identification form's date in three controls (forms.label_signature_date), in the options' format.
            today = datetime.now(ZoneInfo(self.config.timezone)).date()
            candidates = {
                "year": [str(today.year)],
                "day": [f"{today.day:02d}", str(today.day)],
                "month": [today.strftime("%B"), today.strftime("%b"), f"{today.month:02d}", str(today.month)],
            }[part[1]]
            for value in candidates:
                if answer := self._ans(f, value, ["policy:today"]):
                    return answer
        if (f.get("type") in {"date_parts", "date"} and (TODAY.search(label) or consent_date)) or (
            f.get("type") == "text" and TODAY.search(label) and strict_today
        ):
            today = datetime.now(ZoneInfo(self.config.timezone)).date()
            return Answer(
                field_id=f["id"],
                value=f"{today.month:02d}/{today.day:02d}/{today.year}",
                disposition="answer",
                evidence_ids=["policy:today"],
            )
        if SIGNATURE.search(f["label"]) and f.get("type") in {"text", "textarea"} and p.name:
            return Answer(field_id=f["id"], value=p.name, disposition="answer", evidence_ids=["profile:name"])
        addr = p.address
        # An applicant's city can be a suburb. Closest-metropolitan-region questions, and Region pickers that do not
        # offer that city (Taleo "Place of Residence"), take the saved metro region (facts.metro_region).
        metro = str((p.facts or {}).get("metro_region") or "").strip()
        metro_question = bool(
            re.search(
                r"\bmetro(?:politan)?\b.{0,20}\b(?:area|region)\b|\b(?:closest|nearest) (?:major )?(?:city|metro)",
                label,
            )
        )
        region_picker = bool(
            label in {"region", "locality", "area"}
            and options
            and addr.city
            and not any(normalize(o) == normalize(addr.city) for o in options)
        )
        if metro and (metro_question or region_picker):
            if a := self._ans(f, metro, ["fact:metro_region"]):
                return a
        if metro_question and addr.city:
            if a := self._ans(f, addr.city, ["profile:address.city"]):
                return a
        # MAXIMUS's tax-credit survey (PepsiCo on iCIMS, 2026-10-24) asks one page at a time "Are you a resident of the
        # State of California?": residence in a named US state is the saved address's state.
        named = re.search(r"\bresident of (?:the )?(?:state of )?([a-z ]+?)$", label)
        states = {normalize(name): code for code, name in US_STATES.items()}
        if addr.state and named and named[1].strip() in states:
            saved = addr.state.strip()
            home = saved.upper() if saved.upper() in US_STATES else states.get(normalize(saved), "")
            if home and (a := self._ans(f, "Yes" if states[named[1].strip()] == home else "No", ["profile:address.state"])):
                return a
        # BNY (Oracle, 2026-10-24): "Salary Expectation in Local Currency" is a currency menu (Euro, US Dollar, ...).
        currencies = [o for o in options if CURRENCY.search(o)]
        if len(currencies) >= 3 and len(currencies) >= len(options) - 1 and re.search(r"currenc|salary|compensation|pay", label):
            # The job's own location decides; the applicant's residence only when the posting names none.
            where = str(self.job.get("location") or "").strip()
            us = (
                re.search(r"united states|\busa?\b", where, re.I)
                or re.search(r", [A-Z]{2}\b", where)
                if where
                else re.search(r"united states|\busa?\b", addr.country or "", re.I)
            )
            if us:
                dollar = next((o for o in currencies if re.search(r"\bus(?:d| dollar)\b|united states dollar", o, re.I)), None)
                if dollar and (a := self._ans(f, dollar, ["profile:address.country"])):
                    return a
        # "Please indicate all entities for which you are currently working or have worked" (the Federal Reserve,
        # 2026-10-23): with none of the listed organizations in the work history, its none-choice is the answer.
        none_choice = next(
            (o for o in options if re.fullmatch(r"\s*(?:none(?: of (?:the|these)(?: above)?)?|not applicable|n/?a|never)\W*", o, re.I)),
            None,
        )
        if (
            none_choice
            and p.work
            and re.search(
                r"\b(?:entities|companies|organi[sz]ations|agencies|subsidiaries|affiliates|employers)\b.{0,80}"
                r"\b(?:work(?:ed|ing)?|employed)\b",
                label,
            )
            and not any(
                (company := normalize(entry.company)) and (company in normalize(o) or normalize(o) in company)
                for entry in p.work
                for o in options
                if o != none_choice
            )
        ):
            if a := self._ans(f, none_choice, ["profile:work"]):
                return a
        credit = re.search(r"\bcredit hours?\b.{0,80}\b(?:complet|earn|anticipat)", label)
        internship = re.search(
            r"\binternship\b.{0,40}\b(?:compulsory|mandatory|required)\b.{0,40}\b(?:validat|diploma|degree|year)", label
        )
        if p.education and (credit or internship):
            # Questions about a degree still in progress (RTX's credit hours by the start date, Airbus' "internship
            # compulsory to validate your diploma"), asked of an applicant whose degrees are all complete.
            ranks = ["doctor", "master", "bachelor", "associate", "high school"]
            index = min(
                range(len(p.education)),
                key=lambda i: next((k for k, r in enumerate(ranks) if r in normalize(p.education[i].degree)), len(ranks)),
            )
            highest = p.education[index]
            month = datetime.now(ZoneInfo(self.config.timezone)).strftime("%Y-%m")
            if all(e.end and e.end <= month for e in p.education):
                if credit and f.get("type") in {"text", "textarea"}:
                    from calendar import month_name

                    year, number = highest.end.split("-")[:2]
                    study = f" in {highest.field_of_study}" if highest.field_of_study else ""
                    return Answer(
                        field_id=f["id"],
                        value=f"All credit hours required for my {highest.degree}{study}; I completed the degree in "
                        f"{month_name[int(number)]} {year}.",
                        disposition="answer",
                        evidence_ids=[f"profile:education.{index}.degree", f"profile:education.{index}.end"],
                    )
                if internship and (a := self._ans(f, "No", [f"profile:education.{index}.end"])):
                    return a
        for part, labels in ADDRESS.items():
            if label in labels and getattr(addr, part):
                if part == "city" and f.get("type") in {"combobox", "dropdown", "select"}:
                    qualified = address_location_option(options, addr)
                    if qualified:
                        return self._ans(
                            f,
                            qualified,
                            ["profile:address.city", "profile:address.state", "profile:address.country"],
                        )
                if a := self._ans(f, getattr(addr, part), ["profile:address." + part]):
                    return a
        if label in ADDRESS["city"] and not addr.city and p.location:
            if a := self._ans(f, p.location.split(",")[0].strip(), ["profile:location"]):
                return a
        region = p.location.rsplit(",", 1)[1].strip() if "," in p.location else ""
        if label in ADDRESS["state"] and not addr.state and region:
            for candidate in [US_STATES.get(region.upper(), region), region]:
                if a := self._ans(f, candidate, ["profile:location"]):
                    return a
        if label in ADDRESS["country"] | {"what is your current country of residence"} and not addr.country:
            country = p.facts.get("country_of_residence") or p.facts.get("country")
            if not country and (region.upper() in US_STATES or region in US_STATES.values()):
                country = "United States"
            if country and (a := self._ans(f, str(country), ["profile:location"])):
                return a
        if re.fullmatch(r"address type", label) and options:
            # The applicant's own address is their home address (iCIMS "Type": Home / Other).
            home = next((o for o in options if normalize(o) == "home"), None)
            if home and p.address.line1:
                return Answer(field_id=f["id"], value=home, disposition="answer", evidence_ids=["profile:address"])
        if re.search(r"phone.*(?:device|type)|type of phone|phone type", label) and p.phone_type:
            if a := self._ans(f, p.phone_type, ["profile:phone_type"]):
                return a
        if (
            p.phone
            and f.get("type") in {"text", "tel"}
            and not options
            and re.search(r"\b(?:phone|mobile|telephone)\b", label)
            # Publicis (2026-10-24): "Phone Number (Please include Country Code in this format - example: +44 ...)"
            # took the residence country name from the calling-code rule below.
            and re.search(r"(?:\+|incl(?:ude|uding)?\.?|with)\s*(?:the\s*)?country\s*code", f["label"], re.I)
        ):
            # Boehringer (SuccessFactors) 2026-10-16: "Main Phone (+ Country Code)" is the number itself;
            # the calling-code rule below typed the residence country into it. The code comes only
            # from saved facts, never a guess.
            code = re.sub(r"\D", "", p.phone_country_code or "")[:3]
            if not code and re.fullmatch(
                r"united states(?: of america)?|usa?|u\.s\.a?\.?", (addr.country or "").strip(), re.I
            ):
                code = "1"
            digits = re.sub(r"\D", "", p.phone)
            if code and digits.startswith(code) and (p.phone.strip().startswith("+") or len(digits) > 10):
                digits = digits[len(code) :]
            if code and len(digits) >= 7:
                return Answer(
                    field_id=f["id"],
                    value=f"+{code} {digits}",
                    disposition="answer",
                    evidence_ids=["profile:phone", "profile:phone_country_code"],
                )
        if re.search(
            r"country(?: region| territory)? (?:phone )?code|phone ?(?:country|code)|dialing code|country calling",
            label,
        ):
            # ADP's react-phone-number-input select is unlabeled, named "phoneCountry"; the model chose
            # Uzbekistan, then Vanuatu, and the mobile number was refused (St. Onge 2026-10-23).
            # +1 alone cannot distinguish US from Canada. Match the saved
            # residence first; a standalone dialing-code catalog can use +1.
            for candidate, ref in [
                (addr.country, "profile:address.country"),
                (p.phone_country_code, "profile:phone_country_code"),
            ]:
                if candidate and (a := self._ans(f, candidate, [ref])):
                    return a
        # Taleo (Bealls 2026-10-17): "Cellular Number" and "Home Phone Number" are the applicant's own phone;
        # work, office and fax numbers are not.
        if (
            re.search(
                r"^(?:(?:home|cell(?:ular)?|mobile|primary|personal|contact) )?"
                r"(?:phone|mobile|telephone|cell(?:ular)?)(?: phone)?(?: number)?$",
                label,
            )
            and p.phone
        ):
            digits = re.sub(r"[^\d+]", "", p.phone)
            local = digits[2:] if digits.startswith("+1") and len(digits) == 12 else digits
            return Answer(field_id=f["id"], value=local, disposition="answer", evidence_ids=["profile:phone"])
        if f.get("type") in {
            "radio",
            "radiogroup",
            "select",
            "dropdown",
            "combobox",
            "buttonchoice",
        } and MARKETING.search(f["label"]):
            # Required means an answer is mandatory, not that consent is. An
            # offered decline respects the applicant's SMS/marketing preference.
            decline = next(
                (
                    o
                    for o in options
                    if re.match(r"\s*No\b", o, re.I)
                    or re.fullmatch(
                        r"(?:i )?(?:do not (?:consent|agree|accept)|decline|opt[ -]out)", normalize(o)
                    )
                ),
                None,
            )
            choice = "No" if decline else "Yes" if f["required"] and p.accept_required_marketing else "No"
            offered = next((o for o in options if re.match(r"\s*" + choice + r"\b", o, re.I)), None)
            if not offered:
                pattern = (
                    r"^(?:i )?(?:consent|agree|accept|opt[ -]in)$"
                    if choice == "Yes"
                    else r"^(?:i )?(?:do not (?:consent|agree|accept)|decline|opt[ -]out)$"
                )
                offered = next((o for o in options if re.fullmatch(pattern, normalize(o))), None)
            if not options:
                offered = choice
            if offered:
                return Answer(
                    field_id=f["id"], value=offered, disposition="answer", evidence_ids=["policy:consent"]
                )
        if (
            f.get("type") == "checkboxgroup"
            and len(options) == 1
            and p.accept_all_application_terms
            and not disclosure
            and STANDARD_CONSENT.search(options[0])
            and not re.search(r"\b(?:do not|don't|decline|disagree)\b", options[0], re.I)
        ):
            return Answer(
                field_id=f["id"], value=options[0], disposition="answer", evidence_ids=["policy:consent"]
            )
        if (
            p.accept_all_application_terms
            and not disclosure
            and STANDARD_CONSENT.search(f["label"])
            and f.get("type") in {"select", "dropdown", "radiogroup", "radio", "pills", "buttonchoice"}
        ):
            consent = next(
                (
                    o
                    for o in options
                    if re.fullmatch(r"(?:i )?(?:agree|accept|consent|acknowledge)", normalize(o))
                ),
                None,
            )
            if consent:
                return self._ans(f, consent, ["policy:consent"])
        if f.get("type") == "checkbox":
            if p.accept_all_application_terms and re.search(
                r"\b(?:do not|don t|don['’]t|refuse to|decline to) (?:agree|accept|consent|authori[sz]e)\b|"
                r"\bdisagree\b",
                f["label"],
                re.I,
            ):
                return self._ans(
                    f, "false", ["policy:consent"], "Acceptance policy does not select a refusal"
                )
            if MARKETING.search(f["label"]):
                if f["required"] and p.accept_required_marketing:
                    return Answer(
                        field_id=f["id"], value="true", disposition="answer", evidence_ids=["policy:consent"]
                    )
                return Answer(field_id=f["id"], disposition="skip", reason="Optional marketing opt-in")
            all_terms = (
                p.accept_all_application_terms
                and bool(
                    re.search(
                        r"\b(?:agree|accept|consent|acknowledge|certify|terms|privacy|policy|authori[sz]e|confirm)\b|data process|"
                        r"understand.{0,120}(?:application|resume|career profile)",
                        f["label"],
                        re.I,
                    )
                )
                and not disclosure
            )
            routine_consent = (
                p.allow_application_consents
                and bool(
                    re.search(
                        r"privacy (?:policy|notice)|process.{0,30}(?:personal|application) data|accuracy.{0,30}(?:application|information)|information.{0,30}(?:true|accurate)",
                        f["label"],
                        re.I,
                    )
                )
                and not re.search(r"marketing|arbitration|waiv|background|credit check", f["label"], re.I)
            )
            if f["label"] in p.approved_consents or routine_consent or all_terms:
                return Answer(
                    field_id=f["id"], value="true", disposition="answer", evidence_ids=["policy:consent"]
                )
            if MARKETING.search(f["label"]) and not f["required"]:
                return Answer(field_id=f["id"], disposition="skip", reason="Optional marketing opt-in")
        if re.search(r"spouse of an? (?:h\s*1b|l\s*1|e\s*[123])|spousal status", label, re.I):
            status = str(p.facts.get("current_immigration_status_US", ""))
            if re.search(r"\b(?:f-?1|opt|cpt)\b", status, re.I):
                return self._ans(f, "No", ["fact:current_immigration_status_US"])
        if re.search(r"without (?:any )?restrictions.{0,45}(?:length|duration|period|time)", label):
            status = str(p.facts.get("current_immigration_status_US", ""))
            if re.search(r"\b(?:opt|cpt|f.?1|h.?1b|l.?1|tn)\b", status, re.I):
                return self._ans(f, "No", ["fact:current_immigration_status_US"])
        if re.search(r"(?:certification|clearance) requirement.{0,50}job description", label):
            # This offered answer describes the posting, not the applicant's
            # clearance status. Never borrow a willingness/eligibility fact.
            description = str(self.job.get("description", ""))
            not_applicable = next(
                (
                    o
                    for o in options
                    if re.search(
                        r"not applicable.{0,40}(?:not|no).{0,50}(?:certification|clearance) requirement",
                        o,
                        re.I,
                    )
                ),
                None,
            )
            if (
                not_applicable
                and len(description) >= 1200
                and not re.search(
                    r"certificat|clearance|\b(?:CISSP|CISM|CISA|CPA|PMP|CFA|CompTIA)\b", description, re.I
                )
            ):
                return self._ans(f, not_applicable, ["job:description"])
        # Unresolved or conditional compound eligibility cannot fall back to one keyword.
        from ..answer_context import AUTH as ELIGIBILITY_AUTH, SPONSOR

        if ELIGIBILITY_AUTH.search(f["label"]) and SPONSOR.search(f["label"]):
            return None
        intent = self._intent_answer(f, label)
        if intent is not None:
            return intent
        if f.get("type") == "checkbox":
            if WORK["current"].search(f["label"]):
                return None  # The model can determine current employment from dated resume evidence.
            if f["required"]:
                # A checkbox is a widget, not proof that its question is a new
                # consent. Let supported semantic reasoning interpret unknown
                # factual captions before asking the applicant.
                return None
            return Answer(
                field_id=f["id"],
                disposition="skip",
                reason="This commitment has not been approved",
            )
        return None
