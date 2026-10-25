"""A localized phone country code is read back in the site's language. SmartRecruiters' German form showed the
committed US code as "Vereinigte Staaten + 1" under "Ländercode"; only English labels were compared as calling codes,
so every Sopra Steria run looped on "Value not accepted: Ländercode" (2026-10-25)."""

import pytest

from jobpilot.widgets import same_value


@pytest.mark.parametrize("label", ["Ländercode", "Landesvorwahl", "Indicatif pays", "Código de país"])
@pytest.mark.parametrize("expected", ["United States +1", "+1"])
def test_a_localized_calling_code_reads_back_as_the_committed_code(label, expected):
    field = {"type": "combobox", "label": label, "group": "Telefonnummer *"}
    assert same_value(field, "Vereinigte Staaten + 1", expected)


def test_another_country_with_the_same_code_is_still_refused():
    field = {"type": "combobox", "label": "Ländercode"}
    assert not same_value(field, "Kanada + 1", "United States +1")
