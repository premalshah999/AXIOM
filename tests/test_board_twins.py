"""One posting listed by two sources under slightly different words is one row on the Job board. 14 groups of the 1,186 open postings
were the same posting twice (2026-11-02): a list titles it "New Grad 2027: Software Systems Engineer" and the employer's board
"Software Systems Engineer"; one spells a dash as a hyphen. Different postings that only share a company and a city stay two."""

import pytest

from jobpilot.board_view import key_of


def row(title, company="Example Corp", location="Laurel, MD"):
    return {"title": title, "company": company, "location": location}


@pytest.mark.parametrize("a,b", [
    ("Software Systems Engineer", "New Grad 2027: Software Systems Engineer"),
    ("DevOps Engineer I - Azure", "DevOps Engineer I – Azure"),
    ("Full Stack Engineer 4", "Full-stack Engineer 4"),
    ("C++ Software Engineer - Early Career", "Early Career C++ Software Engineer"),
    ("ASIC Design Engineer - New College Grad 2027", "ASIC Design Engineer - New College Grad 2026"),
    ("Software Engineer New Grad - Distributed Data Systems", "New Grad 2027: Software Engineer, Distributed Data Systems"),
    ("Software Embedded Engineer - Hiring at Multiple Levels", "Software Embedded Engineer (Hiring at Multiple Levels)"),
])
def test_the_same_posting_under_another_spelling_is_one_row(a, b):
    assert key_of(row(a)) == key_of(row(b))


@pytest.mark.parametrize("a,b", [
    ("Software Engineer I", "Software Engineer II"),
    ("Summer 2026 Software Engineer Intern", "Summer 2027 Software Engineer Intern"),
    ("Data Engineer", "Data Scientist"),
])
def test_different_postings_stay_apart(a, b):
    assert key_of(row(a)) != key_of(row(b))


def test_another_place_or_company_stays_apart():
    assert key_of(row("Software Engineer")) != key_of(row("Software Engineer", location="Boston, MA"))
    assert key_of(row("Software Engineer")) != key_of(row("Software Engineer", company="Other Corp"))
