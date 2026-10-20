"""RTX (2026-10-18): a fr-CA Workday posting rendered every step in French and 30 page states in a row were
unrecognized. Workday postings are opened in en-US; other ATSs and English locales are unchanged."""

from jobpilot.workday import english_posting_url

FR = ("https://globalhr.wd5.myworkdayjobs.com/fr-CA/Private_Posting_No_TMP/job/US-IA-CEDAR-RAPIDS-112/"
      "Software-Engineer-I_01800567?q=1")


def test_non_english_workday_locale_becomes_en_us():
    assert english_posting_url(FR) == FR.replace("/fr-CA/", "/en-US/")
    assert english_posting_url("https://acme.wd1.myworkdaysite.com/de-DE/recruiting/acme/External/job/X_R1") == \
        "https://acme.wd1.myworkdaysite.com/en-US/recruiting/acme/External/job/X_R1"


def test_english_and_other_ats_urls_are_unchanged():
    for url in ("https://acme.wd5.myworkdayjobs.com/en-GB/Careers/job/X_R1",
                "https://acme.wd5.myworkdayjobs.com/Careers/job/X_R1",
                "https://jobs.lever.co/acme/fr-CA/123", "https://boards.greenhouse.io/acme/jobs/1"):
        assert english_posting_url(url) == url
