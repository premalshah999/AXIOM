# Knowledge base and answering

How ApplyPilot answers an application question: where the applicant's facts live, how a question finds them, when a
model is involved, and the checks that stop a wrong or invented answer. Read this before changing `jobpilot/answers.py`,
`jobpilot/answer_context.py`, `jobpilot/knowledge.py` or `jobpilot/features/knowledge/`.

## Principles

1. **The applicant's facts are data, never code.** Code holds general rules ("a question that tells F-1 OPT holders to
   answer Yes is answered Yes for an applicant whose recorded status is OPT"). The applicant's answers ("my status is
   OPT", "I need no sponsorship") live in their profile and knowledge base. A friend's copy works with their own facts
   and no code changes.
2. **Truthful or nothing.** A legal or personal disclosure is answered only from a fact the applicant confirmed. When
   no fact covers it, the run asks the applicant once (review inbox or Telegram) and saves the answer for next time.
3. **Deterministic first.** Most questions are answered from facts and policies without a model call. The model is used
   for interpretation and prose, and its answer is validated against the same facts.

## Where facts live

| Store | What | Written by |
|---|---|---|
| `settings.profile` (JSON, `jobpilot/schemas.py: Profile`) | Identity, contact, address, education and work history, `facts` (flat structured facts such as `us_citizen`, `requires_future_sponsorship_US`, `current_immigration_status_US`), `reviewed_answers` (answers the applicant confirmed), uploaded evidence, and the answer policies below | Setup wizard, profile page, review inbox, Telegram answers |
| `kn_facts` table (`features/knowledge/tables.py: KnFact`) | The normalized, versioned store used by retrieval. Synced from the profile on every lookup (`migration.sync`, keyed by a profile digest) | `features/knowledge/migration.py`, knowledge API |

A `kn_facts` row has:
- `key`: `kf:<name>` for a structured fact, `kf:education_row` or `kf:work_row` for a history row, or `kq1:<digest>` for
  a confirmed answer keyed by its question's meaning (`features/knowledge/questions.py`: comparators, negation, time
  scope, modality and employer are part of the identity, so "more than 5 years" never matches "less than 5 years").
- `layer`: `confirmed` (usable as an answer), `narrative` (supported prose for essays), `draft` (never used).
- `scope`: `global`, or `employer` with an `employer_id`. Employer-scoped answers are invisible to other employers.
- `provenance`, `valid_from` / `valid_until`, `version`, `superseded_by`, `retired`.

### Answer policies (profile settings)

These are the applicant's standing decisions, applied by general code:

| Setting | Effect |
|---|---|
| `accept_all_application_terms` | Routine hiring-process consents are accepted: privacy and terms acknowledgements, at-will statements, contact/SMS consent, AI-screening consent, being considered for other roles, background-check and drug-screen willingness, and typed e-signatures with the applicant's own name. A consent that embeds a disclosure ("I certify I have never been convicted") still needs its fact |
| `decline_demographics` and `answer_rules.demographics` | Decline voluntary self-identification when the form offers a decline; otherwise use the applicant's own answer for that topic. An option that asserts a status ("I am a protected veteran, but I choose not to self-identify...") is never treated as a decline |
| `application_source`, `answer_rules` | How-did-you-hear, degree/major fallbacks, experience-years tolerance |
| Facts such as `previously_employed_here`, `has_relatives_at_employer`, `government_employment_history`, `government_official`, `has_non_compete` | Answer the prior-employment, relatives, government and restriction questions for every employer. Absent facts are asked, never assumed |

## How one question is answered

```
field ──► Resolver.local (deterministic)            jobpilot/answers.py
            │  demographic decline · status-defined answers · policy facts · U.S.-person status
            │  · hiring-check consent · profile fields · history rows · INTENTS (label → fact key)
            │  · exact retrieval answers
            ▼  (no answer)
          retrieve_answer_context (evidence packet)  jobpilot/features/knowledge/retrieval.py
            │  1 exact key (kq1 / kf)  2 history rows  3 résumé-variant rows
            │  4 structured facts of the category  5 confirmed answers of the category (paraphrase candidates)
            │  6 lexically ranked narratives (essay-type questions only)
            ▼
          AnswerBatch model call (cites evidence ids)
            ▼
          Resolver._validate_answer                   jobpilot/answers.py
            │  evidence exists · critical questions cite a fact on their subject · polarity checks
            │  · consent policy · typed signature · offered option · length limit
            ▼
          answer │ repair once │ ask the applicant (missing_fact)
```

### Critical questions and their subjects

`critical_question(label)` marks legal disclosures: work authorization and sponsorship, citizenship, convictions,
government employment or office (including the Department of Defense), NDAs and restrictive covenants, corruption,
export control and sanctions, clearances, terminations, referrals, consents. The regex is precise on purpose; these are
**not** disclosures: "National Defense Authorization Act", "Authentication, Authorization" (a skill), "authorized to
prescribe", "government-issued ID", "Citizens" (a bank), "agenda", "referred to as", "If your work is under NDA,
describe...".

`topic(label)` (`jobpilot/knowledge.py`) classifies the question itself, ignoring definitions in parentheses and
statements before the question. `relevant_fact(label, key)` then decides which fact keys may support it. A model answer
to a critical question must cite at least one relevant fact.

### Polarity checks (a cited fact is not enough)

The validator also checks the answer's direction where facts decide it:
- **Eligibility** (`answer_context.evaluate_eligibility`): authorization and sponsorship propositions are read from the
  question sentence only (`asked_text` drops the employer's opening statements; `question_head` drops what follows the
  question mark). In "Do you require sponsorship, or will you require sponsorship in the future for employment
  authorization?", the authorization is what is sponsored, so the question is about sponsorship only.
- **Status-defined answers** (`answer_context.status_defined_answer`): when the question itself tells holders of the
  applicant's recorded status what to answer, that answer wins over the plain sponsorship flag. Examples:
  - "Select 'yes' if you are ... on ... F-1 (including OPT)";
  - Intel's "Individuals with work authorization issued pursuant to their student visa (e.g., F-1 ...) should answer
    'Yes'";
  - "OPT and CPT participants should answer 'No'";
  - Capital One's "your answer to this question will be 'Yes'".

  "F-1 CPT" does not name OPT, and "opt out" is not OPT.
- **U.S.-person status** (`answer_context.us_person`): a question listing three or more U.S.-person classes (citizen,
  national, permanent resident, asylee, refugee, protected individual) is answered from `us_citizen` and the recorded
  status. A model answer that contradicts the facts is refused.

## Setting up a new applicant (friends and family)

Fill the profile (setup wizard), then confirm these facts. Each one prevents a question from being asked on every
application:

| Area | Facts |
|---|---|
| Work authorization | `work_authorization_US`, `requires_future_sponsorship_US`, `current_immigration_status_US` (e.g. "OPT; expiration July 2029", "U.S. citizen", "H-1B"), `us_citizen`, `citizenship` |
| Background | `criminal_conviction`, `has_non_compete`, `has_non_solicitation`, `has_employment_restrictions`, `ever_fired` |
| Relationships | `previously_employed_here`, `has_relatives_at_employer`, `government_employment_history`, `government_official`, `company_relationships` |
| Export control | `export_control_restricted_country_history`, `has_asylee_status` |
| Other | `over_18`, `valid_drivers_license`, `willing_to_relocate`, `willing_to_travel`, `availability`, salary expectation |

Answers given later in the review inbox or on Telegram are saved as confirmed facts and reused.

## Debugging an answer

Every refusal is an `answer_validation` event with its `cause`:

| Cause | Meaning |
|---|---|
| `unsupported_evidence` | A cited id does not exist |
| `disclosure_scope` | A critical question with no relevant fact cited |
| `question_polarity` | The answer contradicts the facts |
| `unoffered_choice` | The value is not an offered option |
| `question_time_scope` | A graduation choice that contradicts the dates |
| `field_limit` | Too long for the field |

Model declines are `knowledge_inference_declined` events.

```sh
# Refusals by cause and question (read-only)
sh codex-handoff/bin/q.sh "select data->>'cause', left(data->>'field',100), count(*) from events
  where kind='answer_validation' and created_at > now() - interval '2 days' group by 1,2 order by 3 desc limit 30"
```

To reproduce a refusal offline, build a field from the event's `field`, `options` and `proposed` and run it through
`Resolver.local` and `Resolver._validate_answer` with a synthetic profile (`tests/test_screening_validation.py` shows the
pattern). Tests use synthetic applicants only.

## Change log

- **2026-10-20.** The validator review of every refusal since October 14 found these, now fixed and regression-tested
  in `tests/test_screening_validation.py`:
  - **Status definitions.** The status-definition parser now reads "Select yes if", "should answer" and "must answer"
    clauses. University of Chicago (19 refusals) and Intel are now answered Yes as defined.
  - **Wrong deterministic answers.**
    - Intel's sponsorship question: answered "No", because a DACA shortcut fired on a question that lists DACA as an
      example.
    - Background-check willingness: answered "No" from the clean-record fact.
  - **Topic and regex false positives:**
    - "opt out" read as OPT;
    - "Citizens" (a bank) read as citizenship;
    - "entity sponsored" read as visa sponsorship;
    - "government ID" read as government employment;
    - "referred to as" read as a referral;
    - "Authorization Act" and "Authorization" (a security skill) read as work authorization;
    - an NDA instruction read as a restriction.
  - **New deterministic answers.** U.S.-person status questions (Micron's model had answered "Yes" for an OPT
    applicant). E-signatures and selection consents are accepted under the terms policy.
