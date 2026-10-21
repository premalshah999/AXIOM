from jobpilot.icims_protocol import apply, protocol_name

PROFILE = "https://career-schwab.icims.com/jobs/127949/associate-software-engineer/candidate?in_iframe=1"


def field(label, name="", html_id="", value="", type_="text", frame_url=PROFILE):
    return {"label": label, "name": name, "html_id": html_id, "value": value, "type": type_, "frame_url": frame_url}


def test_names_drop_row_prefixes_and_read_search_menu_anchors():
    assert protocol_name(field("City", "1745957_PersonProfileFields.AddressCity")) == "PersonProfileFields.AddressCity"
    assert protocol_name(field("Year", "-1_CandProfileFields.GraduationDate_Year")) == "CandProfileFields.GraduationDate_Year"
    # iCIMS search menus are scanned as their anchor, which carries the select's id and no name.
    anchor = field("Country", html_id="1745957_PersonProfileFields.AddressCountry_icimsDropdown", type_="combobox")
    assert protocol_name(anchor) == "PersonProfileFields.AddressCountry"


def test_a_date_in_three_controls_is_named_by_protocol_not_by_its_printed_labels():
    # Schwab 2026-10-21 printed "Month", "Day" and "Year" (or "Graduation Date Year") under one caption.
    parts = [
        field("Month", "-1_CandProfileFields.GraduationDate_Month", type_="select"),
        field("Day", "-1_CandProfileFields.GraduationDate_Date", type_="select"),
        field("Graduation Date Year", "-1_CandProfileFields.GraduationDate_Year"),
    ]
    apply(parts, [])
    assert [f["label"] for f in parts] == ["Graduation Date Month", "Graduation Date Day", "Graduation Date Year"]
    assert all(f["date_parts"] == ["Day", "Month", "Year"] for f in parts)


def test_a_signed_in_profile_keeps_its_saved_login():
    # Schwab's signed-in candidate profile shows the saved login and a masked password under "Create a login".
    fields = [
        field("Login", "PersonProfileFields.Login", value="premashah1734895"),
        field("Password", "PersonProfileFields.Password", value="********", type_="password"),
        field("Password (Re-enter)", "PersonProfileFields.Password_Confirm", value="********", type_="password"),
        field("Email", "PersonProfileFields.Email", value="a@example.com", type_="email"),
    ]
    apply(fields, [{"label": "Log Out"}, {"label": "Update Profile"}])
    assert [bool(f.get("site_account")) for f in fields] == [True, True, True, False]


def test_a_new_profile_still_asks_for_its_login():
    # Iridium's new-candidate profile: empty account fields and a "Log back in!" link, no session.
    fields = [
        field("Login", "PersonProfileFields.Login"),
        field("Password", "PersonProfileFields.Password", type_="password"),
    ]
    apply(fields, [{"label": "Log back in!"}, {"label": "Submit Profile"}])
    assert not any(f.get("site_account") for f in fields)
    signed_in_but_empty = [field("Password", "PersonProfileFields.Password", type_="password")]
    apply(signed_in_but_empty, [{"label": "Log Out"}])
    assert not signed_in_but_empty[0].get("site_account")


def test_other_platforms_are_untouched():
    other = field("Year", "GraduationDate_Year", frame_url="https://boards.greenhouse.io/acme/jobs/1")
    apply([other], [{"label": "Log Out"}])
    assert other["label"] == "Year" and "icims_name" not in other
