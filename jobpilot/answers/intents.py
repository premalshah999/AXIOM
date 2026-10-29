"""Answer patterns: intents."""

import re


# Screening intents mapped to fact keys. Each answer still cites the fact it used.
INTENTS = [
    (
        re.compile(r"^(?:primary )?(?:country of (?:your )?)?citizenship$|^nationality$", re.I),
        ["country_of_citizenship", "citizenship"],
    ),
    (
        # John Deere (Eightfold) 2026-10-16: "require assistance in obtaining work status in the United States
        # (e.g. H-1B, TN, Permanent Residency)" never says "sponsor"; its DACA note ("authorized to work")
        # sent it to the authorization fact and answered Yes.
        re.compile(
            r"sponsor|require[sd]? (?:any )?(?:assistance|help|support) (?:in|with|for) "
            r"(?:obtaining|maintaining|securing|getting) (?:your )?(?:current )?(?:work|employment|immigration|visa)",
            re.I,
        ),
        ["requires_sponsorship", "requires_future_sponsorship_us", "needs_sponsorship"],
    ),
    (
        re.compile(
            r"(?:legally |currently )?(?:authori[sz]ed|eligible|permitted|right) to work|work authori[sz]ation|"
            r"employment authori[sz]ation|eligible for employment|(?:legally )?entitled to work|"
            # Target 2026-10-14: "Are you legally authorized for employment in the United States?"
            r"(?:legally )?authori[sz]ed (?:for|to accept|to be in|to obtain) employment|eligible to be employed",
            re.I,
        ),
        ["work_authorized_us", "work_authorized", "work_authorization_us"],
    ),
    (
        re.compile(
            # Not a count of something else: "received assistance at least 18 months between ..." (Montenson's WOTC
            # questionnaire, 2026-10-29) read as the age question, and a truthful No was blocked as contradicting it.
            r"\b(?:18|eighteen)\b(?!\s*(?:months?|weeks?|days?|hours?|credits?|units?|%|percent))"
            r".{0,30}(?:years|older|over|age)|of legal (?:working )?age|"
            r"at least 18\b(?!\s*(?:months?|weeks?|days?|hours?|credits?|units?|%|percent))|"
            r"\bover (?:18|eighteen)\b(?!\s*(?:months?|weeks?|days?|hours?))|\bage (?:18|eighteen)\b",
            re.I,
        ),
        ["over_18", "age_18_or_older"],
    ),
    (re.compile(r"\bu\.?s\.? citizen|citizen of the united states", re.I), ["us_citizen"]),
    (
        re.compile(
            r"(?:previously|ever|formerly|currently) (?:been )?(?:employed|worked|work) (?:by|for|at|with)|"
            r"former employee|current or former|worked (?:for|at) .{0,40} before|previous(?:ly)? (?:work|employ)|"
            r"(?:have|had) you (?:ever )?(?:been )?(?:employed|worked|interned|contracted) (?:by|for|at|with)|"
            # Amazon 2026-10-27: "Are you currently working as an intern at Amazon?"
            r"\b(?:currently|presently) (?:working|employed|interning) (?:as an? [\w-]+ )?(?:at|for|with)\b|"
            r"(?:employ|work).{0,80}(?:subsidiar|affiliate|parent (?:company|organi[sz]ation)|sister compan|"
            r"any of (?:the following|these|our) (?:companies|organi[sz]ations|entities|brands))",
            re.I,
        ),
        ["previously_employed_here", "previous_employee"],
    ),
    (
        re.compile(
            r"relative|family member|related to (?:anyone|an employee|any (?:current )?employee)|"
            r"\bspouse\b|domestic partner|significant other|household member|in.laws?\b|"
            r"(?:know|acquainted with) (?:anyone|someone|any (?:current |existing )?(?:employees?|staff|one))|"
            r"(?:close )?personal relationship with|close (?:friend|associate)s? (?:who|that|of)",
            re.I,
        ),
        ["has_relatives_at_employer"],
    ),
    (
        re.compile(
            r"\bnon[\s-]?(?:compet(?:e|ition)|solicit(?:ation)?)\b|restrictive covenant|"
            # ABB (Workday) 2026-10-18: "Have you signed an agreement with any employer(s) that you will not, (a) engage
            # in competition with that former employer, or (b) contact customers of that employer?"
            r"agree\w*.{0,120}\bnot\b.{0,40}(?:engage in |enter into )?competi|"
            r"agree\w*.{0,200}\b(?:not|never)\b.{0,80}(?:contact|solicit)\w*.{0,30}(?:customers|clients)",
            re.I,
        ),
        ["has_non_compete"],
    ),
    (
        re.compile(
            r"(?:agreements?|understanding).{0,180}(?:restrict|prohibit|interfere|prevent).{0,120}"
            r"(?:employ|work|duties|position|joining|performing.{0,30}services)|"
            r"(?:restrict|prohibit).{0,80}(?:accept|perform).{0,60}(?:employ|work|duties)",
            re.I,
        ),
        ["has_employment_restrictions"],
    ),
    (
        re.compile(r"convicted|felony|misdemeanor|criminal (?:record|history|offen)", re.I),
        ["criminal_conviction"],
    ),
    (
        re.compile(r"relocat.{0,40}(?:own expense|own cost)|(?:own expense|own cost).{0,40}relocat", re.I),
        ["relocation_at_own_expense"],
    ),
    (
        # Needing paid help to move is not the same fact as being willing to move.
        re.compile(
            r"relocat\w*.{0,30}(?:assist|support|package|benefit|reimburs|stipend|help)|"
            r"(?:assist|support|package|reimburs|stipend|help)\w*.{0,30}relocat",
            re.I,
        ),
        ["requires_relocation_assistance"],
    ),
    (re.compile(r"relocat", re.I), ["willing_to_relocate"]),
    (
        re.compile(
            r"willing(?:ness)? to travel|able to travel|open to travel|travel (?:up to|requirement)", re.I
        ),
        ["willing_to_travel"],
    ),
    (
        re.compile(r"on.?site|in.?office|in the office|hybrid|commute|report to (?:the|our) office", re.I),
        ["willing_onsite", "willing_to_work_onsite"],
    ),
    (
        re.compile(r"security clearance|clearance level|(?:an? )?active clearance", re.I),
        ["security_clearance", "has_security_clearance"],
    ),
    (re.compile(r"valid.{0,20}driver.s licen[sc]e|driver.s licen[sc]e", re.I), ["valid_drivers_license"]),
    (
        re.compile(r"\bfired\b|involuntarily discharged|asked to resign", re.I),
        ["ever_fired", "involuntarily_discharged"],
    ),
    (
        re.compile(r"(?:attended|taken.{0,30}classes).{0,50}community college", re.I),
        ["attended_community_college"],
    ),
    (re.compile(r"conditional job offer.{0,180}withdrawn", re.I), ["conditional_job_offer_withdrawn"]),
    (re.compile(r"FINRA.{0,60}licen[sc]|licen[sc].{0,60}FINRA", re.I), ["has_finra_licenses"]),
    (
        re.compile(r"experience.{0,60}travel.{0,40}(?:extensively|for work)", re.I),
        ["past_extensive_work_travel"],
    ),
    (
        re.compile(
            r"salary|compensation|pay (?:expectation|requirement|range)|desired pay|expected (?:pay|ctc)",
            re.I,
        ),
        ["desired_salary", "salary_expectation"],
    ),
    (
        re.compile(
            # HEXAWARE (Oracle, 2026-10-24): "Can Join By" Month/Day/Year was left to a model, which chose yesterday.
            r"start date|date available to work|available to start|notice period|earliest (?:start|availability)|earliest.{0,35}start|when can you start|when.{0,45}(?:start|commence|begin) (?:a |your )?(?:new )?(?:role|employment|work)|"
            r"can join(?: by| from| on)?\b|joining date|date of joining|available to join",
            re.I,
        ),
        ["availability", "notice_period", "start_date"],
    ),
    (
        re.compile(r"(?:were you|have you been) referred|employee referral|referred by", re.I),
        ["referred_by_employee"],
    ),
    (
        re.compile(
            r"(?:have|were|are).{0,80}you.{0,80}(?:employ|work).{0,100}(?:government|military|publicly.funded institution|public institution)|(?:government|military).{0,80}(?:employ|work)",
            re.I,
        ),
        ["government_employment_history"],
    ),
    (
        re.compile(
            r"government (?:official|employee|employ)|public official|politically exposed|foreign official|"
            r"(?:state|government)[- ]owned|government (?:entity|agency|body|instrumentality)|"
            r"elected (?:official|office)|political (?:party|office|candidate|position)|"
            r"prominent (?:public|government|political)|anti.?(?:corruption|bribery)|\bFCPA\b|"
            r"public (?:office|servant)|holds? (?:a |any )?(?:public|government) (?:office|position)",
            re.I,
        ),
        ["government_official"],
    ),
    (re.compile(r"visa (?:status|type)|immigration status|current (?:work )?visa", re.I), ["visa_status"]),
    (re.compile(r"\bgpa\b|grade point", re.I), ["gpa"]),
    (
        re.compile(
            r"how did you (?:hear|learn|find)|where did you (?:hear|find|learn)|source of (?:application|referral)|^source(?: type)?$",
            re.I,
        ),
        ["application_source"],
    ),
    (re.compile(r"pronoun", re.I), ["pronouns"]),
    # Questionnaire answers (Knowledge › Frequently asked): the questions applications most often stopped on
    # (production Needs-you statistics, 2026-10-29). Each reads the fact the applicant saved, never a guess.
    (re.compile(r"nicotine|tobacco|e-?cigarette|\bvap(?:e|ing)\b|\bsmok(?:e|er|ing)\b", re.I), ["uses_nicotine"]),
    (
        re.compile(
            r"(?:do you|have you|will you).{0,60}\bintellectual property\b|"
            r"(?:own|control|economic interest).{0,60}intellectual property",
            re.I,
        ),
        ["has_intellectual_property"],
    ),
    (re.compile(r"refused.{0,20}bond|bond.{0,30}(?:refused|denied|revoked|cancel)", re.I), ["ever_refused_bond"]),
    (re.compile(r"\b(?:been|ever) bonded\b|\bbonded\b.{0,20}\?", re.I), ["ever_bonded"]),
    (
        re.compile(r"(?:criminal )?(?:charges?|indictments?)\b.{0,30}\bpending|pending\b.{0,20}(?:criminal )?charges?", re.I),
        ["has_pending_charges"],
    ),
    (
        re.compile(
            r"professional misconduct|(?:suspended|barred|disciplined|sanctioned).{0,80}(?:regulat|licen[sc]|FINRA|"
            r"securities|association|profession)|found responsible of",
            re.I,
        ),
        ["professional_sanctions"],
    ),
    (
        re.compile(
            r"(?:consent|agree|permission).{0,60}\b(?:AI|artificial intelligence)\b|"
            r"\b(?:AI|artificial intelligence)\b.{0,60}(?:evaluat|screen|assess|review).{0,40}(?:candidacy|application)",
            re.I,
        ),
        ["ai_screening_consent"],
    ),
    (
        re.compile(
            r"(?:prior|previous|current|any) (?:u\.?s\.? )?(?:military|armed forces) service|"
            r"service in the (?:u\.?s\.?|united states) armed forces",
            re.I,
        ),
        ["prior_military_service"],
    ),
]
