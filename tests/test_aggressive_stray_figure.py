"""A stray figure in a draft's bullet is reworded by the short fix, not a reason to leave the draft out (2026-11-02: 2 of 10 drafts
across five real postings, such as "3" outside the bracket). One in the title still is. Synthetic data only."""

from test_aggressive_pipeline import POSTING, proposals

from jobpilot.features.resumes.aggressive import quality
from jobpilot.features.resumes.aggressive.schema import BulletFix, ShapeFix


def drafts_with_a_stray_figure():
    ps = quality.normalize_proposals(proposals())
    shapes = [[(len(b) - 20, len(b) + 20) for b in p.bullets] for p in ps]
    ps[0].bullets[1] = ps[0].bullets[1].replace("before adding filters", "across 3 counties before adding filters")
    return ps, shapes


def test_the_bullet_holding_it_is_sent_to_the_fix_with_the_figure_named():
    ps, shapes = drafts_with_a_stray_figure()
    assert quality.proposal_problems(ps[0], [], POSTING) and not quality.blocking(ps[0], [], POSTING)
    asked = quality.shape_request(ps, shapes, [], POSTING)["bullets"]
    assert [(x["draft"], x["bullet"]) for x in asked] == [(0, 1)]
    assert asked[0]["change"].startswith('say it without the number or figure "3"') and asked[0]["change"].endswith("keep the length")
    assert asked[0]["aim_chars"] == asked[0]["chars"]


def test_a_wording_without_it_is_taken_and_the_draft_is_kept():
    ps, shapes = drafts_with_a_stray_figure()
    still = ps[0].bullets[1].replace("3 counties", "three counties")  # Spelled out is still a figure.
    clean = ps[0].bullets[1].replace("across 3 counties ", "across every county ")
    quality.apply_shape_fix(ps, shapes, ShapeFix(bullets=[BulletFix(draft=0, bullet=1, wordings=[still, clean])]), [], POSTING)
    assert ps[0].bullets[1] == clean
    kept, dropped = quality.keep_sound(ps, [], POSTING, shapes)
    assert ps[0] in kept and dropped == []


def test_with_no_clean_wording_the_draft_is_still_left_out():
    ps, shapes = drafts_with_a_stray_figure()
    before = ps[0].bullets[1]
    quality.apply_shape_fix(ps, shapes, ShapeFix(bullets=[BulletFix(draft=0, bullet=1, wordings=[before])]), [], POSTING)
    assert ps[0].bullets[1] == before
    kept, dropped = quality.keep_sound(ps, [], POSTING, shapes)
    assert ps[0] not in kept and 'no other number or figure ("3")' in dropped[0]["problems"][0]


def test_a_figure_in_the_title_is_not_sent_to_the_fix():
    ps, shapes = drafts_with_a_stray_figure()
    ps[0].title = "Explorer for 3 Counties"
    assert quality.blocking(ps[0], [], POSTING)
    assert all(x["draft"] != 0 for x in quality.shape_request(ps, shapes, [], POSTING)["bullets"])
