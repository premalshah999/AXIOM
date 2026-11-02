"""One headline figure per draft project (2026-11-02): the model wrote a [target: ...] in every bullet on 6 of 10 drafts, and each was
left out. Code keeps the first; a later one that ends its bullet goes, and one inside a sentence stays. Synthetic data only."""

from jobpilot.features.resumes.aggressive.quality import TARGET, one_headline


def test_a_second_target_that_ends_its_bullet_goes_and_its_period_stays():
    first = "Reads public pass schedules and ranks station options [target: p95 under 200 ms]."
    out = one_headline([first, "Checks each pass against outage windows [target: 95% of passes matched]."])
    assert out == [first, "Checks each pass against outage windows."]
    assert sum(len(TARGET.findall(b)) for b in out) == 1


def test_the_first_target_stays_wherever_it_is():
    out = one_headline(["Reads feeds across regions.", "Checks passes against windows [target: 95% matched]."])
    assert out[1].endswith("[target: 95% matched].")


def test_a_second_target_inside_a_sentence_is_left_for_the_check():
    bullets = ["Reads feeds [target: p95 under 200 ms].", "Checks passes, a [target: 95% matched] goal, for each review."]
    assert one_headline(bullets) == bullets  # Cutting it could break the sentence: the draft is left out as before.


def test_a_draft_with_one_target_or_none_is_untouched():
    for bullets in (["Reads feeds.", "Checks passes."], ["Reads feeds [target: p95 under 200 ms].", "Checks passes."]):
        assert one_headline(list(bullets)) == bullets
