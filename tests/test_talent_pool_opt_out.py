"""AVEVA 2026-10-15: an opt-in/opt-out talent-pool data retention question is answered NO (the default)."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

LABEL = (
    "Yes, I agree that AVEVA may keep my data on file to be considered for other roles in AVEVA and other software "
    "companies in the Schneider Electric Group, and that AVEVA and other software companies in the Schneider Electric "
    "Group may contact me at a later date. I can withdraw this consent at any time. Indicate ‘YES’ to Opt In "
    "OR ‘NO’ to Opt Out"
)


def ask(kind="text", options=(), **profile):
    return Resolver(Profile(**profile), None, None).talent_pool_opt_out(
        {
            "id": "t",
            "label": LABEL,
            "type": kind,
            "required": True,
            "options": list(options),
            "value": "",
            "valid": True,
        }
    )


def test_text_field_opts_out():
    a = ask()
    assert a.value in {"NO", "No"} and a.evidence_ids == ["policy:marketing"]


def test_select_picks_the_single_no():
    assert ask("select", ["Yes", "No"]).value == "No"
    assert ask("select", ["Maybe", "Later"]) is None


def test_unrelated_question_is_ignored():
    r = Resolver(Profile(), None, None)
    assert (
        r.talent_pool_opt_out(
            {
                "id": "x",
                "label": "Are you legally authorized to work in the US?",
                "type": "select",
                "required": True,
                "options": ["Yes", "No"],
                "value": "",
                "valid": True,
            }
        )
        is None
    )


def test_profile_may_join_talent_communities():
    join = {"answer_rules": {"join_talent_communities": True}}
    assert ask(**join).value in {"YES", "Yes"}
    assert ask("select", ["Yes", "No"], **join).value == "Yes"
    assert ask("checkbox", **join).value == "true"
    assert ask("checkbox").value == "false"
