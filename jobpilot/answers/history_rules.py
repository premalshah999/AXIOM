"""Resolver methods: history_rules."""

from datetime import datetime
from zoneinfo import ZoneInfo
import re
from ..knowledge import topic
from ..schemas import Answer
from .history import EDUCATION
from .history import HISTORY_CLASS
from .history import HISTORY_CONTACT
from .history import HISTORY_PERIOD
from .history import HISTORY_REGION
from .history import HISTORY_UNKNOWN
from .history import WORK
from .history import degree_level
from .history import history_region
from .history import month_year
from .text import normalize



# Degree types that are never another degree's equivalent (owner rule 2026-10-13: never Arts or MBA for a science or
# engineering degree). Text similarity chose "MBA" for "Master of Science" (EPIC, 2026-10-25).
DISTINCT_DEGREE = re.compile(r"\b(?:mba|business administration|arts?|fine|music|laws?|jd|juris|education|nursing|"
                             r"medicine|md|phd|doctor\w*|associate\w*|high school|ged)\b", re.I)


def same_degree_kind(field, value, pick):
    """A degree question's fuzzy pick must be the recorded degree's level and must not name a distinct degree type the
    record does not. Other questions are unaffected."""
    if not re.search(r"\bdegree\b|education level|level of education", str(field.get("label", "")), re.I):
        return True
    if degree_level(pick) != degree_level(value):
        return False
    return not (DISTINCT_DEGREE.search(normalize(pick)) and not DISTINCT_DEGREE.search(normalize(value)))

class HistoryRulesMixin:
    def reason_for_leaving(self, f):
        """(answer, evidence) for the role a reason-for-leaving question is about: its work row, else the latest."""
        rows = list(self.profile.work or [])
        if not rows:
            return None
        group = " ".join(str(f.get(k, "")) for k in ("group", "section", "context"))
        match = re.search(r"\b(?:work(?: experience)?|employment|employer|experience)\s*(\d+)\b", group, re.I)
        index = int(match[1]) - 1 if match else None
        if index is not None and not 0 <= index < len(rows):
            return None
        row = (
            rows[index]
            if index is not None
            else max(rows, key=lambda w: (w.current or not w.end, w.start or ""))
        )
        evidence = f"profile:work.{rows.index(row)}"
        if row.current or not row.end:
            return (
                "I am still in this role and am looking for a full-time position with more room to grow.",
                evidence,
            )
        try:
            ended = datetime.strptime(row.end[:7], "%Y-%m").strftime("%B %Y")
        except ValueError:
            ended = row.end[:4]
        kind = "internship" if re.search(r"\bintern(?:ship)?\b", row.title or "", re.I) else "role"
        return (
            f"The {kind} ended in {ended}, and I am looking for a full-time position with more room to grow.",
            evidence,
        )

    def education_fallback(self, f, value, evidence):
        """The owner's chain when a recorded degree, major or school is not offered:
        closest option, the answer model choosing among the 3 closest, Other, or ask."""
        from ..answer_rules import closest_option, other_option, top_options

        options = [o for o in f.get("options") or [] if str(o).strip()]
        if not options or f.get("options_partial") or not value:
            return None
        for step in self.answer_rules.education:
            if step == "closest":
                pick = self.policy_choice(f, options) or closest_option(value, options)
                if pick and not same_degree_kind(f, value, pick):
                    pick = None
                if pick and (a := self._ans(f, pick, evidence, f"Closest offered option to {value}")):
                    return a
            elif step == "pick3":
                top = top_options(value, options, floor=0.4)
                if len(top) >= 2:
                    self.narrowed[f["id"]] = top
                    return None  # The answer model chooses among these; its value is still validated.
            elif step == "other":
                other = other_option(options)
                if other and (a := self._ans(f, other, evidence, f"{value} is not offered; chose Other")):
                    return a
            elif step == "ask":
                return Answer(
                    field_id=f["id"],
                    disposition="review" if f.get("required") else "skip",
                    reason=f"{value} is not among the offered options",
                    blocker_type="missing_fact",
                )
        return None

    def graduation_parts(self, f, end, unit):
        """({"year", "month", "day"}, evidence) for a graduation date asked in parts, or None when the date cannot be
        given whole. A month-precision profile date takes its day from the owner's reviewed answer naming the same
        month and year ("21st May 2026"); else, only where the date is required, the first of the month, as iCIMS's
        own resume parser fills its rows (see history dates below). An optional date stays blank rather than partial."""
        from ..widgets import parse_date

        try:
            month, day, year = parse_date(end)
        except (ValueError, TypeError):
            return None
        evidence = ["profile:education.0.end"]
        if not (month and year):
            return None
        if not day and "Day" in (f.get("date_parts") or [unit.title()]):
            for review in reversed(self.profile.reviewed_answers):
                if review.scope != "personal" or not re.search(r"graduat", review.question, re.I):
                    continue
                try:
                    m, d, y = parse_date(review.answer)
                except (ValueError, TypeError):
                    continue
                if d and (m, y) == (month, year):
                    day = d
                    evidence.append("reviewed:" + review.id)
                    break
            else:
                if not f.get("required"):
                    return None
                day = "01"
        return {"year": year, "month": month, "day": day}, evidence

    def graduation_choice_valid(self, field, value):
        """Interpret dated graduation categories, rather than typing a date into a menu."""
        if not self.profile.education or not re.search(r"graduat", field["label"], re.I):
            return None
        from ..widgets import parse_date

        try:
            month, day, year = parse_date(self.profile.education[0].end)
            known = (int(year), int(month))
            today = datetime.now(ZoneInfo(self.config.timezone)).date()
            completed = known < (today.year, today.month) or bool(
                day and datetime(int(year), int(month), int(day)).date() <= today
            )
            label = normalize(str(value))
            # normalize() turns the contraction into "i ve": DV Trading's Greenhouse "I've already graduated" read as no
            # category, so a date was typed into its menu on every run (2026-10-29).
            if re.fullmatch(r"(?:i ve |i have |have |i )?(?:already )?graduated", label):
                return True if completed else False if known > (today.year, today.month) else None
            if completed and re.search(
                r"\b(?:i graduate after|i (?:will|ll) graduate|i expect to graduate)\b", label
            ):
                return False
            bounds = re.split(r"\s+(?:-|–|to|through)\s+", str(value), maxsplit=1)
            if len(bounds) == 2:
                left, right = parse_date(bounds[0]), parse_date(bounds[1])
                return (int(left[2]), int(left[0])) <= known <= (int(right[2]), int(right[0]))
        except (TypeError, ValueError):
            pass
        return None

    def complete_history_answer(self, field, packet):
        """Applicant-confirmed absence or complete history supports scoped No."""
        from ..features.knowledge import employment_history_complete, resolve_employer
        from ..features.knowledge.employers import mention_aliases

        label = field["label"]
        proposition = label.partition("?")[0]
        if topic(label) != "prior_employment" or re.search(
            r"\b(?:clients?|member sites?|related|relatives?|family|spouse|referr\w*|never|not)\b",
            proposition,
            re.I,
        ):
            return None
        attested = employment_history_complete(self.db)
        explicit_no = self.profile.facts.get("previously_employed_at_applying_company") is False
        employer = packet.employer
        if not employer and self.employer and self.job.get("url"):
            employer = resolve_employer(self.db, self.employer, self.job["url"])
        if not (attested or explicit_no) or not employer:
            return None
        aliases = set(mention_aliases([self.employer or "", employer.display, *employer.aliases]))
        if not aliases or not (
            "<employer>" in packet.question.text
            or re.search(r"\b(?:this|our) (?:company|organi[sz]ation|employer)\b", label, re.I)
        ):
            return None
        for row in self.profile.work:
            # Broad on both sides: any overlap with a listed employer withholds the No.
            names = mention_aliases(row.company)
            if aliases.intersection(names) or any(
                re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", label, re.I) for name in names
            ):
                return None
        evidence = (
            ["fact:previously_employed_at_applying_company"]
            if explicit_no
            else [attested["reference"], *[f"profile:work.{i}" for i in range(len(self.profile.work))]]
        )
        reason = (
            "Applicant explicitly confirmed No to prior employment at the company being applied to"
            if explicit_no
            else "Applicant confirmed that all past employment is listed; this employer is absent"
        )
        if field.get("type") in {"text", "textarea", "date", "date_parts", "month"}:
            # No previous employment is not a company name, employment type or
            # last-worked date. Optional conditional details remain empty.
            if re.match(r"if\b", label, re.I) and not field.get("required"):
                return Answer(
                    field_id=field["id"],
                    disposition="skip",
                    reason=reason,
                    evidence_ids=evidence,
                    retryable=False,
                )
            return None
        if answer := self._ans(field, "No", evidence, reason):
            return answer
        # Options phrased as statements: "I have not previously been employed at Affirm"
        # alongside "I have been employed at Affirm as an intern", ... Exactly one
        # offered option may negate employment; anything else stays unanswered.
        negations = [
            option
            for option in field.get("options", [])
            if re.search(r"\b(?:not|never|no)\b", option, re.I) and re.search(r"employ|work", option, re.I)
        ]
        if len(negations) == 1:
            return self._ans(field, negations[0], evidence, reason)
        # Home Depot: "Current Associate ...", "Former Associate ...", "Not Applicable".
        # When every other option asserts employment, the single N/A is the No.
        options = [o for o in field.get("options", []) if normalize(o)]
        absent = [
            o for o in options if re.fullmatch(r"not applicable|n ?a|none(?: of the above)?", normalize(o))
        ]
        if len(absent) == 1 and all(
            re.search(
                r"\b(?:current|former|previous|past|associate|employee|employed|worked|intern)\b", o, re.I
            )
            for o in options
            if o != absent[0]
        ):
            return self._ans(field, absent[0], evidence, reason)
        return None

    def school_answer(self, field, school, evidence):
        from ..widgets import school_option_matches, school_other_option

        if self.profile.facts.get("school_catalog_other_first") is True and field.get("type") in {
            "combobox",
            "prompt",
            "select",
            "dropdown",
            "pills",
            "radiogroup",
        }:
            other = [o for o in field.get("options", []) if school_other_option(o)]
            policy_evidence = [*evidence, "fact:school_catalog_other_first"]
            if len(other) == 1:
                return self._ans(
                    field, other[0], policy_evidence, "Applicant requested Other for institution catalogs"
                )
            # No offered Other (Roblox, Superhuman list the real campus): the actual
            # school is the truthful answer; refusing parked the whole application.
        selected = field.get("value", "")
        if selected and not school_other_option(selected) and school_option_matches(school, selected):
            return self._ans({**field, "options": [], "options_partial": True}, selected, evidence)

        # Partial catalogs search the actual school before looking for Other.
        if field.get("options_partial") or not field.get("options"):
            return self._ans(field, school, evidence)
        options = field["options"]
        actual = [o for o in options if not school_other_option(o) and school_option_matches(school, o)]
        if len(actual) == 1:
            return self._ans(field, actual[0], evidence)
        fallback = self.education_fallback(field, school, evidence)
        if fallback is not None or field["id"] in self.narrowed:
            return fallback  # The owner's chain decided, or the answer model chooses among the closest.
        other = [o for o in options if school_other_option(o)]
        if len(other) == 1:
            return self._ans(
                field, other[0], evidence, "Applicant prefers Other when the actual school is unavailable"
            )
        return Answer(
            field_id=field["id"],
            disposition="review" if field.get("required") else "skip",
            reason="Actual school and Other are absent from the offered catalog",
            blocker_type="control_failure",
            retryable=False,
        )

    def _single_entry(self, f, label):
        """Forms without repeating rows ask about the most recent school/job directly."""
        p = self.profile
        if p.education and re.search(
            r"(?:which|what) (?:school|university|college).{0,120}(?:currently attending|last attend|most recently attend)",
            label,
        ):
            index = max(range(len(p.education)), key=lambda i: p.education[i].end or "")
            school = p.education[index].school
            return self.school_answer(f, school, [f"profile:education.{index}.school"])
        if p.education and re.search(
            # Axtria (JazzHR, 2026-10-23): "Completion date of your highest degree (mm/yyyy)" went to the owner.
            r"graduation date|anticipated graduation|graduation (?:month|year)|"
            r"completion date of (?:your )?(?:highest )?degree|degree completion date|date of degree completion",
            label,
        ):
            if any(re.match(r"^(?:yes|no)\b", o, re.I) for o in f.get("options", [])):
                # A dated eligibility proposition needs interpretation. Never
                # type the graduation date into its Yes/No dropdown, including
                # when that dropdown happens to have an editable search input.
                return None
            entry = p.education[0]
            if "highest" in label:
                # "Completion date of your highest degree": the highest-ranked degree, wherever the profile lists it.
                ranks = ["doctor", "master", "bachelor", "associate", "high school"]
                entry = min(
                    p.education, key=lambda e: next((i for i, r in enumerate(ranks) if r in normalize(e.degree)), len(ranks))
                )
            source = f"profile:education.{p.education.index(entry)}.end"
            if entry.end:
                categories = [o for o in f.get("options", []) if self.graduation_choice_valid(f, o) is True]
                if len(categories) == 1:
                    return self._ans(f, categories[0], [source])
                # iCIMS asks "Graduation Date (Month / Day / Year)" as a Month menu, a Day menu and a 4-character
                # Year box ("05/2026" became "05/2"), and refuses a date missing any part ("Please fill in all date
                # fields", Schwab 2026-10-21). Each part gets its own component, all parts or none.
                part = re.search(r"graduation (?:date )?(year|month|day)\b", label)
                if part and part[1] == "month" and re.search(r"year", label):
                    part = None  # "Graduation month/year" asks for both.
                evidence = [source]
                if part:
                    whole = self.graduation_parts(f, entry.end, part[1])
                    if not whole:
                        return None
                    components, evidence = whole
                    number = int(components[part[1]] or 0)
                    if part[1] == "year":
                        spellings = [components["year"]]
                    elif part[1] == "month":
                        from calendar import month_abbr, month_name

                        spellings = [month_name[number], month_abbr[number], f"{number:02d}", str(number)]
                    else:
                        spellings = [str(number), f"{number:02d}"]
                    options = f.get("options") or []
                    value = next(
                        (o for o in options if normalize(o) in {normalize(s) for s in spellings}), spellings[0]
                    )
                else:
                    value = (
                        entry.end
                        if f.get("type") in {"date", "date_parts", "month"} or f.get("widget") == "date-text"
                        else month_year(entry.end)
                    )
                    full_date = f.get("type") in {"date", "date_parts"} or (
                        # Quora's Ashby date box is a text input that refuses a month alone (2026-10-24).
                        f.get("widget") == "date-text" and f.get("date_format") != "month-year"
                    )
                    if (
                        full_date
                        and re.fullmatch(r"\d{4}-\d{2}", str(entry.end))
                        and getattr(getattr(self, "answer_rules", None), "history_day", "") == "first"
                    ):
                        # The owner's rule: a day the profile does not record is the 1st (Quora's full-date picker,
                        # 2026-10-23, went to the owner as "missing exact date").
                        value = f"{entry.end}-01"
                        evidence = [*evidence, "fact:history_date_day_policy"]
                return self._ans(f, value, evidence)
        if p.education and re.search(
            r"highest (?:level of )?(?:education|(?:academic )?degree)|education level|level of education",
            label,
        ):
            ranks = ["doctor", "master", "bachelor", "associate", "high school"]
            order = lambda e: next((i for i, r in enumerate(ranks) if r in normalize(e.degree)), len(ranks))  # noqa: E731
            best = sorted(p.education, key=order)[0]
            from ..widgets import _alias_group

            for alias in [best.degree, *sorted(_alias_group(best.degree), key=len, reverse=True)]:
                if a := self._ans(f, alias, ["profile:education.highest"]):
                    return a
            return self._policy_ans(f, ["profile:education.highest"]) or self.education_fallback(
                f, best.degree, ["profile:education.highest"]
            )
        if len(label.split()) > 6:
            return None
        if p.education and re.search(
            r"^(?:school|university|college|institution|school name|university name)$", label
        ):
            return self.school_answer(f, p.education[0].school, ["profile:education.0.school"])
        if p.education and re.search(r"^(?:degree|degree type|degree level)$", label):
            evidence = ["profile:education.0.degree"]
            return (
                self._ans(f, p.education[0].degree, evidence)
                or self._policy_ans(f, evidence)
                or self.education_fallback(f, p.education[0].degree, evidence)
            )
        if p.education and re.search(r"^(?:discipline|major|field of study|area of study)$", label):
            evidence = ["profile:education.0.field_of_study"]
            return (
                self._ans(f, p.education[0].field_of_study, evidence)
                or self._policy_ans(f, evidence)
                or self.education_fallback(f, p.education[0].field_of_study, evidence)
            )
        current = next((w for w in p.work if w.current), p.work[0] if p.work else None)
        if current and re.search(r"^(?:current|most recent) (?:company|employer)(?: name)?$", label):
            return self._ans(f, current.company, ["profile:work.current.company"])
        if current and re.search(r"^(?:current|most recent) (?:job )?(?:title|position|role)$", label):
            return self._ans(f, current.title, ["profile:work.current.title"])
        return None

    def _entry_answer(self, f, label):
        group = (f.get("group", "") or "") + " " + (f.get("section", "") or "")
        # "Employment History 1" is SuccessFactors' wizard group (2026-10-15).
        m = re.search(
            r"(work experience|experience|employment(?: history)?|work history|education)\s*(\d+)",
            group,
            re.I,
        )
        # An unnumbered repeated row is placed by its occurrence (forms.mark_history_rows).
        row = f.get("history_row") or {}
        named = None if m else re.fullmatch(
            r"\s*(education|experience|work experience|employment)\s+at\s+(.+?)\s*", group, re.I
        )
        if named and not row:
            # Oracle names a row by its school or employer ("Education at University of Maryland", 2026-10-23).
            education = named[1].lower() == "education"
            entries = self.profile.education if education else self.profile.work
            names = [normalize(getattr(e, "school" if education else "company", "") or "") for e in entries]
            wanted = normalize(named[2])
            matches = [i for i, n in enumerate(names) if n and (n == wanted or n in wanted or wanted in n)]
            if not matches and education:
                from ..widgets import school_other_option

                if school_other_option(named[2]):
                    # The catalog's Other names a school it does not list (HEXAWARE's "Education at Other", Oracle
                    # 2026-10-26: its End Date Month went to the owner). The row's own degree level identifies it.
                    level = degree_level((f.get("history_identity") or {}).get("degree", ""))
                    matches = [i for i, e in enumerate(entries) if level and degree_level(e.degree or "") == level]
            if len(matches) == 1:
                row = {"kind": "education" if education else "work", "index": matches[0]}
        if not m and not row:
            return self._single_entry(f, label)
        # "Employer Country" / "Employer City" / "School State" are the row's location, not its name: Oracle's
        # Sherwin-Williams row (2026-10-23) put the employer name into both and the page refused the section.
        part = re.sub(
            r"^(?:employer|company|organi[sz]ation|school|institution|university|college)\s+"
            r"(?=(?:city|town|state|province|country|region|location)$)",
            "",
            normalize(f["label"]),
        )
        if part != normalize(f["label"]):
            f = {**f, "label": part}
            label = part
        if HISTORY_CONTACT.search(f["label"]):
            # The profile holds no street address, postal code or phone for an employer or school, and the
            # applicant's own contact facts never fill them (Atlassian 2026-10-19: iCIMS's parser put the company
            # name into "Address" and the home zip into "Zip", and the resolver agreed).
            return Answer(
                field_id=f["id"],
                disposition="review" if f.get("required") else "skip",
                evidence_ids=[HISTORY_UNKNOWN],
                reason="No street address, postal code or phone is recorded for this history row",
                blocker_type="missing_fact",
            )
        index = int(m[2]) - 1 if m else row["index"]
        if index == "highest":
            ranks = ["doctor", "master", "bachelor", "associate", "high school"]
            levels = [next((i for i, r in enumerate(ranks) if r in normalize(e.degree)), len(ranks))
                      for e in self.profile.education]
            index = levels.index(min(levels)) if levels else 0
        if (m[1].lower() if m else row["kind"]) == "education":
            entries, patterns, kind = self.profile.education, EDUCATION, "education"
        else:
            entries, patterns, kind = self.profile.work, WORK, "work"
        # The row's own "State", not its scoped label "State (Education at University of ...)", which also names a
        # school: WTW's education State got the school name (Oracle, 2026-10-24).
        region = HISTORY_REGION.fullmatch(normalize(f["label"])) or HISTORY_REGION.fullmatch(
            normalize(re.sub(r"^(?:school|institution|university|college)\s+", "", f.get("own_label") or "", flags=re.I))
        )
        if index >= len(entries):
            # Never fall through to the applicant's home State/Country for a row the profile does not have.
            return (
                Answer(field_id=f["id"], disposition="skip", reason="No such history row") if region else None
            )
        entry = entries[index]
        if region:
            # A history row's State/Country come only from that row's own location ("Ann Arbor, MI" ->
            # Michigan, United States; "Pune, India" -> India, no state), never from the home address.
            value = history_region(getattr(entry, "location", "") or "", region[1].startswith("country"))
            evidence = [f"profile:{kind}.{index}.location"]
            stated = list(getattr(self.answer_rules, "employment_countries", []) or [])
            if kind == "work" and region[1].startswith("country") and index < len(stated) and stated[index].strip():
                value, evidence = stated[index].strip(), ["fact:employment_countries"]  # The applicant's own list.
            if not value and kind == "education":
                # Schools have no saved location; "University of Michigan, Ann Arbor" or "Ohio State University" is in
                # that state (WTW's Oracle education row, 2026-10-24). "Washington University" (Missouri) is not read.
                from .eligibility import US_STATES

                school = str(entry.school or "")
                code = next(
                    (
                        c for c, name in US_STATES.items()
                        if re.search(rf"\buniversity of {re.escape(name)}\b|^{re.escape(name)} state university\b", school, re.I)
                    ),
                    "",
                )
                if code:
                    evidence = [f"profile:{kind}.{index}.school"]
                    if region[1].startswith("country"):
                        value = "United States"
                    else:
                        answer = self._ans(f, US_STATES[code], evidence) or self._ans(f, code, evidence)
                        if answer:
                            return answer
            answer = self._ans(f, value, evidence) if value else None
            return answer or Answer(
                field_id=f["id"],
                disposition="review" if f.get("required") else "skip",
                reason=f"No {region[1]} is recorded for {kind} {index + 1}",
                blocker_type="missing_fact",
            )
        if HISTORY_PERIOD.fullmatch(re.sub(r"\s*[*✱:]+\s*$", "", f["label"].strip())) and f.get("type") == "text":
            # One box for the row's whole period (TikTok's "Start & end date", 2026-10-28) takes both of its dates.
            begin = month_year(entry.start) if entry.start else ""
            close = "Present" if getattr(entry, "current", False) else (month_year(entry.end) if entry.end else "")
            if begin and close:
                return self._ans(
                    f, f"{begin} - {close}", [f"profile:{kind}.{index}.start", f"profile:{kind}.{index}.end"]
                )
        if HISTORY_CLASS.search(normalize(f["label"])):
            return None  # "Type of Company" classifies the employer; the row's recorded name does not answer it.
        order = list(patterns.items())
        if kind == "work" and re.match(r"(?:is|are|was|were|do|did|does|have|has)\b", normalize(f["label"])):
            # "Is this your current position?" asks whether the row is current; "position" is also the title pattern
            # (Belden SuccessFactors audited the answer "No" against the job title, 2026-10-29).
            order.sort(key=lambda item: item[0] != "current")
        for attr, pattern in order:
            if not pattern.search(f["label"]):
                continue
            if f.get("type") == "checkbox" and attr != "current":
                return None  # A history date/name cannot establish a checkbox proposition.
            value = getattr(entry, attr)
            if attr == "location" and value and re.fullmatch(r"(?:city|town)", normalize(f["label"])):
                value = str(value).split(",")[0].strip()  # "Pune, India" -> "Pune" in a City box
            if attr == "gpa" and value and not f.get("options") and (number := re.match(r"\s*(\d+(?:\.\d+)?)", str(value))):
                # A GPA box takes the number: Oracle refused "3.9/4.0" ("Enter a valid number", INTEGRIS 2026-10-23).
                value = number[1]
            if attr == "current":
                value = "true" if value else "false"
                if f.get("type") != "checkbox":
                    value = "Yes" if value == "true" else "No"
            elif attr in {"start", "end"}:
                if attr == "end" and getattr(entry, "current", False):
                    ats = self.job.get("ats")
                    # Workday's "I currently work here" box hides its end date; the box answers it there.
                    # An optional end date the page still refuses ("Enter a date after Start Date": Dallas County's
                    # Oracle row kept a parsed end date beside its Current Job box, 2026-10-25) needs a date too.
                    refused = bool(f.get("error")) and ats != "workday"
                    if not ((f.get("required") and ats != "workday") or ats == "icims" or refused):
                        return Answer(
                            field_id=f["id"], disposition="skip", reason="Current role has no end date"
                        )
                    # SuccessFactors' application wizard requires an end date on every employment row and has
                    # no currently-employed checkbox (ASSA ABLOY, 2026-10-15): today's date says "ongoing".
                    # iCIMS has no such checkbox either; ICE refused a blank one with "Invalid Data Error" although
                    # the field carries no required marker (2026-10-19). Oracle rows without the checkbox
                    # (Dallas County, Community Health Systems 2026-10-26) asked the owner for the month instead.
                    from datetime import date as _date

                    value = _date.today().isoformat()
                if not value:
                    return Answer(
                        field_id=f["id"],
                        disposition="review" if f.get("required") else "skip",
                        reason=f"Missing confirmed {kind} {index + 1} {attr} date",
                        blocker_type="missing_fact",
                    )
                # iCIMS splits each date into Month (Jan..Dec), Day (1..31) and Year boxes.
                component = re.search(r"\b(year|month|day)\b", f["label"], re.I)
                if (
                    component
                    and f.get("type") not in {"date_parts", "date", "month"}
                    and f.get("widget") != "date-text"
                ):
                    from ..widgets import parse_date

                    month, day, year = parse_date(str(value))
                    if re.fullmatch(r"\d{4}", str(value)):
                        year = str(value)
                    unit = component[1].casefold()
                    if unit == "day" and not day and month and year:
                        # A month-precision fact in a Day box: the first of the month, as iCIMS's own resume parser
                        # fills every row ("Jul / 1 / 2023"). ICE refused a placeholder day beside a month and year.
                        day = "01"
                    part = {"year": year, "month": month, "day": day}[unit]
                    if not part:
                        return Answer(
                            field_id=f["id"],
                            disposition="review" if f.get("required") else "skip",
                            reason=f"Missing confirmed {kind} {index + 1} {attr} {unit}",
                            blocker_type="missing_fact",
                        )
                    value = str(year if unit == "year" else int(part))
                    answer = self._ans(f, value, [f"profile:{kind}.{index}.{attr}"])
                    if not answer and unit == "month":
                        from calendar import month_abbr, month_name

                        for name in (month_name[int(month)], month_abbr[int(month)]):
                            if answer := self._ans(f, name, [f"profile:{kind}.{index}.{attr}"]):
                                break
                    return answer
                value = (
                    (
                        str(value)
                        if f.get("type") in {"date", "date_parts", "month"} or f.get("widget") == "date-text"
                        else month_year(value)
                    )
                    if value
                    else ""
                )
                if re.fullmatch(r"\d{4}-\d{1,2}", value) and (
                    f.get("type") == "date"
                    or (
                        f.get("widget") == "date-text"
                        and f.get("date_format", "mdy") in {"mdy", "dmy", "ymd"}
                    )
                ):
                    # A full-date control needs a day; a history month starts on the 1st, as the
                    # SuccessFactors picker does (careers.sap.com MM/DD/YYYY rows, 2026-10-16).
                    value += "-01"
            if value in ("", None):
                return None
            evidence = [f"profile:{kind}.{index}.{attr}"]
            if kind == "education" and attr == "school":
                return self.school_answer(f, value, evidence)
            if f.get("type") in {"date_parts"}:
                return Answer(field_id=f["id"], value=str(value), disposition="answer", evidence_ids=evidence)
            answer = self._ans(f, value, evidence)
            if not answer and kind == "education" and attr == "degree":
                from ..widgets import offered_degree_equivalent

                if offered_degree_equivalent(f, value):
                    return self._ans(
                        f, f["value"], evidence, "Accepted offered choice at the recorded degree level"
                    )
            if not answer and kind == "education" and attr in {"degree", "field_of_study"}:
                return self._policy_ans(f, evidence) or self.education_fallback(f, value, evidence)
            return answer
        return None

    def names_recorded_employer(self, label):
        """The question (or this job's employer) names a company in the applicant's work history."""
        text = " " + normalize(f"{label} {self.employer or ''}") + " "
        for work in self.profile.work:
            name = re.sub(
                r"\b(?:inc|llc|ltd|corp|corporation|co|company|the)\b", " ", normalize(work.company or "")
            )
            name = re.sub(r"\s+", " ", name).strip()
            if len(name) >= 3 and f" {name} " in text:
                return True
        return False

    def variant_rows(self):
        from ..features.resumes import variant_rows

        if not hasattr(self, "_variant_rows"):
            self._variant_rows = (
                variant_rows(self.db, self.resume_artifact_id) if self.resume_artifact_id else []
            )
            if not self.resume_artifact_id and self.resume_text:
                self._variant_rows = [
                    {"unit_id": "master:selected", "kind": "source_resume", "text": self.resume_text[:14000]}
                ]
        return self._variant_rows
