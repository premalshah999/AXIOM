"""A saved iCIMS profile carried a second phone row ("Delete Phones (2)"). The applicant has one phone, so both rows
were filled with the same Mobile number and every Update Profile came back "An unexpected server error occurred"
(Emory, 2026-10-24..27). The extra rows are deleted, last first, and their confirmation is accepted only for that
click."""

from unittest.mock import AsyncMock

from support_icims import adapter
from test_icims_profile_submit import page, profile

PROFILE = "https://careers-acme.icims.com/jobs/42/java-developer/candidate?mode=apply&apply=yes"


def saved_profile(*labels):
    controls = [{"id": label, "label": label, "href": ""} for label in labels]
    return page(PROFILE, [{"id": "first", "type": "text", "label": "First Name", "required": True}], controls,
                "Candidate Profile")


async def test_extra_phone_and_address_rows_are_deleted_last_first(service):
    obs = saved_profile("Delete Phones (2)", "Delete Phones (3)", "Add More (Phones)", "Delete Addresses (2)",
                        "Update Profile")
    value = adapter(service, obs)
    value.form.settle = AsyncMock()
    confirms = []
    value.click = AsyncMock(side_effect=lambda control, **kw: confirms.append(value.e.accept_confirms))
    value.emit = lambda *args: None
    assert await value.drop_extra_contact_rows(obs) is True
    assert [c.args[0]["label"] for c in value.click.await_args_list] == [
        "Delete Phones (3)", "Delete Phones (2)", "Delete Addresses (2)"
    ]
    assert confirms == [1, 1, 1]
    assert value.e.accept_confirms == 0


async def test_a_single_row_profile_is_left_alone(service):
    obs = saved_profile("Add More (Phones)", "Update Profile")
    value = adapter(service, obs)
    assert await value.drop_extra_contact_rows(obs) is False
    value.click.assert_not_awaited()


async def test_rows_that_come_back_are_not_deleted_forever(service):
    obs = saved_profile("Delete Phones (2)")
    value = adapter(service, obs)
    value.form.settle = AsyncMock()
    value.emit = lambda *args: None
    assert await value.drop_extra_contact_rows(obs) is True
    assert await value.drop_extra_contact_rows(obs) is True
    assert await value.drop_extra_contact_rows(obs) is False


def test_the_plain_profile_fixture_has_no_extra_rows():
    assert not [c for c in profile()["controls"] if "Delete" in c["label"]]
