"""What each platform's employer mail looks like, for the empty-database mail tests (2026-11-01).

Sender domains, subject wording and link hosts are the platforms' real formats, read from the sender and subject of mail
production has received; every employer, address, code and token here is made up. An employer's own name is what a real
mail carries in its sender, subject or body, so the identity checks see what they see in real life."""

import secrets

ME = "axiom.test.applicant@gmail.com"
BUTTON = '<a href="{link}" style="background:#06c;color:#fff;padding:8px 16px">{label}</a>'
FOOTER = (
    '<p style="font-size:11px"><a href="https://www.{site}/privacy">Privacy policy</a> | '
    '<a href="https://www.{site}/">{employer} home</a><br>This is an automated message. Please do not reply. © 2026</p>'
)
WRAPPER = '<!doctype html><html><body><table><tr><td><img src="https://www.{site}/logo.png" alt="{employer}"><br>{inner}{footer}</td></tr></table></body></html>'


def html(inner, employer, site):
    footer = FOOTER.format(site=site, employer=employer)
    return WRAPPER.format(site=site, employer=employer, inner=inner, footer=footer)


# url: a posting on the platform's real host. mail: for each kind, (From, subject, how the body says it).
# {code}/{link} are filled per message. `link` is where the platform's verification link really points: the employer's own
# host, or the platform's tracker, which redirects there.
PLATFORMS = {
    "workday": {
        "ats": "workday",
        "company": "Northwind Trading",
        "url": "https://northwind.wd5.myworkdayjobs.com/en-US/External/job/Analyst_R100",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://harborfreight.wd1.myworkdayjobs.com/en-US/Careers/job/Clerk_R7",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <no-reply@otp.workday.com>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Northwind Trading <no-reply@otp.workday.com>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>. It expires in 10 minutes.</p>"),
        "link": ("Northwind Trading <northwind@myworkday.com>", "Verify your candidate account", "<p>Thank you for creating an account.</p>" + BUTTON.replace("{label}", "Verify Account")),
        "reset": ("Northwind Trading <northwind@myworkday.com>", "Reset your password for your candidate account", "<p>We received a request to reset your password.</p>" + BUTTON.replace("{label}", "Reset Password")),
    },
    "icims": {
        "ats": "icims",
        "company": "Northwind Trading, LLC",
        "url": "https://careers-northwind.icims.com/jobs/1234/analyst/job",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines, Inc.",
            "url": "https://careers-harborfreight.icims.com/jobs/77/clerk/job",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <harborfreight+autoreply@talent.icims.com>",
            "code_subject": "Your One-Time Password (OTP) for Profile Creation at Harbor Freight Lines, Inc.",
        },
        "code": ("Northwind Trading <northwind+autoreply@talent.icims.com>", "Your One-Time Password (OTP) for Profile Creation at Northwind Trading, LLC", "<p>Your one-time password is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <northwind+autoreply@talent.icims.com>", "User account with Northwind Trading, LLC", "<p>Finish creating your user account.</p>" + BUTTON.replace("{label}", "Continue")),
        "reset": ("iCIMS <no-reply@icims.com>", "Reset your password", "<p>Use the button below for your Northwind Trading account.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "greenhouse": {
        "ats": "greenhouse",
        "company": "Northwind Trading",
        "url": "https://job-boards.greenhouse.io/northwind/jobs/4001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://job-boards.greenhouse.io/harborfreight/jobs/4002",
            "site": "harbor-freight-lines.example",
            "code_from": "Greenhouse <no-reply@us.greenhouse-mail.io>",
            "code_subject": "Security code for your application to Harbor Freight Lines",
            "code_body": "<p>Copy and paste this code into the security code field on your application to Harbor Freight Lines: <b>{code}</b></p>",
        },
        "code": ("Greenhouse <no-reply@us.greenhouse-mail.io>", "Security code for your application to Northwind Trading", "<p>Copy and paste this code into the security code field on your application to Northwind Trading: <b>{code}</b></p>"),
        "link": ("Greenhouse <no-reply@us.greenhouse-mail.io>", "Verify your email for Northwind Trading", "<p>Please confirm your email address for your application to Northwind Trading.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Greenhouse <no-reply@us.greenhouse-mail.io>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "lever": {
        "ats": "lever",
        "company": "Northwind Trading",
        "url": "https://jobs.lever.co/northwind/1b2c3d4e-0000-4000-8000-000000000001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://jobs.lever.co/harborfreight/1b2c3d4e-0000-4000-8000-000000000002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <no-reply@hire.lever.co>",
            "code_subject": "Your verification code for Harbor Freight Lines",
        },
        "code": ("Northwind Trading <no-reply@hire.lever.co>", "Your verification code for Northwind Trading", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <no-reply@hire.lever.co>", "Verify your email for Northwind Trading", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Northwind Trading <no-reply@hire.lever.co>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "ashby": {
        "ats": "ashby",
        "company": "Northwind Trading",
        "url": "https://jobs.ashbyhq.com/northwind/2c3d4e5f-0000-4000-8000-000000000001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://jobs.ashbyhq.com/harborfreight/2c3d4e5f-0000-4000-8000-000000000002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <no-reply@ashbyhq.com>",
            "code_subject": "Your Harbor Freight Lines application verification code",
        },
        "code": ("Northwind Trading <no-reply@ashbyhq.com>", "Your Northwind Trading application verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <no-reply@ashbyhq.com>", "Confirm your email for Northwind Trading", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Confirm email")),
        "reset": ("Northwind Trading <no-reply@ashbyhq.com>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "smartrecruiters": {
        "ats": "smartrecruiters",
        "company": "Northwind Trading",
        "url": "https://jobs.smartrecruiters.com/NorthwindTrading/743999000000001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://jobs.smartrecruiters.com/HarborFreightLines/743999000000002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <notification@smartrecruiters.com>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Northwind Trading <notification@smartrecruiters.com>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <notification@smartrecruiters.com>", "Verify your email", "<p>Please confirm your email address for Northwind Trading.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Northwind Trading <notification@smartrecruiters.com>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "successfactors": {
        "ats": "successfactors",
        "company": "Northwind Trading",
        "url": "https://career4.successfactors.com/career?company=NorthwindP&career_job_req_id=901",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://career8.successfactors.com/career?company=HarborFreightP&career_job_req_id=902",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <system@successfactors.eu>",
            "code_subject": "One-Time Password for Candidate Account Verification",
            "code_body": "<p>Harbor Freight Lines careers. Passcode: <b>{code}</b></p>",
        },
        "code": ("Northwind Trading <system@successfactors.eu>", "One-Time Password for Candidate Account Verification", "<p>Northwind Trading careers. Passcode: <b>{code}</b></p>"),
        "link": ("Northwind Trading <system@successfactors.eu>", "Activate your candidate account", "<p>Please activate your account.</p>" + BUTTON.replace("{label}", "Activate your account")),
        "reset": ("Northwind Trading <system@successfactors.eu>", "Your password reset request", "<p>To reset your password use the button below.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "oracle": {
        "ats": "oracle",
        "company": "Northwind Trading",
        "url": "https://ecqq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://edel.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/1002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines Recruiting <edel.fa.sender@workflow.email.us-ashburn-1.ocs.oraclecloud.com>",
            "code_subject": "Confirm your identity",
            "code_body": "<p>Harbor Freight Lines. Confirm your identity using this code: <b>{code}</b></p>",
        },
        "code": ("Northwind Trading Recruiting <ecqq.fa.sender@workflow.mail.us2.cloud.oracle.com>", "Confirm your identity", "<p>Northwind Trading. Confirm your identity using this code: <b>{code}</b></p>"),
        "link": ("Northwind Trading Recruiting <ecqq.fa.sender@workflow.mail.us2.cloud.oracle.com>", "Take action to confirm your identity", "<p>Northwind Trading. Please confirm your identity.</p>" + BUTTON.replace("{label}", "Confirm your identity")),
        "reset": ("Northwind Trading Recruiting <ecqq.fa.sender@workflow.mail.us2.cloud.oracle.com>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "taleo": {
        "ats": "taleo",
        "company": "Northwind Trading",
        "url": "https://northwind.taleo.net/careersection/ext/jobdetail.ftl?job=2001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://harborfreight.taleo.net/careersection/ext/jobdetail.ftl?job=2002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <no-reply@harborfreight.taleo.net>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Northwind Trading <no-reply@northwind.taleo.net>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <no-reply@hrnorthwind.northwind.example>", "Set up a user name and password at Northwind", "<p>Use the button below to set up a password.</p>" + BUTTON.replace("{label}", "Set up password")),
        "reset": ("Northwind Trading <no-reply@northwind.taleo.net>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "eightfold": {
        "ats": "eightfold",
        "company": "Northwind Trading",
        "url": "https://northwind.eightfold.ai/careers/job/3001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://harborfreight.eightfold.ai/careers/job/3002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines Careers <confirm@eightfold.ai>",
            "code_subject": "Verify your email for Harbor Freight Lines",
        },
        "code": ("Northwind Trading Careers <confirm@eightfold.ai>", "Verify your email for Northwind Trading", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading Careers <confirm@eightfold.ai>", "Verify your email", "<p>Please confirm your email for Northwind Trading.</p>" + BUTTON.replace("{label}", "Verify your email")),
        "reset": ("Northwind Trading Careers <notifications@career-mail.northwind.example>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "workable": {
        "ats": "workable",
        "company": "Northwind Trading",
        "url": "https://apply.workable.com/northwind/j/ABCDE12345/",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://apply.workable.com/harborfreight/j/FGHIJ67890/",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <noreply@candidates.workablemail.com>",
            "code_subject": "Your verification code for Harbor Freight Lines",
        },
        "code": ("Northwind Trading <noreply@candidates.workablemail.com>", "Your verification code for Northwind Trading", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <noreply@candidates.workablemail.com>", "Verify your email for Northwind Trading", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Northwind Trading <noreply@candidates.workablemail.com>", "Reset your password", "<p>Reset your password for Northwind Trading.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "adp": {
        "ats": "adp",
        "company": "Northwind Trading",
        "url": "https://myjobs.adp.com/northwindcareers/cx/job-details?reqId=5001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://myjobs.adp.com/harborfreightcareers/cx/job-details?reqId=5002",
            "site": "harbor-freight-lines.example",
            "code_from": "ADP <SecurityServices_NoReply@adp.com>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("ADP <SecurityServices_NoReply@adp.com>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("ADP <SecurityServices_NoReply@adp.com>", "Verify your email for Northwind Trading", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("ADP <SecurityServices_NoReply@adp.com>", "Reset your password for Northwind Trading", "<p>Reset your password.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "dayforce": {
        "ats": "dayforce",
        "company": "Northwind Trading",
        "url": "https://jobs.dayforcehcm.com/en-US/northwind/CANDIDATEPORTAL/jobs/6001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://jobs.dayforcehcm.com/en-US/harborfreight/CANDIDATEPORTAL/jobs/6002",
            "site": "harbor-freight-lines.example",
            "code_from": "Dayforce <notify@dayforce.com>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Dayforce <notify@dayforce.com>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Dayforce <notify@dayforce.com>", "Verify your email for Northwind Trading", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Dayforce <notify@dayforce.com>", "Reset your password for Northwind Trading", "<p>Reset your password.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "avature": {
        "ats": "avature",
        "company": "Northwind Trading",
        "url": "https://northwind.avature.net/careers/JobDetail/Analyst/7001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://harborfreight.avature.net/careers/JobDetail/Clerk/7002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines <no-reply@harborfreight.avature.net>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Northwind Trading <no-reply@northwind.avature.net>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading <no-reply@northwind.avature.net>", "Confirm your email", "<p>Please confirm your email for Northwind Trading.</p>" + BUTTON.replace("{label}", "Confirm email")),
        "reset": ("Northwind Trading <no-reply@northwind.avature.net>", "Password activation", "<p>Use the button below to reset your password.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
    "custom": {
        "ats": "custom",
        "company": "Northwind Trading",
        "url": "https://careers.northwind-trading.example/jobs/8001",
        "site": "northwind-trading.example",
        "other": {
            "company": "Harbor Freight Lines",
            "url": "https://careers.harbor-freight-lines.example/jobs/8002",
            "site": "harbor-freight-lines.example",
            "code_from": "Harbor Freight Lines Careers <no-reply@notify.harbor-freight-lines.example>",
            "code_subject": "Your Harbor Freight Lines verification code",
        },
        "code": ("Northwind Trading Careers <no-reply@notify.northwind-trading.example>", "Your Northwind Trading verification code", "<p>Your verification code is <b>{code}</b>.</p>"),
        "link": ("Northwind Trading Careers <no-reply@notify.northwind-trading.example>", "Verify your email", "<p>Please confirm your email address.</p>" + BUTTON.replace("{label}", "Verify email")),
        "reset": ("Northwind Trading Careers <no-reply@notify.northwind-trading.example>", "Reset your password", "<p>Reset your password.</p>" + BUTTON.replace("{label}", "Reset password")),
    },
}
KINDS = ("code", "link", "reset")


def fresh_code():
    return f"{secrets.randbelow(900000) + 100000}"


def deliver(box, platform, kind, *, to=ME, code=None, link=None, other=False, from_=None, **extra):
    """One real-format message into the fake mailbox. Returns what it carries (the code or the link). `from_` replaces the
    sender: mail from a domain the platform and the employer's name do not give away."""
    spec = PLATFORMS[platform]
    employer, site = spec["company"], spec["site"]
    if other:
        o = spec["other"]
        employer, site = o["company"], o["site"]
        sender, subject = o["code_from"], o["code_subject"]
        body = o.get("code_body", "<p>Your verification code is <b>{code}</b>.</p>")
    else:
        sender, subject, body = spec[kind]
    sender = from_ or sender
    if kind == "code":
        code = code or fresh_code()
        inner, text = body.format(code=code), f"Your verification code is {code}"
        if "Passcode" in body:
            text = f"{employer} careers. Passcode: {code}"
        elif "Confirm your identity" in body:
            text = f"{employer}. Confirm your identity using this code: {code}"
        elif "security code field" in body:
            text = f"Copy and paste this code into the security code field on your application to {employer}: {code}"
        elif "one-time password" in body:
            text = f"Your one-time password is {code}"
    else:
        inner = body.format(link=link)
        text = None  # A real account mail's plain part repeats the words; the button is in the HTML.
    box.deliver(
        sender=sender,
        to=to,
        subject=subject,
        body=text or "Please open this message in an HTML mail reader.",
        html=html(inner, employer, site),
        **extra,
    )
    return code if kind == "code" else link
