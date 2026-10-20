"""A required narrative always fits the site's character limit (RA Capital, 255 characters)."""

from jobpilot.features.knowledge.writing import fit_length


def test_whole_sentences_within_limit():
    text = ("I build data pipelines that analysts trust. " * 3) + "This role fits that work closely."
    out = fit_length(text, 100)
    assert len(out) <= 100 and out.endswith(".") and out.count("I build") == 2


def test_single_long_sentence_ends_on_a_word():
    out = fit_length("word " * 80, 60)
    assert len(out) <= 60 and out.endswith(".") and "  " not in out


def test_short_text_is_unchanged():
    assert fit_length("Short answer.", 255) == "Short answer."
