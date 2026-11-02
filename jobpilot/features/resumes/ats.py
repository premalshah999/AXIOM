"""The ATS check: a keyword match score anyone can recompute by hand. No model call, for any field of work.

What this estimates, and what it doesn't. No universal ATS score exists. Real systems parse a resume into fields and let recruiters
search and rank; most rank and surface rather than reject, and auto-rejection hangs mostly on knockout questions, not resume text.
Searches are still largely literal, so the realistic failure is weak overlap with the requirements block, then bad parsing. So the score
is one honest number: the weighted share of the posting's terms found in the resume's text.

    score = 100 * (weight of terms found) / (weight of all terms)
    required term 3, responsibility 2, preferred 1

Rules, each from the reference pipeline's hard-won checks or from how parsers match:

- Terms come from the requirements, duties and preferred blocks. Culture prose ("winning culture", "global family"), benefits and
  equal-opportunity text never drive a keyword.
- The posting's own spelling is what's shown (`CI/CD`, not `CI` and `CD`). Matching tolerates case, plurals and hyphens; an alias only
  counts both ways when it's the same thing (RAG, Retrieval-Augmented Generation).
- A missing term is one of two things: `available`, the applicant's own history says it; `needed`, nothing they gave us says it. A
  `needed` term is never inserted anywhere, by anyone, and the UI says "you'd need this".
- Coverage is advisory. 60-70% of requirement terms is a natural resume; chasing 100% is how stuffing happens.

Every term carries a `pattern` that Python and the browser run identically, so the screen rescores on each keystroke without a request
and the server's number, computed on save, is the same one.
"""

import re

from .._store import norm

WEIGHTS = {"required": 3, "duties": 2, "preferred": 1}
MAX_TERMS = 30
NATURAL_LOW, NATURAL_HIGH = 60, 70

METHOD = (
    "The weighted share of this posting's terms found in your resume: required terms count 3, responsibilities 2, preferred 1. "
    "It uses plain text matching and no AI."
)
DISCLAIMER = (
    "This estimates keyword match. No universal ATS score exists, and each employer's system works differently. "
    "A natural resume usually covers 60-70% of the required terms, so don't chase 100%."
)

HEADINGS = {
    "required": (
        r"(?:minimum |basic |required |key |must[- ]have |essential |core )*(?:requirements?|qualifications?|skills(?: and experience| required)?|competenc(?:y|ies))"
        r"|what you(?:'|’| wi)ll need|what we(?:'|’| a)re looking for|what you bring|what you(?:'|’)ll bring|what you have|you have|you bring"
        r"|who you are|about you|must haves?|your background|your experience|experience(?: required)?|education(?: and experience)?|licens(?:e|ure)s?(?: and certifications?)?|certifications?"
        r"|(?:you (?:may|might|could|will) be|you(?:'|’)ll be|you(?:'|’)re|you are) an? (?:great |good |strong |ideal )?(?:fit|match)(?: if)?|(?:the )?ideal candidate"
    ),
    "duties": (
        r"(?:key |core |primary |main )*(?:responsibilit(?:y|ies)|duties)|what you(?:'|’| wi)ll do|what you(?:'|’)ll be doing|the role|about the role|about this role|your role"
        r"|in this role|day[- ]to[- ]day|you will|job description|position summary|role overview|overview of the role|the job|the work"
    ),
    "preferred": (
        r"(?:preferred|desired|additional)(?: qualifications?| skills?| experience)?|nice[- ]to[- ]haves?|good to have|bonus(?: points)?|pluses|extra credit"
        r"|helpful(?:,? but not required)?|not required,? but [\w ]+|even better(?: if)?"
    ),
    "other": (
        r"about (?:us|the company|the team|our company|[A-Z][\w&. ]{1,30})|who we are|benefits?|perks|compensation|salary|pay(?: range)?|what we offer|why (?:join|work)[\w ]*"
        r"|equal (?:employment )?opportunity|eeo|diversity[\w ]*|our (?:values|culture|mission|story)|how to apply|location|work (?:authorization|environment)|physical demands|disclaimer"
        r"|why you(?:'|’)ll love[\w ]*|what success looks like|how we work|show us what you(?:'|’)ve built|life at [\w ]+"
    ),
}
HEAD = {tier: re.compile(rf"^\W*(?:{body})\W*$", re.I) for tier, body in HEADINGS.items()}
INLINE_HEAD = re.compile(
    r"(?<=[.!?;\s])((?:minimum |basic |preferred |required |key )?(?:requirements|qualifications|responsibilities|duties|benefits|nice to have|what you(?:'|’| wi)ll (?:do|need|bring))\s*:)",
    re.I,
)
# Legal and benefits sentences a posting often runs on into, under whichever heading came last. They never describe the work.
BOILERPLATE = re.compile(
    r"equal (?:employment )?opportunity|consideration for employment|without regard to|regardless of (?:race|age|gender|sex)|protected (?:veteran|class|status|basis)|national origin|sexual orientation"
    r"|reasonable accommodation|e-verify|follow [\w.' ]{1,30} on (?:linkedin|twitter|facebook|instagram|glassdoor)\b|learn more at\b|parental leave|(?:paid|discretionary|flexible|unlimited|generous) time off"
    r"|health,? (?:vision|dental)|medical,? dental|dental,? (?:and )?vision|401\(?k\)?"
    r"|competitive (?:compensation|salary|pay|benefits)",
    re.I,
)
SOFT_PREFERRED = re.compile(r"\b(?:preferred|a plus|is a bonus|nice to have|desirable|ideally|not required)\b", re.I)

STOP = frozenset(
    """a about above across after again all also an and any are as at be because been being below between both but by can could did do does doing down during each either else etc
    few for from further had has have having he her here hers him his how i if in into is it its itself just me more most must my no nor not now of off on once only or other our ours
    out over own per plus same she should so some such than that the their theirs them then there these they this those through to too under until up us very via was we were what when
    where which while who whom why will with would you your yours within without using use used uses including include includes included like well able ability abilities""".split()
)
# Words a posting uses about any job. They're never a keyword alone, and never begin or end one.
GENERIC = frozenset(
    """experience experienced experiences skill skills knowledge understanding background expertise proficiency proficient familiarity familiar work working works worked job jobs
    role roles position positions team teams company companies candidate candidates applicant applicants employee employees employer opportunity opportunities year years month months
    day days time times strong excellent great good solid proven demonstrated deep broad high highly new key core primary main various multiple several many other others related
    relevant required require requires requirement requirements preferred desired responsible responsibility responsibilities qualification qualifications degree bachelor bachelors
    master masters ideal successful success help helps helping support supports supporting ensure ensures ensuring provide provides providing make makes making build builds create
    creates develop develops manage manages lead leads drive drives own owns join need needs needed want wants looking seek seeking get getting take taking part parts level levels
    environment environments world people person business businesses best first one two three four five both across fast paced fast-paced self motivated self-motivated detail oriented
    detail-oriented hands hands-on plus bonus etc area areas field fields industry industries client clients customer customers related ongoing day-to-day communication interpersonal
    active current valid senior junior lead principal staff entry mid perform performs prepare prepares maintain maintains read reads educate educates own applicable""".split()
)
# All-capitals words that aren't tools or skills.
NOT_TERMS = frozenset(
    "US USA UK EU EEO EOE AA ADA LLC INC LTD CEO CFO CTO COO VP PTO WFH NYC SF LA DC II III IV BS BA MS MA PHD MBA OR AND THE YOU OUR FOR YOUR WE TO OF IN ON AT AN AS".split()
)
LEAD_INS = re.compile(
    r"\b(?:experience (?:with|in|using|on)|knowledge of|proficien(?:t|cy) (?:in|with)|familiar(?:ity)? with|expertise (?:in|with)|background in|understanding of|skilled in|"
    r"competen(?:t|cy) in|working with|hands-on (?:with|experience in)|certifi(?:ed|cation) (?:in|as)|licensed as|trained in|fluent in|fluency in|command of|exposure to|such as|including)\s+([^.;:\n()]{2,160})",
    re.I,
)
# A tool, product or acronym by its shape: PyTorch, SQL, C++, Node.js, .NET, CI/CD, P&L, GAAP.
TOOLISH = re.compile(
    r"(?<![\w.+#/&-])(?:[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z]{2,}[0-9]*[a-z]{0,2}|[A-Z][A-Za-z]*(?:\+\+|#)|\.NET|[A-Z]?[a-zA-Z]+\.js|[A-Z][A-Za-z0-9]*/[A-Z][A-Za-z0-9]*|[A-Z]&[A-Z])(?![\w+#/&-])"
)
# Two to four capitalized words: "Microsoft Excel", "Epic Systems". "and" never joins them: "Python and SQL" is two terms, and a word ending a sentence never starts the next term.
TITLE_PHRASE = re.compile(r"(?<![/\w])[A-Z][\w+#&-]*(?:\.[\w+#&-]+)*(?:\s(?:of\s|&\s)?[A-Z][\w+#&-]*(?:\.[\w+#&-]+)*){1,3}(?![\w/])")
# Real terms shared across fields, so a posting that says "budgeting" once still counts it. The applicant's own skills are added at runtime.
KNOWN = (
    "A/B testing,account management,accounts payable,accounts receivable,acute care,Agile,algorithms,analytics,API,APIs,audit,AWS,Azure,bookkeeping,budgeting,business development,"
    "case management,cash flow,change management,chart review,CI/CD,cloud,CNC,coding,cold calling,compliance,computer vision,content marketing,contract negotiation,copywriting,CPR,CRM,"
    "curriculum development,customer service,cybersecurity,dashboards,data analysis,data engineering,data modeling,data pipelines,data science,data visualization,databases,deep learning,"
    "demand generation,distributed systems,Docker,documentation,e-commerce,EHR,email marketing,EMR,ERP,ETL,event planning,Excel,financial analysis,financial modeling,financial reporting,"
    "forecasting,fundraising,GAAP,Git,GitHub,Google Analytics,grant writing,graphic design,HIPAA,HTML,CSS,inventory management,JavaScript,Java,Jira,Kubernetes,lead generation,lesson planning,"
    "Linux,logistics,machine learning,market research,medication administration,microservices,natural language processing,negotiation,NLP,onboarding,patient care,payroll,performance management,"
    "PowerPoint,Power BI,process improvement,procurement,product management,production support,program management,project management,PyTorch,Python,quality assurance,quality control,"
    "recruiting,regulatory compliance,relational databases,REST,risk management,root cause analysis,Salesforce,sales,scheduling,scikit-learn,Scrum,SEO,SEM,social media,SolidWorks,"
    "SQL,stakeholder management,statistics,supply chain,Tableau,talent acquisition,TensorFlow,Terraform,testing,training,troubleshooting,TypeScript,UI,unit testing,user research,UX,"
    "vendor management,version control,welding,Word,workflow,Xero,QuickBooks,React,Node.js,Spark,Airflow,Snowflake,dbt,pandas,NumPy,FastAPI,Django,Flask,PostgreSQL,MySQL,MongoDB,Redis,Kafka,"
    "retrieval-augmented generation,RAG,LLM,evaluation,prompt engineering,R"
).split(",")
# Same thing, two spellings. Both directions count, because some parsers match literally and a recruiter searches either.
ALIASES = {
    "rag": ["retrieval augmented generation"],
    "retrieval-augmented generation": ["RAG"],
    "javascript": ["JS"],
    "js": ["JavaScript"],
    "typescript": ["TS"],
    "kubernetes": ["K8s"],
    "k8s": ["Kubernetes"],
    "postgresql": ["Postgres"],
    "postgres": ["PostgreSQL"],
    "nlp": ["natural language processing"],
    "natural language processing": ["NLP"],
    "machine learning": ["ML"],
    "ml": ["machine learning"],
    "ci/cd": ["CICD", "continuous integration"],
    "llm": ["large language model"],
    "large language model": ["LLM"],
    "ehr": ["electronic health record", "electronic health records"],
    "emr": ["electronic medical record", "electronic medical records"],
    "crm": ["customer relationship management"],
    "erp": ["enterprise resource planning"],
    "seo": ["search engine optimization"],
    "sem": ["search engine marketing"],
    "ui": ["user interface"],
    "ux": ["user experience"],
    "qa": ["quality assurance"],
    "quality assurance": ["QA"],
    "aws": ["Amazon Web Services"],
    "gcp": ["Google Cloud"],
    "excel": ["Microsoft Excel", "MS Excel"],
    "power bi": ["PowerBI"],
}


def _body(term: str) -> str:
    """One spelling as a pattern: any case, spaces or hyphens between words, an optional plural on the last."""
    words = [w for w in re.split(r"[\s-]+", term.strip()) if w]
    parts = []
    for i, w in enumerate(words):
        piece = re.escape(w)
        if i == len(words) - 1 and re.search(r"[A-Za-z]{2}$", w) and not w.isupper() and not w.endswith("s"):
            piece += "(?:s|es)?"
        elif i == len(words) - 1 and w.isupper() and len(w) >= 2:
            piece += "s?"
        elif i == len(words) - 1 and w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
            piece = re.escape(w[:-1]) + "s?"
        parts.append(piece)
    return r"[\s\-]+".join(parts)


def pattern_for(term: str) -> str:
    """Runs the same in Python (re.I | re.A) and JavaScript ("i", lookbehind included)."""
    spellings = [term, *ALIASES.get(norm(term), [])]
    bodies = sorted({_body(s) for s in spellings}, key=len, reverse=True)
    return r"(?<![\w+#])(?:" + "|".join(bodies) + r")(?![\w+#])"


def matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text or "", re.I | re.A) is not None


# A posting read from an API often arrives as one long line, with its headings still in it as capitalized words.
FLAT_HEAD = re.compile(
    r"(?<=[\s.!?:;])((?:(?:Minimum|Basic|Required|Preferred|Desired|Additional|Key|Core) )?(?:Qualifications|Requirements|Responsibilities|Duties)|Preferred|Nice to Have|Nice-to-Have|"
    r"What [Yy]ou(?:'|’| wi)ll [Dd]o|What [Yy]ou(?:'|’| wi)ll (?:[Nn]eed|[Bb]ring)|What [Ww]e(?:'|’| a)re [Ll]ooking [Ff]or|What [Ww]e [Oo]ffer|About (?:[Uu]s|[Yy]ou|[Tt]he (?:[Rr]ole|[Cc]ompany|[Tt]eam))|Who [Yy]ou [Aa]re|Benefits|Perks|Compensation|Equal Opportunity)"
    r"(?=\s+[A-Z•\-]|\s*:)"
)


def _unflatten(description: str) -> str:
    if description.count("\n") >= 3:
        return description
    return FLAT_HEAD.sub(lambda m: "\n" + m.group(1) + "\n", description)


def blocks(description: str) -> dict:
    """The posting by what each part is. Without any heading, all of it describes the work."""
    text = INLINE_HEAD.sub(lambda m: "\n" + m.group(1) + "\n", _unflatten(description or ""))
    out = {"required": [], "duties": [], "preferred": [], "other": []}
    current, seen = None, False
    head_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tier = next((t for t, rx in HEAD.items() if rx.match(stripped)), None) if len(stripped) <= 70 else None
        if tier:
            current, seen = tier, True
            continue
        head_lines.append((current, stripped))
    for tier, line in head_lines:
        where = tier if tier else ("other" if seen else "duties")
        # By the sentence: a posting flattened to one paragraph ends in "forklift preferred", and that doesn't make all of it optional.
        for sentence in re.split(r"(?<=[.!?;])\s+", line):
            if BOILERPLATE.search(sentence):
                out["other"].append(sentence)
                continue
            # A requirement that calls itself optional counts as preferred.
            soft = where in {"required", "duties"} and SOFT_PREFERRED.search(sentence)
            out["preferred" if soft else where].append(sentence)
    return {k: "\n".join(v) for k, v in out.items()}


def _clean_candidate(raw: str) -> str:
    words = re.sub(r"[\"'“”‘’()\[\]]", " ", raw).split()
    while words and (words[0].lower().strip(",:") in STOP or words[0].lower().strip(",:") in GENERIC):
        words = words[1:]
    while words and (words[-1].lower().strip(",:.") in STOP or words[-1].lower().strip(",:.") in GENERIC):
        words = words[:-1]
    return " ".join(words).strip(" ,.:;/-")


def _list_items(fragment: str) -> list[str]:
    parts = re.split(r",|;|\band\b|\bor\b|\band/or\b", fragment)
    return [_clean_candidate(p) for p in parts]


def _acceptable(term: str) -> bool:
    if not term or len(term) < 2 or len(term) > 40 or len(term.split()) > 4:
        return False
    if term in NOT_TERMS or term.upper() in NOT_TERMS and term.isupper():
        return False
    low = term.lower()
    if low in STOP or low in GENERIC or re.fullmatch(r"[\d\W]+", term):
        return False
    return not all(w.lower() in STOP or w.lower() in GENERIC for w in term.split())


def _candidates(text: str, own_terms: list[str]) -> tuple[dict[str, str], set[str]]:
    """({normalized: the posting's spelling} found in one block, the keys only a word count made terms)."""
    found: dict[str, str] = {}
    low = text.lower()

    def add(term):
        term = term.strip()
        if _acceptable(term):
            found.setdefault(norm(term), term)

    def spelled(term):
        """The term as the posting wrote it."""
        m = re.search(pattern_for(term), text, re.I | re.A)
        return m.group(0) if m else None

    for term in [*KNOWN, *own_terms]:
        if term == "R":
            # A lone letter counts only in a list of languages, where it can't be the start of a sentence.
            if re.search(r"(?:\b(?:Python|SQL|Java|SAS|Stata|MATLAB)\s*,?\s*(?:and\s+|or\s+)?R\b)|(?:\bR\s*,\s*(?:Python|SQL|SAS|Stata)\b)", text):
                found.setdefault("r", "R")
            continue
        if norm(term).split()[0] in low or len(term) < 5:
            hit = spelled(term)
            if hit:
                add(hit)
    for m in TOOLISH.finditer(text):
        add(m.group(0))
    for m in LEAD_INS.finditer(text):
        for item in _list_items(m.group(1)):
            # "want to continue developing your engineering" came from one sentence running on past its list: a real term has no "your" or "want to" inside it.
            if item and len(item.split()) <= 4 and not any(w.lower() in GENERIC or w.lower() in STOP - {"of", "for"} for w in item.split()):
                add(item)
    for m in TITLE_PHRASE.finditer(text):
        phrase = _clean_candidate(m.group(0))
        # "Tableau Knowledge of GAAP" is a flattened sentence, not a name.
        if len(phrase.split()) >= 2 and not any(w.lower() in GENERIC for w in phrase.split()):
            add(phrase)
    # Recurring phrases: a posting repeats what it cares about, in any field.
    words = re.findall(r"[a-z][a-z+#/-]{2,}", low)
    counts: dict[str, int] = {}
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            gram = words[i : i + n]
            if gram[0] in STOP or gram[0] in GENERIC or gram[-1] in STOP or gram[-1] in GENERIC or any(w in STOP for w in gram):
                continue
            key = " ".join(gram)
            counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if count >= 2:
            add(key)
    singles: dict[str, int] = {}
    for w in words:
        if len(w) >= 6 and w not in STOP and w not in GENERIC:
            singles[w] = singles.get(w, 0) + 1
    frequent = set()
    for key, count in singles.items():
        if count >= 3:
            if norm(key) not in found:
                frequent.add(norm(key))
            add(key)
    return found, frequent


def terms(description: str, title: str = "", own_terms: list[str] | None = None, limit: int = MAX_TERMS, company: str = "") -> list[dict]:
    """The posting's keywords: each with its tier, weight and pattern, in the order a recruiter would care about them.

    `common` marks a plain word that's here only because the posting repeats it ("building", "follow"): it counts in the score, but it's no skill, so nobody claims or adds it as one.
    """
    own = [t for t in (own_terms or []) if t and len(t) <= 40]
    parts = blocks(description)
    picked: dict[str, dict] = {}
    position = 0
    for tier in ("required", "duties", "preferred"):
        text = parts[tier]
        if not text:
            continue
        found, frequent = _candidates(text, own)
        for key, spelling in found.items():
            position += 1
            if key in picked:
                continue
            picked[key] = {"term": spelling, "key": key, "tier": tier, "weight": WEIGHTS[tier], "order": position, **({"common": True} if key in frequent else {})}
    # The employer's own name is what the posting talks about, not something an applicant could have.
    employer = norm(company)
    for key in list(picked):
        if employer and (key == employer or key in employer.split()):
            picked.pop(key)
    # One thing, two spellings: keep the posting's more visible one and let its pattern match both.
    for key in list(picked):
        for alias in ALIASES.get(key, []):
            other = norm(alias)
            if other in picked and other != key and key in picked:
                first, second = sorted((key, other), key=lambda k: (-picked[k]["weight"], picked[k]["order"]))
                picked.pop(second, None)
    # The same word twice, once in the plural: the first spelling's pattern already matches both.
    for key in list(picked):
        if key.endswith("s") and key[:-1] in picked:
            picked.pop(key)
    # A fragment of a longer term isn't its own term when the longer one is here ("learning" beside "machine learning").
    for key in sorted(picked, key=len, reverse=True):
        if key not in picked:
            continue
        for other in list(picked):
            if other != key and re.search(rf"(?<![\w+#]){re.escape(other)}(?![\w+#])", key) and len(other.split()) < len(key.split()):
                # Only if the shorter one never stands alone in the posting's blocks.
                alone = sum(1 for _ in re.finditer(rf"(?<![\w+#]){re.escape(other)}(?![\w+#])", norm(description)))
                inside = sum(1 for _ in re.finditer(rf"(?<![\w+#]){re.escape(key)}(?![\w+#])", norm(description)))
                if alone <= inside:
                    picked.pop(other, None)
    title_norm = norm(title)
    flat = norm(" ".join(parts[t] for t in ("required", "duties", "preferred")))
    rows = []
    for row in picked.values():
        pattern = pattern_for(row["term"])
        count = len(re.findall(pattern, flat, re.I | re.A))
        bonus = 1 if count >= 3 else 0
        if title_norm and row["key"] in title_norm:
            bonus += 1
        rows.append({**row, "weight": row["weight"] + bonus, "base": row["weight"], "count": count, "pattern": pattern})
    rows.sort(key=lambda r: (-r["weight"], r["order"]))
    return [{k: v for k, v in r.items() if k not in {"order", "key"}} for r in rows[:limit]]


def score(term_rows: list[dict], resume_text: str, evidence_text: str = "") -> dict:
    """The score, its parts, and each term's status. Pure: same input, same number, here and in the browser."""
    keywords, found_weight, total_weight = [], 0, 0
    tiers = {t: {"found": 0, "total": 0} for t in WEIGHTS}
    for row in term_rows:
        hit = matches(row["pattern"], resume_text)
        status = "matched" if hit else ("available" if matches(row["pattern"], evidence_text) else "needed")
        total_weight += row["weight"]
        found_weight += row["weight"] if hit else 0
        tier = row["tier"]
        tiers[tier]["total"] += 1
        tiers[tier]["found"] += 1 if hit else 0
        keywords.append({**row, "status": status})
    return {
        "score": round(100 * found_weight / total_weight) if total_weight else None,
        "found_weight": found_weight,
        "total_weight": total_weight,
        "tiers": tiers,
        "keywords": keywords,
        "method": METHOD,
        "disclaimer": DISCLAIMER,
    }


def forbidden(score_result: dict) -> list[dict]:
    """Terms nothing in the applicant's material supports. Nothing may add them to a resume."""
    return [k for k in score_result["keywords"] if k["status"] == "needed"]


def adds_forbidden(text: str, source: str, forbidden_rows: list[dict]) -> list[str]:
    """Forbidden terms this text states that its own source doesn't. This also catches lower-case tool names, which claims.NAMED can't see."""
    return [k["term"] for k in forbidden_rows if matches(k["pattern"], text) and not matches(k["pattern"], source)]
