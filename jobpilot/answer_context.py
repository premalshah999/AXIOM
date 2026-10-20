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


def eligibility_propositions(label):
    """Return required subjects and polarity for the common US eligibility question.

    Foreign-country scope is checked by Resolver. Unusual conditional or
    alternatives stay with the answer model rather than a keyword shortcut.
    """
    if defined_screening(label):
        return None
    auth, sponsor = AUTH.search(label), SPONSOR.search(label)
    if not sponsor:
        return None
    negative_sponsor = bool(WITHOUT_SPONSOR.search(label))
    if auth:
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


# Statuses a question can name when it defines its own answer ("support includes F-1 OPT ... answer Yes").
STATUS_PATTERNS = (
    r"\bSTEM\s+OPT\b",
    r"\bOPT\b",
    r"\bCPT\b",
    r"\bH-?1B1?\b",
    r"\bTN\b",
    r"\bE-?3\b",
    r"\bJ-?1\b",
    r"\bL-?1\b",
    r"\bO-?1\b",
)


def status_defined_answer(label, facts):
    """The question defines Yes for listed statuses, and the applicant's recorded status is listed.

    Capital One: "immigration related support does include documentation needed for F-1 OPT ...
    If you require this type of support, your answer to this question will be 'Yes'". An OPT
    applicant's honest answer is Yes, even though they need no visa sponsorship.
    """
    if not re.search(
        r"(?:answer|response)[^.]{0,60}?\b(?:will|should|must|would)\s+be\W{0,3}yes", label, re.I
    ):
        return None
    status = str(
        facts.get("current_immigration_status_us") or facts.get("fact:current_immigration_status_US") or ""
    )
    for pattern in STATUS_PATTERNS:
        if re.search(pattern, status, re.I) and re.search(pattern, label, re.I):
            return True, ["fact:current_immigration_status_US"]
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

    proposition = eligibility_propositions(label)
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
