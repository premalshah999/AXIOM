"""Question identity, factual propositions and compact model evidence.

These helpers preserve meaningful punctuation and scope. They never supply an
unknown personal fact or use lexical similarity as an approved answer.
"""

import hashlib
import json
import re
import unicodedata


CACHE_VERSION = "answers-v8"
AUTH_KEYS = ("work_authorized_us", "work_authorized", "work_authorization_us")
SPONSOR_KEYS = ("requires_sponsorship", "requires_future_sponsorship_us", "needs_sponsorship")
AUTH = re.compile(
    r"authori[sz](?:ed|ation)|eligible.{0,20}work|right to work|entitled to work|permitted.{0,20}work|"
    r"(?:can|able to).{0,35}work|(?:work|employ).{0,25}without.{0,60}sponsor",
    re.I,
)
SPONSOR = re.compile(r"sponsor", re.I)
WITHOUT_SPONSOR = re.compile(
    r"without(?: requiring| needing)?(?: any| employer| company| visa| immigration)*.{0,35}sponsor|"
    r"\b(?:not|never|no|don['’]?t|doesn['’]?t|won['’]?t|do not|does not|will not)"
    r"\b.{0,30}(?:require|need|depend).{0,45}sponsor|"
    r"\b(?:no|not)\b.{0,12}sponsorship (?:required|needed)|(?:require|need).{0,20}no sponsorship",
    re.I,
)


def semantic_text(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "≤": "<=", "≥": ">=", "−": "-"}))
    # Only display suffixes are removed. Comparators, negation, decimals and ranges remain.
    return re.sub(r"\s+", " ", text).strip().rstrip("?*✱ ")


def question_identity(field):
    return {
        "version": CACHE_VERSION,
        "label": semantic_text(field.get("label", "")),
        "type": field.get("type", ""),
        "section": semantic_text(field.get("section", "")),
        "group": semantic_text(field.get("group", "")),
        "hint": semantic_text(field.get("hint", "")),
        "options": [semantic_text(o) for o in field.get("options", [])],
        "employer": semantic_text(field.get("employer", "")),
    }


def screening_text(field):
    label = field.get("label", "")
    hint = str(field.get("hint") or "").strip()
    if hint and hint.casefold() not in label.casefold() and (AUTH.search(label) or SPONSOR.search(label)):
        return label + " " + hint
    return label


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def boolean(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if re.fullmatch(r"(?:yes|true|y)(?: \(confirmed by applicant\))?", text):
        return True
    if re.fullmatch(r"(?:no|false|n)(?: \(confirmed by applicant\))?", text):
        return False
    return None


def supported_boolean(facts, keys):
    candidates = []
    for key, raw in facts.items():
        if key.casefold() not in keys:
            continue
        value = boolean(raw)
        # Older profiles stored this explicitly confirmed authorization as prose.
        # Do not generalize a leading Yes across conditional/limited authorization.
        if (
            value is None
            and key.casefold() in AUTH_KEYS
            and re.fullmatch(
                r"yes,?\s+(?:legally\s+)?authorized to work for any employer in (?:the )?(?:usa|us|united states)"
                r"\s*\(applicant confirmed\)",
                str(raw).strip(),
                re.I,
            )
        ):
            value = True
        candidates.append((key, value))
    values = {value for _, value in candidates if value is not None}
    if len(values) != 1:
        return None
    return next(iter(values)), ["fact:" + key for key, value in candidates if value is not None]


def defined_screening(label):
    """An employer's explicit answer definition needs interpretation, not a boolean alias."""
    return bool(
        SPONSOR.search(label)
        and re.search(
            r"\b(?:select|answer|choose|check)\s+[\"'“”‘’]?(?:yes|no)[\"'“”‘’]?\s+if\b|"
            r"\bfor (?:the )?purposes of\b|\b(?:defined as|includes? (?:applicants|candidates|those))\b|"
            r"\bincludes?\b.{0,160}\b(?:OPT|STEM OPT|F[ -]?1)\b",
            label,
            re.I,
        )
    )


def question_head(label):
    """The question itself: employer boilerplate after its question mark ("does not
    disqualify you...") must not negate it."""
    head, mark, _ = str(label).partition("?")
    return head + mark if mark and len(head) > 20 else str(label)


# A sentence about the applicant, as opposed to a statement about the position.
ABOUT_APPLICANT = re.compile(r"\b(?:you|your|yourself|I|my|me)\b", re.I)


def asked_text(text):
    """The question without the employer's statements before it.

    "This position requires authorization to work in the US without sponsorship. Will you now or in the future
    require sponsorship?" asks only about sponsorship, and "This position does not sponsor any Visa assistance.
    Please confirm you are authorized to work in the US" only about authorization. Reading the opening statement as a
    proposition refused the right answer (2026-10-14, 2026-10-18).
    """
    pieces = re.split(r"(?<=[.!])(?<!U\.S\.)(?<!e\.g\.)(?<!i\.e\.)\s+(?=[A-Z])", str(text))
    while len(pieces) > 1 and not ABOUT_APPLICANT.search(pieces[0]):
        pieces.pop(0)
    return " ".join(pieces)


def eligibility_propositions(label):
    """Return required subjects and polarity for the common US eligibility question.

    Foreign-country scope is checked by Resolver. Unusual conditional or
    alternatives stay with the answer model rather than a keyword shortcut.
    """
    if defined_screening(label):
        return None
    label = asked_text(label)
    auth, sponsor = AUTH.search(label), SPONSOR.search(label)
    if not sponsor:
        return None
    negative_sponsor = bool(WITHOUT_SPONSOR.search(label))
    if auth:
        # The sponsorship mention nearest before the authorization: in "Do you require sponsorship, or will you
        # require sponsorship in the future for employment authorization?" the second clause sponsors the
        # authorization. Reading the first mention made it "authorized or sponsored" and forced a Yes (2026-10-18).
        sponsor = ([m for m in SPONSOR.finditer(label) if m.start() < auth.start()] or [sponsor])[-1]
        between = label[min(auth.end(), sponsor.end()) : max(auth.start(), sponsor.start())]
        if sponsor.start() < auth.start() and not re.search(
            r"\?|\b(?:or|and)\s+(?:are|do|will|would|have|is|can|did)\s+you\b", between, re.I
        ):
            # "Will you require Blackstone to sponsor you to obtain, maintain, or extend
            # your employment authorization?": the authorization is what is sponsored,
            # not a second proposition. Reading "or" here answered Yes for an applicant
            # who needs no sponsorship.
            return {"sponsorship": not negative_sponsor, "operator": "and"}
        operator = "or" if not negative_sponsor and re.search(r"\bor\b", between, re.I) else "and"
        if re.search(r"\b(?:if|unless|provided that|whether)\b", label, re.I):
            return None
        negative_auth = bool(
            re.search(
                r"(?:not|never|aren['’]?t|isn['’]?t).{0,15}(?:legally )?authori[sz]ed|not eligible",
                label,
                re.I,
            )
        )
        return {"authorization": not negative_auth, "sponsorship": not negative_sponsor, "operator": operator}
    if negative_sponsor:
        return {"sponsorship": False, "operator": "and"}
    return None


# How a recorded immigration status is written, and the names a question may use for it. Acronyms are matched
# case-sensitively ("opt out" is not OPT). A plain F-1 covers its OPT and CPT periods; "F-1 CPT" does not name OPT.
STATUS_NAMES = (
    (
        r"\bOPT\b|(?i:optional practical training)",
        r"\b(?:STEM\s+)?OPT\b|(?i:optional practical training)|\bF-?1\b(?!\s+CPT\b)",
    ),
    (
        r"\bCPT\b|(?i:curricular practical training)",
        r"\bCPT\b|(?i:curricular practical training)|\bF-?1\b(?!\s+(?:STEM\s+)?OPT\b)",
    ),
    (r"\bH-?1B1?\b", r"\bH-?1B1?\b"),
    (r"\bTN\b", r"\bTN\b"),
    (r"\bE-?3\b", r"\bE-?3\b"),
    (r"\bJ-?1\b", r"\bJ-?1\b"),
    (r"\bL-?1\b", r"\bL-?1\b"),
    (r"\bO-?1\b", r"\bO-?1\b"),
)
_QUOTE = r"[\"'“”‘’]?"
_ANSWER_VERB = r"(?:select|answer|choose|check|mark|respond(?:\s+with)?|click)"
# "Select "yes" if ...", "should answer “No”"; not "answer ‘Yes/No’" or "answer Yes or No", which define nothing.
DIRECTIVE = re.compile(
    rf"\b{_ANSWER_VERB}\s+{_QUOTE}(?P<answer>yes|no)\b{_QUOTE}(?!\s*(?:/|or\b)\s*{_QUOTE}(?:yes|no)\b)", re.I
)
# Capital One: "... support does include documentation needed for F-1 OPT ... your answer to this question will be
# “Yes.”" The listed statuses come before the definition.
ANSWER_WILL_BE_YES = re.compile(r"(?:answer|response)[^.]{0,60}?\b(?:will|should|must|would)\s+be\W{0,3}yes", re.I)
NEGATION = re.compile(r"\b(?:not|never|no longer|neither|nor)\b|n['’]t\b", re.I)


def recorded_status(facts):
    """The applicant's recorded US immigration status, or ""."""
    for key, value in facts.items():
        if key.casefold().removeprefix("fact:") == "current_immigration_status_us" and value:
            return str(value)
    return ""


def _names_status(text, label_pattern):
    """The condition names the status, and no negation sits just before the name."""
    for hit in re.finditer(label_pattern, text):
        if not NEGATION.search(text[max(0, hit.start() - 30) : hit.start()]):
            return True
    return False


# Where the clause before an instruction starts: a question mark, colon, semicolon, "if", or a sentence end that is not
# "e.g." or "i.e." (a capital letter must follow, so "U.S. on a visa" stays one clause).
CLAUSE_START = re.compile(r"[?!;:]|(?i:\bif\b)|(?<!e\.g)(?<!i\.e)\.\s+(?=[A-Z])")


def _directive_conditions(label):
    """(answer, condition) for every instruction in the label that ties an answer to a condition."""
    hits = list(DIRECTIVE.finditer(label))
    for i, hit in enumerate(hits):
        answer = hit["answer"].casefold() == "yes"
        after = label[hit.end() :]
        if m := re.match(r"\s+if\b", after):
            # "Select "yes" if you are in the U.S. on a nonimmigrant ... visa (e.g. F-1 (including OPT) ...)"
            stop = hits[i + 1].start() - hit.end() if i + 1 < len(hits) else len(after)
            condition = after[m.end() : min(stop, 240)]
            condition = re.split(r"\bplease note\b", condition, maxsplit=1, flags=re.I)[0]
        else:
            # "(OPT and CPT participants should answer “No”)", "if you are on F-1 OPT, answer “Yes”", Intel's "NOTE:
            # Individuals with work authorization issued pursuant to their student visa (e.g., F-1, J-1, M-1) ...
            # should answer “Yes”". The clause runs back to the previous sentence, colon or "if"; parentheses stay in.
            begin = hits[i - 1].end() if i else 0
            condition = CLAUSE_START.split(label[max(begin, hit.start() - 300) : hit.start()])[-1]
        yield answer, condition


def status_defined_answer(label, facts):
    """The question defines its answer for the applicant's recorded status: (answer is Yes, evidence) or None.

    Owner rule: a sponsorship question is answered Yes only when the question itself tells holders of the applicant's
    status (F-1 OPT) to answer Yes, and No otherwise. The stored sponsorship flag answers the plain visa question.
    - University of Chicago 2026-10-18: "Select "yes" if you are in the U.S. on a nonimmigrant or dual-intent visa
      (e.g. F-1 (including OPT), J-1 ...)". The validator read only the first sentence and refused the right Yes 19 times.
    - "Are you legally able to work ... without sponsorship ...? (OPT and CPT participants should answer “No”)".
    - Capital One 2026-10-14: "immigration related support does include documentation needed for F-1 OPT ... your
      answer to this question will be “Yes.”"
    """
    status = recorded_status(facts)
    names = [name for own, name in STATUS_NAMES if re.search(own, status, re.I)]
    if not names:
        return None
    refs = ["fact:current_immigration_status_US"]
    answers = {
        answer
        for answer, condition in _directive_conditions(label)
        if any(_names_status(condition, name) for name in names)
    }
    if len(answers) == 1:
        return next(iter(answers)), refs
    if not answers and ANSWER_WILL_BE_YES.search(label) and any(re.search(name, label) for name in names):
        return True, refs
    return None


# U.S.-person classes (export control, "U.S. worker" reporting). A question listing several of them asks one status.
US_PERSON_CLASS = re.compile(
    r"U\.?\s?S\.?\s+(?:citizen|national|person|worker)|united states (?:citizen|national)|"
    r"(?:lawful |legal )?permanent resident|green.?card|\basylee|granted asylum|\brefugee|protected individual",
    re.I,
)
US_PERSON_ASK = re.compile(
    r"\bare you\b(?:\s+(?:currently|presently))?\s+(?:a|an|any|one|in one|among|either)\b", re.I
)
US_PERSON_KEYS = (
    "us_citizen",
    "us_national",
    "permanent_resident",
    "lawful_permanent_resident",
    "green_card_holder",
    "has_asylee_status",
    "has_refugee_status",
)


def us_person_question(text):
    """The question lists U.S.-person classes and asks whether the applicant is one of them.

    "Are you a U.S. Citizen, U.S. National, Lawful Permanent Resident ..., Asylee, or Refugee?", Micron's "A U.S.
    worker is defined ... as a 1) U.S. citizen ... 5) person granted asylum. Are you in one of the above five
    groups?", and an export-control "Are you any of the following?" whose options are the classes.
    """
    kinds = {m.group(0).casefold()[:6] for m in US_PERSON_CLASS.finditer(text or "")}
    return len(kinds) >= 3 and bool(US_PERSON_ASK.search(text or ""))


def us_person(facts):
    """(is a U.S. person, evidence) when the applicant's facts decide it, else None.

    Any true citizen, national, permanent-resident, asylee or refugee fact makes the applicant one. A false citizen
    fact together with a recorded nonimmigrant status (F-1 OPT, H-1B ...) means they are none of the classes.
    """
    values = {str(k).casefold().removeprefix("fact:"): (k, v) for k, v in facts.items()}
    present = [values[k] for k in US_PERSON_KEYS if k in values]
    if yes := [k for k, v in present if boolean(v) is True]:
        return True, ["fact:" + yes[0]]
    status = recorded_status(facts)
    citizen = values.get("us_citizen")
    if citizen and boolean(citizen[1]) is False and any(re.search(own, status, re.I) for own, _ in STATUS_NAMES):
        return False, ["fact:" + citizen[0], "fact:current_immigration_status_US"]
    return None


def evaluate_eligibility(label, facts):
    proposition = eligibility_propositions(label)
    if not proposition:
        return None
    values, refs = [], []
    for subject, keys in (("authorization", AUTH_KEYS), ("sponsorship", SPONSOR_KEYS)):
        if subject not in proposition:
            continue
        fact = supported_boolean(facts, keys)
        if fact is None:
            return None
        value, sources = fact
        values.append(value == proposition[subject])
        refs.extend(sources)
    return (any(values) if proposition["operator"] == "or" else all(values)), refs


def required_topics(label):
    from .knowledge import topic

    # Only the question itself: "Are you a U.S. Citizen ... or Refugee? ... They do not need ... sponsorship" asks one
    # status question, and its explanation is not a second (authorization and sponsorship) proposition.
    proposition = eligibility_propositions(question_head(label))
    if proposition and "authorization" in proposition:
        return {"authorization", "sponsorship"}
    return {topic(label)} - {""}


def reference_exists(reference, facts):
    if reference in facts:
        return True
    if reference.startswith("fact:") and any(
        key.casefold() == reference.casefold() for key in facts if key.startswith("fact:")
    ):
        return True
    for key, value in facts.items():
        if not reference.startswith(key + "."):
            continue
        current = value
        try:
            for part in reference[len(key) + 1 :].split("."):
                current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        return current not in (None, "")
    return False


def complete_employment_context(profile):
    """One citable attestation carries the full history it describes."""
    return {
        "statement": "Applicant confirmed all employment, internships, contract and temporary work "
        "are listed here. Compare the named employer and affiliates against this complete history. "
        "This does not establish client, family or referral relationships.",
        "rows": [
            {
                "reference": f"profile:work.{i}",
                **{key: getattr(row, key) for key in ("company", "title", "start", "end")},
            }
            for i, row in enumerate(profile.work)
        ],
    }


def relevant_context(fields, facts, evidence, job, *, fact_limit=40, evidence_limit=6):
    """Retrieve by question scope; retained references are the validation allowlist."""
    from .knowledge import relevant_fact

    # Short labels ("Status", "Category", "Military Status") often explain
    # their meaning only in the offered choices. Use that text for retrieval,
    # never as evidence about the applicant or as a direct answer rule.
    questions = " ".join(
        " ".join(
            [
                f.get("label", ""),
                " ".join(str(f.get(k) or "") for k in ("section", "group", "context", "related_question"))[
                    :1200
                ],
                str(f.get("hint", ""))[:500],
                " ".join(str(option) for option in f.get("options", []))[:4000],
            ]
        )
        for f in fields
    )
    words = set(re.findall(r"[a-z0-9]+", questions.casefold()))
    narratives = bool(re.search(r"why|describe|tell us|cover letter|experience|achievement", questions, re.I))
    education = bool(
        re.search(
            r"\bgpa\b|grade point|school|college|university|degree|graduat|enroll|major|field of study",
            questions,
            re.I,
        )
    )
    employment = bool(
        re.search(
            r"employment|employer|work history|professional experience|years.{0,25}experience|tenure|current.{0,20}job",
            questions,
            re.I,
        )
    )

    def score(key, value):
        relevant = sum(relevant_fact(f.get("label", ""), key.removeprefix("fact:")) for f in fields)
        lexical = len(
            words
            & set(
                re.findall(
                    r"[a-z0-9]+", (key.replace("_", " ") + " " + json.dumps(value, default=str)).casefold()
                )
            )
        )
        if key.startswith("profile:work.") or key.startswith("profile:education."):
            lexical += 2 if narratives else 0
            if (education and key.startswith("profile:education.")) or (
                employment and key.startswith("profile:work.")
            ):
                lexical += 20
        return 100 * relevant + lexical

    ordered = sorted(facts, key=lambda key: -score(key, facts[key]))
    # The model should not need to guess a retrieval keyword to see an ordinary
    # applicant fact. Always supply compact confirmed facts and history, then
    # rank longer material for this question. References remain unchanged.
    # Credentials belong to account/mail helpers, never the answer model.
    ordered = [
        key
        for key in ordered
        if not re.search(
            r"password|secret|credential|api[_:-]?key|access[_:-]?token|verification[_:-]?code", key, re.I
        )
    ]
    core = [
        key
        for key in ordered
        if (
            key.startswith("profile:")
            or (
                key.startswith("fact:")
                and isinstance(facts[key], (str, bool, int, float))
                and len(json.dumps(facts[key], default=str).encode()) <= 400
            )
        )
    ]
    # Put small facts before long narratives so the budget cannot crowd out
    # residence, phone or immigration status with an employment description.
    core.sort(
        key=lambda key: (
            isinstance(facts[key], (dict, list)),
            len(json.dumps(facts[key], default=str).encode()),
        )
    )
    # Reserve space for this question's confirmed facts before generic profile
    # material. Length alone previously allowed unrelated short facts to win.
    priority = [key for key in ordered if score(key, facts[key]) >= 100]
    keep = priority + core + [key for key in ordered if score(key, facts[key]) > 0][:fact_limit]
    chosen = {}
    used = 0
    for key in dict.fromkeys(keep):
        value = facts[key]
        if isinstance(value, dict) and key.startswith("profile:work."):
            value = {
                **value,
                "description": str(value.get("description", ""))[: 1600 if narratives or employment else 240],
            }
        size = len(json.dumps({key: value}, default=str).encode())
        if used + size <= 18000:
            chosen[key] = value
            used += size
    if any(key.startswith("profile:work.") and key not in chosen for key in facts):
        # The compact attestation already carries every row. Long descriptions
        # may be omitted without throwing away that complete employment proof.
        attested = chosen.get("fact:employment_history_complete")
        bundled = (
            {row.get("reference") for row in attested.get("rows", [])}
            if isinstance(attested, dict)
            else set()
        )
        if not {key for key in facts if key.startswith("profile:work.")}.issubset(bundled):
            chosen.pop("fact:employment_history_complete", None)

    ranked = sorted(evidence, key=lambda key: -len(words & set(re.findall(r"\w+", evidence[key].casefold()))))
    selected, used = {}, 0
    for key in ranked[:evidence_limit] if narratives else []:
        text = evidence[key][:3000]
        if used + len(text.encode()) <= 10000:
            selected[key] = text
            used += len(text.encode())
    if "resume:selected" in evidence:
        selected["resume:selected"] = evidence["resume:selected"][: 10000 if narratives else 3500]
    description = str(job.get("description") or "")
    if len(description) > 6000:
        paragraphs = re.split(r"\n+|(?<=[.!?])\s+", description)
        relevant = sorted(
            enumerate(paragraphs), key=lambda row: -len(words & set(re.findall(r"\w+", row[1].casefold())))
        )
        picked = sorted(relevant[:16], key=lambda row: row[0])
        description = "\n".join(text for _, text in picked)[:6000]
    return chosen, selected, {**job, "description": description}
