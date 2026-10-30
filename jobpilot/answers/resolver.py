"""The Resolver: question in, answer out."""

from .demographic_rules import DemographicRulesMixin
from .history_rules import HistoryRulesMixin
from .consent_rules import ConsentRulesMixin
from .policy_rules import PolicyRulesMixin
from .people_rules import PeopleRulesMixin
from .intent_rules import IntentRulesMixin
from .model_batch import ModelBatchMixin
from .validate import ValidateMixin
from .local_rules import LocalRulesMixin
import hashlib
import json
import re
from ..answer_context import CACHE_VERSION
from ..answer_context import boolean
from ..answer_context import defined_screening
from ..answer_context import digest
from ..answer_context import screening_text
from ..answer_rules import delegated_facts
from ..knowledge import applicable
from ..knowledge import topic
from ..schemas import Answer
from ..schemas import Profile
from .consent import SELECT_ALL
from .consent import TODAY
from .contact import CONTACT
from .contact import display_name_case
from .demographics import DECLINE
from .demographics import demographic_topic
from .demographics import strip_fact_annotations
from .history import EDUCATION
from .history import HISTORY_CONTACT
from .history import HISTORY_REGION
from .history import WORK
from .text import normalize
from .text import option_value
from .text import yes_no

# A repeated history row's group: Workday's "Education 2", "Work Experience 1", "Language 1".
HISTORY_ROW = re.compile(r"(?:work experience|experience|education|employment|language)s?\s*\d+\b", re.I)


# A history row's group: numbered ("Education 2", "Work Experience 1") or, on Oracle, named by its school or employer
# ("Education at Other", HEXAWARE 2026-10-26: old answers and evidence conflicts reached its End Date Month first).
HISTORY_GROUP = re.compile(
    r"(?:work experience|experience|employment|education)\s*\d+|"
    r"^\s*(?:education|experience|work experience|employment)\s+at\s+\S",
    re.I,
)


# A question that asks which country the applicant is a citizen of ("country of citizenship", "country of your
# nationality", "citizenship (country)").
CITIZENSHIP_COUNTRY_ASK = re.compile(
    r"\bcountr(?:y|ies)\b[^.?]{0,25}?\b(?:citizenship|nationality)\b|\b(?:citizenship|nationality)\b\s*(?:\(\s*)?countr",
    re.I,
)
# What else the question asks for beyond the first country; a further country is given only when it does.
CITIZENSHIP_MORE_ASK = re.compile(
    r"\bif different\b|\bdifferent\b|\bother\b|\badditional\b|\bdual\b|\bsecond\b|\ball\b|\bmost recently\b", re.I
)
# Facts that name the applicant's citizenship country, in the order they are read; and a further country they hold.
CITIZENSHIP_COUNTRY_KEYS = ("country_of_citizenship", "citizenship", "nationality", "country_of_nationality")
OTHER_CITIZENSHIP_KEY = re.compile(
    r"^(?:second(?:ary)?|other|additional|dual)_(?:country_of_)?(?:citizenship|nationality)$|"
    r"^(?:citizenship|nationality)_(?:2|two|second(?:ary)?|other|additional)$|"
    r"^country_of_(?:second(?:ary)?|other|additional|dual)_(?:citizenship|nationality)$"
)
RESIDENCY_COUNTRY_KEY = re.compile(r"^(?:country_of_)?permanent_resid\w*(?:_country)?$")
# A saved value that names a status, a yes/no or a document, not a country.
NOT_A_COUNTRY = re.compile(
    r"citizen|\bnational\b|resident|visa|status|permit|holder|green ?card|\bopt\b|h-?1b|\d|^(?:yes|no|true|false|n/?a|none)$",
    re.I,
)


class Resolver(
    DemographicRulesMixin,
    HistoryRulesMixin,
    ConsentRulesMixin,
    PolicyRulesMixin,
    PeopleRulesMixin,
    IntentRulesMixin,
    ModelBatchMixin,
    ValidateMixin,
    LocalRulesMixin,
):
    def __init__(
        self,
        profile: Profile,
        db,
        config,
        run_id=None,
        employer="",
        job=None,
        resume_text="",
        kb=None,
        resume_artifact_id=None,
    ):
        self.profile, self.db, self.config = profile, db, config
        self.run_id, self.employer = run_id, employer
        self.resume_text, self.job = resume_text, job or {}
        self.kb = kb
        self.answer_cache = {}
        self.resume_artifact_id = resume_artifact_id
        self.context_packets = {}
        self.context_blockers = {}
        self.context_labels = {}
        # Field id -> the closest offered options the answer model may choose from (rules "pick3").
        self.narrowed = {}
        # Source channels ("social media") whose follow-up question offered no truthful option on this application: the
        # parent menu is answered again without them (answers/sources.py).
        self.dead_sources = set()

    @property
    def answer_rules(self):
        if not hasattr(self, "_answer_rules"):
            from ..answer_rules import rules_for

            self._answer_rules = rules_for(self.profile)
        return self._answer_rules

    def question_context(self, field):
        field = {**field, "label": screening_text(field)}
        from ..features.knowledge import Budget, retrieve_answer_context
        from ..features.knowledge.migration import row_id

        history = None
        group = " ".join(str(field.get(k, "")) for k in ("group", "section", "context"))
        match = re.search(r"\b(work(?: experience)?|education)\s*(\d+)\b", group, re.I)
        if match:
            rows = self.profile.education if match[1].lower() == "education" else self.profile.work
            index = int(match[2]) - 1
            if 0 <= index < len(rows):
                education = match[1].lower() == "education"
                history = row_id(
                    "edu" if education else "exp",
                    rows[index].model_dump(),
                    ("school", "degree", "field_of_study", "start")
                    if education
                    else ("company", "title", "start"),
                )
        scoped_label = field["label"]
        # A field's "context" is a dict of its group's other values (forms.py). It describes the page, not this
        # question: iCIMS keeps a whole profile in one group, so "Phone Country Code: (+1) United States" scoped
        # Email, City and Address as phone questions, and each was answered with the address country (2026-10-21).
        context = field.get("context")
        scope = " ".join(
            [str(field.get(k, "")) for k in ("section", "group", "hint")] + ([context] if isinstance(context, str) else [])
        )
        if normalize(field["label"]) in {
            "name",
            "country",
            "country region",
            "phone",
            "email",
            "city",
            "state",
            "address",
        } and re.search(
            r"citizen|nationality|birth|education|school|university|work(?: history| experience)|employer|phone",
            scope,
            re.I,
        ):
            scoped_label += " (" + scope[:300] + ")"
        self.context_labels[field["id"]] = scoped_label
        packet = retrieve_answer_context(
            self.db,
            scoped_label,
            field.get("options", []),
            self.employer,
            self.job.get("url", ""),
            history_row=history,
            resume_artifact_id=self.resume_artifact_id,
            budget=Budget(max_items=10, max_bytes=4000),
        )
        from ..execution_services import authoritative_packet

        packet, blocker = authoritative_packet(self.db, packet, field.get("options", []))
        self.context_blockers[field["id"]] = blocker
        self.context_packets[field["id"]] = packet
        return packet

    def structured_answer(self, field, packet):
        # A generic remembered "Degree" or "Field of Study" cannot answer a
        # numbered history row. The row's saved facts precede old question memory.
        row = " ".join(str(field.get(k) or "") for k in ("group", "section"))
        if HISTORY_GROUP.search(row):
            patterns = EDUCATION if re.match(r"\s*education\s+at\b|.*\beducation\s*\d+", row, re.I) else WORK
            if any(pattern.search(field["label"]) for pattern in patterns.values()):
                return None
        if self.context_blockers.get(field["id"]):
            reason = self.context_blockers[field["id"]]
            return self._invalid(
                field, reason, "model_output" if "retrieval budget" in reason else "missing_fact"
            )
        if packet.conflicts and demographic_topic(field["label"]):
            # Owner demographic policy outranks old answers that disagree (Roblox race picker
            # 2026-10-14): decline when offered, otherwise the confirmed race preference.
            options = [o for o in field.get("options") or [] if str(o).strip()]
            # Multi-select pickers drop chosen options from the list; the selection still counts.
            options += [
                v.strip()
                for v in re.split(r"[\n;,]", str(field.get("value") or ""))
                if v.strip() and v.strip() not in options
            ]
            decline = next((o for o in options if DECLINE.search(o)), None)
            if decline and self.demographic_rule(field).decline_first and field.get("type") != "checkbox":
                return Answer(
                    field_id=field["id"],
                    value=decline,
                    disposition="answer",
                    evidence_ids=["policy:demographics"],
                    reason="Decline disclosure policy",
                )
            answer = self.confirmed_demographic({**field, "options": options}, options) if options else None
            if answer and answer.evidence_ids == ["fact:race_preference"]:
                return answer
        if packet.conflicts:
            from ..evidence_equivalence import cached_answer

            if answer := cached_answer(self, field, packet):
                return answer
            return self._invalid(field, "Confirmed answers conflict; clarification required", "missing_fact")
        # Rolling dates/notice periods are derived at application time, not
        # reused from an earlier dated application.
        if TODAY.search(field["label"]) or "notice period" in field["label"].casefold():
            return None
        from .validate import BARE_HISTORY_DATE

        if BARE_HISTORY_DATE.fullmatch(field["label"]):
            return None  # A saved "End Date" answered every history row (validate.BARE_HISTORY_DATE).
        if packet.exact_answer is None:
            return None
        refs = [
            "knowledge:" + item.fact_id
            for item in packet.items
            if item.kind == "fact" and item.match == "exact"
        ]
        if not refs:
            return None
        value = packet.exact_option if packet.exact_option is not None else packet.exact_answer
        if isinstance(value, bool):
            value = ("true" if value else "false") if field["type"] == "checkbox" else yes_no(value)
        if not isinstance(value, (str, int, float)):
            return None
        from ..email_alias import same_mailbox

        own = str(self.profile.email or "").strip()
        if (
            "@" in str(value)
            and own
            and own.lower() != str(value).strip().lower()
            and same_mailbox(own, str(value))
        ):
            # The run applies with a platform plus-address (iCIMS, see email_alias); the knowledge fact holds the base
            # mailbox. 0 of 17 iCIMS Email answers carried the alias on 2026-10-18 (REA map, icims.md risk 1).
            return self._ans(field, own, ["profile:email"], "The run's platform address for this mailbox")
        return self._ans(field, str(value), refs, "Confirmed scoped knowledge")

    def cache_key(self, field):
        # Cache validated reasoning within this application only. Profile/KB,
        # row context and real options changes all invalidate it. Dynamic dates
        # still go through local() first.
        context = {
            "version": CACHE_VERSION,
            "profile_version": digest(self.profile.model_dump()),
            "knowledge_version": digest([a.model_dump() for a in self.kb.entries]) if self.kb else "",
            "question": {
                k: field.get(k)
                for k in (
                    "label",
                    "type",
                    "required",
                    "options",
                    "options_partial",
                    "group",
                    "section",
                    "slot",
                    "context",
                    "hint",
                    "related_question",
                    "maxlength",
                    "value",
                )
            },
            "employer": self.employer,
            "job_version": digest(self.job),
            "resume_artifact_id": self.resume_artifact_id,
            "context_version": (
                self.context_packets[field["id"]].store_version
                if field["id"] in self.context_packets
                else None
            ),
        }
        return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()

    def _facts(self):
        return {k.lower(): v for k, v in self.profile.facts.items()}

    def _ans(self, f, value, evidence, reason=""):
        from ..widgets import placeholder_option

        value = strip_fact_annotations(value)

        if placeholder_option(value):
            return None
        if f.get("type") == "number" and not re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)", str(value).strip()):
            return None  # Interpret formatted amounts/preferences before sending to a numeric widget.
        if f.get("type") == "checkbox":
            from ..widgets import checkbox_value

            try:
                return Answer(
                    field_id=f["id"],
                    value=checkbox_value(value),
                    disposition="answer",
                    evidence_ids=evidence,
                    supporting_facts=evidence,
                    reason=reason,
                )
            except ValueError:
                return None
        if f.get("type") in {"date", "date_parts", "month"} or f.get("widget") == "date-text":
            from ..widgets import date_text, parse_date

            try:
                if f.get("type") == "date_parts" or f.get("widget") == "date-text":
                    date_text(f, value)
                else:
                    parse_date(value)
            except ValueError:
                # A related Boolean/status fact is evidence for interpretation,
                # not a date. Let the existing answer batch infer the date.
                return None
        options = f.get("options", [])
        if f.get("options_partial"):
            matched = option_value(value, options) if options else None
            matched = matched or str(value)
        else:
            matched = option_value(value, options)
        if matched is None or matched == "":
            return None
        return Answer(
            field_id=f["id"],
            value=matched,
            disposition="answer",
            evidence_ids=evidence,
            supporting_facts=evidence,
            reason=reason,
        )

    def knows(self, f):
        try:
            a = self.local(f)
        except Exception:
            return False
        return bool(a and a.disposition == "answer")

    def located_in_answer(self, f):
        """Options "I am located in <State>, I acknowledge" / "I am not located in <State>":
        a residence question (TransUnion's Massachusetts notice), answered from the address."""
        from .eligibility import US_STATES

        options = f.get("options", [])
        state = self.profile.address.state
        if not options or not state:
            return None

        def parse(option):
            n = normalize(option)
            m = (
                re.match(
                    r"(?:i am |i m )?(not )?(?:located|residing|living|based) in ([a-z ]+?)(?: i acknowledge.*)?$",
                    n,
                )
                # Some notices say "reside" rather than "am located". The trailing claim only
                # acknowledges receipt of this notice; unrelated assertions are not residence evidence.
                or re.fullmatch(
                    r"i (?:(do not|don t) )?reside in ([a-z ]+?)"
                    r"(?: and acknowledge that i have received notice of this (?:statement|statute)"
                    r"(?: pursuant to (?:[a-z]{1,3} )*\d+[a-z]*(?: \d+[a-z]*)*)?)?",
                    n,
                )
                # "Not a resident of Maine - does not apply" / "Maine - Acknowledgment of Receipt ..."
                or re.match(
                    r"(?:i am |i m )?(not )?(?:a )?resident of ([a-z ]+?)(?: does not apply.*| i acknowledge.*)?$",
                    n,
                )
                or re.match(r"()([a-z ]+?) acknowledg(?:e)?ment of receipt\b.*", n)
            )
            return m

        states = {normalize(name): code for code, name in US_STATES.items()}
        states.update({code.lower(): code for code in US_STATES})
        home = states.get(normalize(state))
        parsed = [parse(o) for o in options]
        if not home or not all(m and m.group(2).strip() in states for m in parsed):
            return None
        chosen = [
            o
            for o, m in zip(options, parsed, strict=True)
            if (not m.group(1)) == (states[m.group(2).strip()] == home)
        ]
        if len(chosen) != 1:
            return None
        return self._ans(f, chosen[0], ["profile:address"], "Residence state from the applicant's address")

    def language_answer(self, f):
        """A history row's "Language" picker (Workday My Experience > Languages), answered from
        the applicant's confirmed languages: row n takes the n-th language."""
        if normalize(f.get("label", "")) != "language" or f.get("type") not in {
            "dropdown",
            "combobox",
            "select",
            "prompt",
            "text",
        }:
            return None
        # Profiles store confirmed languages as Profile.languages; older/real profiles
        # keep them as facts (languages list, or language_proficiencies keyed by language).
        languages, source = self.profile.languages, "profile:languages"
        if not languages:
            languages, source = self.profile.facts.get("languages"), "fact:languages"
        proficiencies = self.profile.facts.get("language_proficiencies")
        if not languages and isinstance(proficiencies, dict):
            languages, source = list(proficiencies), "fact:language_proficiencies"
        if isinstance(languages, str):
            languages = [x.strip() for x in re.split(r"[,;/]", languages) if x.strip()]
        if not isinstance(languages, list) or not languages:
            return None
        row = re.search(r"language\w*\s*(\d+)", f"{f.get('group') or ''} {f.get('section') or ''}", re.I)
        index = int(row[1]) - 1 if row else 0
        if not 0 <= index < len(languages):
            return None
        options = [o for o in f.get("options", []) if str(o).strip()]
        value = option_value(languages[index], options) if options else str(languages[index])
        if not value:
            return None
        return self._ans(f, value, [source], "Applicant's confirmed language")

    def repair_override(self, f):
        """The repair agent's checked correction for this job's field (repair_agent.py), if any."""
        job_id = (self.job or {}).get("id")
        if not job_id or self.db is None:
            return None
        if not hasattr(self, "_repair_overrides"):
            self._repair_overrides = self.db.get_setting("repair_overrides:" + job_id, {}) or {}
        fix = self._repair_overrides.get(normalize(re.sub(r"[\s.*:]+$", "", f.get("label", ""))))
        if not fix:
            return None
        from .validate import BARE_HISTORY_DATE

        if BARE_HISTORY_DATE.fullmatch(f.get("label", "")):
            return None  # One row's corrected "From Date" is not every row's (Supermicro 2026-10-27).
        if f.get("history_kind") or HISTORY_ROW.match(f.get("group") or ""):
            # A correction is saved by label and names no row. Lennar's (Workday, 2026-10-21) "School or University"
            # fix for the first education row answered the second as well: four applications listed the master's school
            # for the bachelor's degree. History rows are answered from the profile's own rows.
            return None
        from ..repair_agent import MASKED_VALUE, YES_NO

        if MASKED_VALUE.search(str(fix.get("value", ""))):
            return None  # Saved before the repair agent refused masked placeholders ("[email]", iPipeline 2026-10-19).
        if YES_NO.fullmatch(str(fix.get("value", "")).strip()):
            # Saved before the repair agent refused bare yes/no fixes: "Yes" to "Are you, or have you ever been, a
            # contractor for the Federal Reserve System?" traced only to some other fact's "Yes" (2026-10-26).
            return None
        from ..widgets import placeholder_option

        options = [o for o in f.get("options", []) if not placeholder_option(o)]
        value = option_value(fix["value"], options) if options else fix["value"]
        if value is None:
            return None
        return Answer(
            field_id=f["id"],
            value=value,
            disposition="answer",
            evidence_ids=["repair:" + str(fix.get("run_id", ""))],
            reason="Repair agent correction",
        )

    def named_employer_date(self, f):
        """A start or end date asked of an employer named in the question ("What was your end date at ExampleSoft?"):
        that work row's own date. Leonardo DRS (2026-10-27) asked the owner although the profile holds it."""
        if f.get("options"):
            return None
        match = re.search(r"\b(start|end|starting|ending|from|to)\s+date\b.{0,30}?\b(?:at|with|for)\s+(.+?)\s*\??$",
                          str(f.get("label", "")), re.I)
        if not match:
            return None
        named = normalize(match[2])
        rows = [i for i, w in enumerate(self.profile.work) if w.company and normalize(w.company) == named]
        if len(rows) != 1:
            return None
        row = self.profile.work[rows[0]]
        value = row.start if match[1].lower() in {"start", "starting", "from"} else row.end
        if not value or (match[1].lower() not in {"start", "starting", "from"} and row.current):
            return None
        return self._ans(f, value, [f"profile:work.{rows[0]}"], "The named employer's row in the work history")

    def citizenship_obtained_date(self, f):
        """The date a citizenship was obtained, for an applicant whose recorded citizenship was acquired at birth and
        who records no other status (permanent residency, naturalization): the date of birth. Westinghouse's
        SuccessFactors citizenship table (2026-10-27) asked the owner for "Date Obtained 1" three times."""
        label = str(f.get("label") or "")
        if (
            f.get("options")
            or not re.search(r"\b(?:citizenship|nationality)\b", label, re.I)
            or not re.search(r"\bdate\b.{0,40}\b(?:obtained|acquired|granted)\b|\b(?:obtained|acquired)\b.{0,40}\bdate\b",
                             label, re.I)
            or re.search(r"\b(?:[2-9]|second|third)\b", label, re.I)  # A further row holds another status.
        ):
            return None
        facts = self.profile.facts
        births = [
            k for k, v in facts.items()
            if (re.search(r"(?:citizenship|nationality)_acquired$", k, re.I)
                and re.search(r"\b(?:at|by|since|from) birth\b|\bbirthright\b", str(v), re.I))
            or (re.search(r"(?:citizenship|nationality)_acquired_at_birth$", k, re.I) and v is True)
        ]
        other = any(
            re.search(r"permanent_resid|green_card|naturali[sz]|refugee|asylee", k, re.I)
            and (v is True or str(v).strip().casefold() in {"true", "yes"})
            for k, v in facts.items()
        )
        dob = next((k for k in facts if k.casefold() in {"date_of_birth", "dob", "birth_date"} and facts[k]), None)
        if not births or other or not dob:
            return None
        return self._ans(
            f, str(facts[dob]), [f"fact:{births[0]}", f"fact:{dob}"], "Citizenship acquired at birth: the date of birth"
        )

    def citizenship_country_answer(self, f):
        """A question for the applicant's country of citizenship, answered from the saved citizenship facts.

        Apptronik (Greenhouse, 2026-10-30, nine runs): "a. If NO, please indicate your country of citizenship and, if
        different, the country in which you have most recently obtained citizenship or permanent residency status. This
        information will be used to determine whether ... a license from the U.S. government" was classed as a
        government disclosure; the model's correct answer failed the scope check on every run and the repair agent
        stopped on "identical failure repeated". The saved facts state it, so no model or scope check is involved. A
        further country is given only when the question asks for one and the profile holds it; otherwise the one
        country is the full answer.
        """
        label = str(f.get("label") or "")
        options = [str(o) for o in f.get("options") or [] if str(o).strip()]
        if (
            f.get("type") == "checkbox"
            or not CITIZENSHIP_COUNTRY_ASK.search(label)
            or (options and all(re.match(r"\s*(?:yes|no)\b", o, re.I) for o in options))
        ):
            return None
        facts = self._facts()

        def country(key):
            value = facts.get(key)
            text = value.strip() if isinstance(value, str) else ""
            return text if text and len(text) <= 60 and not NOT_A_COUNTRY.search(text) else ""

        if (
            re.match(r"\s*(?:\(?[a-z0-9]\)?\W+)?if (?:no|not)\b", label, re.I)
            and re.search(r"\b(?:enter|write|put|type|answer|indicate)\b[^.]{0,20}\bn/?a\b", label, re.I)
            and boolean(facts.get("us_citizen")) is True
        ):
            # "(Enter "N/A" if marked Yes above)": a U.S. citizen's follow-up for those who are not.
            if answer := self._ans(f, "N/A", ["fact:us_citizen"], "The condition for naming a country does not apply"):
                return answer
        first = next((k for k in CITIZENSHIP_COUNTRY_KEYS if country(k)), None)
        if first is None:
            return None
        named, evidence = [country(first)], [f"fact:{first}"]
        if not options and CITIZENSHIP_MORE_ASK.search(label):
            residency = bool(re.search(r"permanent resid", label, re.I))
            for key in facts:
                if (
                    (OTHER_CITIZENSHIP_KEY.match(key) or (residency and RESIDENCY_COUNTRY_KEY.match(key)))
                    and country(key)
                    and normalize(country(key)) not in {normalize(c) for c in named}
                ):
                    named.append(country(key))
                    evidence.append(f"fact:{key}")
        return self._ans(f, "; ".join(named), evidence, "Country of citizenship from the saved citizenship facts")

    def local(self, f):
        from .third_party import guard

        if answer := guard(self, f):
            return answer  # An employer's, supervisor's or reference's contact detail is never the applicant's own.
        if answer := self.repair_override(f):
            return answer
        if answer := self.signature_block(f):
            return answer
        if answer := self.initials_block(f):
            return answer
        if answer := self.talent_pool_opt_out(f):
            return answer
        if f.get("options") and re.search(
            r"profile data|personal data|data across|my data",
            f.get("label", "") + " " + " ".join(map(str, f.get("options") or [])),
            re.I,
        ):
            if picked := self._policy_ans(f, ["policy:marketing"]):
                return picked  # Narrowest data-sharing choice (Qualcomm 2026-10-14).
        if answer := self.required_contact_consent(f):
            return answer
        if f.get("type") == "checkbox" and not f.get("required") and SELECT_ALL.match(f.get("label", "")):
            # An optional "Select all" also agrees to optional marketing (AbbVie SMS/WhatsApp,
            # 2026-10-14); each box below it is decided on its own consent/marketing policy.
            return Answer(
                field_id=f["id"],
                value="false",
                disposition="answer",
                evidence_ids=["policy:marketing"],
                reason="Optional select-all left unchecked",
            )
        f = {**f, "label": screening_text(f)}
        if answer := self.demographic_decline(f):
            return answer
        # Supervisors and references the applicant saved in Knowledge; nothing saved means the question stays open.
        if answer := self.people_answer(f):
            return answer
        # A question that defines its own answer for the applicant's recorded status is the most specific signal.
        if answer := self.status_answer(f):
            return answer
        if answer := self.owner_policy_answer(f):
            return answer
        if answer := self.us_person_answer(f):
            return answer
        if answer := self.check_consent_answer(f):
            return answer
        if answer := self.located_in_answer(f):
            return answer
        if answer := self.language_answer(f):
            return answer
        if answer := self.named_employer_date(f):
            return answer
        if answer := self.citizenship_obtained_date(f):
            return answer
        if answer := self.citizenship_country_answer(f):
            return answer
        if f.get("type") == "checkbox":
            # A single checkbox's caption is a proposition, not an offered
            # answer value. Current consent/fact policy precedes old caption
            # memory and always returns an explicit checked state.
            if answer := self._local(f):
                if answer.disposition != "answer" or answer.value in {"true", "false"}:
                    return answer
        if (
            normalize(f["label"]) == "country"
            and f.get("type") == "combobox"
            and re.fullmatch(r"phone|telephone|mobile(?: number)?", f.get("section", ""), re.I)
        ):
            # Greenhouse labels its calling-code picker "Country" within
            # Phone. Scoped retrieval must not turn that into an unknown
            # "Country Phone" personal fact or lose the supplied residence.
            return self._local({**f, "label": "Country phone code"})
        from ..history_editor_guard import history_answer

        if answer := history_answer(self, f):
            return answer
        if topic(f["label"]) == "source" and self.profile.application_source:
            # The applicant's current source preference precedes old structured
            # answers as well as legacy exact-answer memory.
            return self._local(f)
        packet = self.question_context(f)
        row = " ".join(str(f.get(k) or "") for k in ("group", "section"))
        if HISTORY_GROUP.search(row):
            patterns = EDUCATION if re.match(r"\s*education\s+at\b|.*\beducation\s*\d+", row, re.I) else WORK
            if (
                any(pattern.search(f["label"]) for pattern in patterns.values())
                # The row's own location (or nothing) answers its address/region boxes, never retrieval guesses.
                or HISTORY_CONTACT.search(f["label"])
                or HISTORY_REGION.fullmatch(normalize(f["label"]))
            ):
                return self._local(f)
        if answer := self.complete_history_answer(f, packet):
            return answer
        answer = self.structured_answer(f, packet)
        if answer is not None:
            return answer
        if defined_screening(f["label"]):
            # The employer's definition names the applicant's recorded status, or a model reasons over it.
            return self.status_answer(f)
        return self._local({**f, "label": self.context_labels[f["id"]], "own_label": f["label"]})

    async def resolve(self, fields, *, use_local=True):
        from ..agent_settings import require

        require(self.db, "answers")
        answers = await self._resolve(fields, use_local=use_local)
        labels = {f["id"]: f["label"] for f in fields}
        by_id = {f["id"]: f for f in fields}
        await self.infer_before_asking(answers, by_id)
        for answer in answers:
            field = by_id.get(answer.field_id, {})
            if answer.disposition == "answer" and field.get("type") == "checkbox":
                from ..widgets import checkbox_value

                try:
                    answer.value = checkbox_value(answer.value)
                except ValueError:
                    answer.disposition = "review" if field.get("required") else "skip"
                    answer.blocker_type = "model_output"
                    answer.reason = "Checkbox answer must specify checked or unchecked"
                    answer.retryable = True
            if answer.disposition == "answer" and answer.value:
                answer.value = self.platform_address(answer.value)
                answer.value = display_name_case(labels.get(answer.field_id, ""), answer.value)
        return answers

    def platform_address(self, value):
        """The run's platform plus-address (email_alias) in place of its base mailbox, whichever source answered.

        ADP (Avalon, 2026-10-19): the Email fields came from knowledge and from saved confirmed answers, both holding
        the base mailbox, so registration hit the old identity (40014) although the run applied with +adp."""
        from ..email_alias import same_mailbox

        own, text = str(self.profile.email or "").strip(), str(value).strip()
        if (
            own
            and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text)
            and text.lower() != own.lower()
            and same_mailbox(text, own)
        ):
            return own
        return value

    def answer_sources(self):
        """Facts, evidence and applicable learned answers every model answer is validated against."""
        facts = {"profile:" + k: v for k, v in self.profile.model_dump().items() if k in CONTACT and v}
        for key in ("phone_country_code", "phone_type"):
            if value := getattr(self.profile, key, ""):
                facts["profile:" + key] = value
        facts.update({"fact:" + k: v for k, v in self.profile.facts.items()})
        for i, w in enumerate(self.profile.work):
            facts[f"profile:work.{i}"] = w.model_dump()
        from ..features.knowledge import employment_history_complete

        if employment_history_complete(self.db):
            from ..answer_context import complete_employment_context

            facts["fact:employment_history_complete"] = complete_employment_context(self.profile)
        for i, e in enumerate(self.profile.education):
            facts[f"profile:education.{i}"] = e.model_dump()
        if self.profile.skills:
            facts["profile:skills"] = self.profile.skills
        if any(getattr(self.profile.address, k) for k in ("city", "state", "country")):
            facts["profile:address"] = self.profile.address.model_dump()
        if self.profile.accept_all_application_terms:
            facts["fact:application_consent_policy"] = (
                "Applicant accepts all application terms, conditions, privacy notices, and consent requests."
            )
        if self.profile.accept_required_marketing:
            facts["fact:required_marketing_policy"] = (
                "Accept marketing only if required to complete the application."
            )
        if self.profile.application_source:
            facts["fact:application_source_policy"] = self.profile.application_source
        facts.update(delegated_facts(self.answer_rules, self.profile.autonomous, self.profile.work))
        evidence = {"evidence:" + e.id: e.text for e in self.profile.evidence}
        if self.resume_text:
            evidence["resume:selected"] = self.resume_text[:12000]
        learned = {
            "reviewed:" + a.id: a for a in self.profile.reviewed_answers if applicable(a, self.employer)
        }
        return facts, evidence, learned
