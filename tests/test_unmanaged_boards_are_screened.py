"""A board source added outside the managed catalog was imported whole, unscreened and without a listing.

203 such boards stored 5,620 postings in one minute (2026-11-01): every role, in every country. The autopilot reads only
listed postings, so none was ranked; 45 of them fit the owner's filters and were invisible. Any board the scanner can
read goes through it, whoever added the source."""

import pytest

from jobpilot.workers import BOARD_KINDS, screened_source


@pytest.mark.parametrize("kind", sorted(BOARD_KINDS))
def test_every_readable_board_is_scanned_with_the_owners_filters(kind):
    assert screened_source({"kind": kind, "auto_queue": False}, managed=False)


def test_other_sources_keep_the_plain_import_unless_managed():
    assert not screened_source({"kind": "career_page", "auto_queue": False}, managed=False)
    assert screened_source({"kind": "career_page", "auto_queue": False}, managed=True)
    assert screened_source({"kind": "career_page", "auto_queue": True}, managed=False)


def test_the_scanner_reads_every_board_kind_it_is_routed():
    import inspect

    from jobpilot import jobboard

    source = inspect.getsource(jobboard.read_feed)
    for kind in BOARD_KINDS:
        assert f'kind == "{kind}"' in source
