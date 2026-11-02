"""Synthetic resumes and postings for the tailoring evals: five fields, four postings each. Every name, employer and school is made up."""

from jobpilot.features.resumes.editor import Bullet, Entry, ResumeDoc, Section


def _doc(name, roles, school, degree, skills, projects=()):
    entries = []
    for i, (title, employer, dates, bullets) in enumerate(roles):
        entries.append(Entry(id=f"r{i}", heading=title, subheading=employer, dates=dates, bullets=[Bullet(id=f"r{i}b{j}", text=t) for j, t in enumerate(bullets)]))
    sections = [Section(id="experience", title="Experience", kind="experience", entries=entries)]
    if projects:
        sections.append(
            Section(
                id="projects",
                title="Projects",
                kind="projects",
                entries=[Entry(id=f"p{i}", heading=t, bullets=[Bullet(id=f"p{i}b{j}", text=x) for j, x in enumerate(bs)]) for i, (t, bs) in enumerate(projects)],
            )
        )
    sections.append(Section(id="education", title="Education", kind="education", entries=[Entry(id="e0", heading=school, subheading=degree, dates="08/2016 - 05/2020")]))
    sections.append(Section(id="skills", title="Skills", kind="skills", text=skills))
    return ResumeDoc(name=name, contact=[f"{name.split()[0].lower()}@example.test", "+1 202 555 0100"], sections=sections)


RESUMES = {
    "analyst": _doc(
        "Alex Example",
        [
            ("Data Analyst", "ExampleSoft", "06/2021 - Present", [
                "Built a reporting pipeline in SQL and Python that replaced a manual spreadsheet process for the finance team",
                "Designed dashboards in Tableau that show weekly revenue by region, used by twelve account managers",
                "Reviewed monthly close variances with the controller and documented each adjustment for the external audit",
            ]),
            ("Analytics Intern", "Sample Labs", "06/2020 - 08/2020", [
                "Cleaned survey data in pandas and wrote a short report on response bias for the research team",
                "Automated a weekly export with a Python script, which saved the lab about two hours a week",
            ]),
        ],
        "Example University",
        "B.S., Statistics",
        "Languages: Python, SQL\nTools: Tableau, Excel, pandas",
        [("InvoiceCheck", ["Wrote a command line tool in Python that checks invoices against purchase orders every night"])],
    ),
    "nurse": _doc(
        "Sam Sample",
        [
            ("Registered Nurse", "Sample General Hospital", "07/2020 - Present", [
                "Cared for up to six patients per shift on a medical-surgical unit, including post-operative recovery",
                "Administered medications and documented every dose and assessment in Epic during each shift",
                "Taught discharge plans to patients and families, and followed up on questions by telephone the next day",
                "Precepted three new graduate nurses through their first ten weeks on the unit",
            ]),
        ],
        "Sample College of Nursing",
        "B.S.N., Nursing",
        "Clinical: Medication Administration, Patient Education, IV Therapy\nCertifications: BLS, ACLS\nSystems: Epic",
    ),
    "accountant": _doc(
        "Jordan Placeholder",
        [
            ("Staff Accountant", "Example Manufacturing Co.", "01/2021 - Present", [
                "Prepared month-end journal entries and account reconciliations for a four-entity manufacturing group",
                "Reduced the close from eight days to five by standardizing accrual schedules in Excel",
                "Supported the annual external audit by preparing lead sheets and answering auditor requests",
            ]),
            ("Accounting Clerk", "Sample Supply LLC", "06/2019 - 12/2020", [
                "Processed about 300 vendor invoices a month in QuickBooks and resolved payment disputes with suppliers",
                "Maintained the fixed asset register and calculated monthly depreciation for the controller",
            ]),
        ],
        "Example State University",
        "B.B.A., Accounting",
        "Accounting: GAAP, Reconciliations, Accruals\nSystems: QuickBooks, Excel",
    ),
    "marketer": _doc(
        "Riley Testcase",
        [
            ("Marketing Coordinator", "Example Outdoor Goods", "03/2022 - Present", [
                "Planned and sent weekly email campaigns in Mailchimp to a list of about 40,000 customers",
                "Wrote product pages and blog posts, and tracked organic traffic in Google Analytics each month",
                "Ran paid social campaigns on Meta with a monthly budget of 8,000 dollars and reported results to the director",
            ]),
            ("Marketing Assistant", "Sample Events", "05/2020 - 02/2022", [
                "Scheduled social media posts and answered customer comments across three brand accounts",
                "Coordinated logistics for twelve trade shows, including booth materials and shipping",
            ]),
        ],
        "Sample University",
        "B.A., Communications",
        "Marketing: Email Marketing, Content Marketing, Social Media\nTools: Mailchimp, Google Analytics, Canva",
    ),
    "engineer": _doc(
        "Casey Fixture",
        [
            ("Software Engineer", "ExampleSoft", "08/2020 - Present", [
                "Built REST APIs in Python and FastAPI for the billing service, used by four internal teams",
                "Moved nightly batch jobs to Airflow and added alerting, which ended most of the manual restarts",
                "Reduced p95 latency on the invoice endpoint from 1.8 seconds to 400 milliseconds by adding a Redis cache",
                "Wrote integration tests with pytest and ran them in GitHub Actions on every pull request",
            ]),
        ],
        "Example Institute of Technology",
        "B.S., Computer Science",
        "Languages: Python, SQL, TypeScript\nInfrastructure: Docker, Airflow, Redis, GitHub Actions\nPractices: Code Review, Testing",
        [("TinyQueue", ["Wrote a small job queue in Python on top of Redis to learn how retries and visibility timeouts work", "Chose at-least-once delivery over exactly-once after reading how much the second one would cost to build"])],
    ),
}

_P = {
    "analyst": [
        ("Reporting Analyst", "Requirements\n- Experience with Python, SQL and Tableau\n- Knowledge of GAAP and financial reporting\n- Experience with Snowflake and Kubernetes\nResponsibilities\nBuild reporting pipelines and own dashboards for finance stakeholders.\nBenefits\nDental and a winning culture."),
        ("Business Intelligence Developer", "What you'll do\nBuild Power BI dashboards and data models for the sales team.\nRequirements\n- Power BI and DAX\n- SQL and data modeling\n- Experience with Azure Synapse\nPreferred\n- Python is a plus"),
        ("Marketing Data Analyst", "Responsibilities\nAnalyze campaign performance, run A/B testing and report results to marketing leads.\nQualifications\n- SQL and Excel\n- A/B testing and statistics\n- Experience with Google Analytics and Looker"),
        ("Senior Data Scientist", "Requirements\n- PhD or 8 years of experience\n- Machine learning, PyTorch, deep learning\n- Experience with Spark and AWS\n- Published research\nPreferred\n- Experience leading a team"),
    ],
    "nurse": [
        ("ICU Registered Nurse", "Qualifications\n- Active RN license\n- BLS and ACLS certification\n- Two years of ICU experience\n- Experience with ventilator management and hemodynamic monitoring\nResponsibilities\nProvide patient care and medication administration in a critical care unit."),
        ("Telemetry Nurse", "Responsibilities\nMonitor telemetry patients, document in Epic, and provide patient education.\nRequirements\n- RN license and BLS\n- Telemetry experience\n- Knowledge of HIPAA and infection control"),
        ("Clinic Nurse", "What you'll do\nTriage patients by telephone, give vaccinations and teach patients about chronic disease management.\nRequirements\n- RN license\n- Experience with Cerner\n- Patient education"),
        ("Nurse Manager", "Requirements\n- 5 years of management experience\n- MSN degree\n- Budgeting, staffing and performance management\n- Experience leading quality improvement projects\nPreferred\n- Certified Nurse Manager credential"),
    ],
    "accountant": [
        ("Senior Accountant", "Requirements\n- CPA preferred\n- Experience with month-end close, GAAP and account reconciliations\n- Proficiency in NetSuite and Excel\nResponsibilities\nPrepare journal entries and support the annual audit."),
        ("Financial Analyst", "Responsibilities\nBuild financial models, forecasting and variance analysis for the FP&A team.\nRequirements\n- Excel and financial modeling\n- Experience with Anaplan\n- Forecasting and budgeting"),
        ("Accounts Payable Lead", "Requirements\n- Accounts payable experience\n- QuickBooks or SAP\n- Vendor management\n- Experience leading a team of clerks\nResponsibilities\nOwn the invoice process and resolve payment disputes."),
        ("Internal Auditor", "Qualifications\n- CIA or CPA\n- SOX compliance and internal controls\n- Risk assessment\n- Experience with audit software such as AuditBoard"),
    ],
    "marketer": [
        ("Email Marketing Manager", "Requirements\n- Email marketing and marketing automation\n- Experience with Mailchimp or HubSpot\n- A/B testing\n- SQL is a plus\nResponsibilities\nOwn campaigns, segmentation and lifecycle journeys."),
        ("Content Strategist", "What you'll do\nPlan content marketing, write blog posts and improve SEO with keyword research.\nRequirements\n- SEO and Google Analytics\n- Copywriting\n- Experience with Ahrefs or SEMrush"),
        ("Paid Media Specialist", "Responsibilities\nManage paid social and Google Ads budgets and report return on ad spend.\nQualifications\n- Meta Ads and Google Ads\n- Budget management\n- Experience with Tableau"),
        ("Director of Marketing", "Requirements\n- 10 years of experience\n- P&L ownership\n- Brand strategy and team leadership\n- Experience managing agencies and a budget above 5 million dollars"),
    ],
    "engineer": [
        ("Backend Engineer", "Requirements\n- Python and FastAPI or Django\n- PostgreSQL and Redis\n- Experience with Kubernetes and AWS\n- CI/CD\nResponsibilities\nBuild REST APIs and improve latency for the billing platform."),
        ("Data Engineer", "What you'll do\nBuild data pipelines with Airflow and dbt.\nRequirements\n- Python and SQL\n- Airflow\n- Snowflake and Spark\nPreferred\n- Kafka"),
        ("Platform Engineer", "Requirements\n- Terraform and Kubernetes\n- AWS\n- Docker and GitHub Actions\n- Observability with Prometheus and Grafana\nResponsibilities\nOwn deployment pipelines and developer tooling."),
        ("Machine Learning Engineer", "Requirements\n- PyTorch and transformers\n- Retrieval-augmented generation and vector databases\n- Python\n- Experience shipping LLM features to production\nPreferred\n- Published research"),
    ],
}

POSTINGS = [
    {"resume": field, "title": title, "company": f"Northwind {i}", "description": text, "url": f"https://jobs.example.test/{field}/{i}", "location": "Remote"}
    for field, rows in _P.items()
    for i, (title, text) in enumerate(rows)
]
assert len(POSTINGS) == 20


def profile_of(doc: ResumeDoc) -> dict:
    skills = []
    for s in doc.sections:
        if s.kind == "skills":
            for line in s.text.splitlines():
                skills += [x.strip() for x in line.split(":", 1)[-1].split(",") if x.strip()]
    work = []
    for s in doc.sections:
        if s.kind == "experience":
            for e in s.entries:
                work.append({"company": e.subheading, "title": e.heading, "start": e.dates[:7], "description": "\n".join("- " + b.text for b in e.bullets)})
    return {"name": doc.name, "email": doc.contact[0], "skills": skills, "work": work}
