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


def test_custom_date_protocol_keeps_question_caption_and_separates_rows():
    """ICE, run 219a4917844749168505eab4946a466a, 2026-10-21 audit: rcf2023/2024 posts date triplets."""
    parts = [
        {**field("Start Date Month", "1_PersonProfileFields.rcf2023_Month", type_="select"), "group": "Work Experience 1"},
        {**field("Day", "1_PersonProfileFields.rcf2023_Date", type_="select"), "group": "Work Experience 1"},
        {**field("Year", "1_PersonProfileFields.rcf2023_Year"), "group": "Work Experience 1"},
        {**field("Start Date Month", "2_PersonProfileFields.rcf2023_Month", type_="select"), "group": "Work Experience 2"},
        {**field("Year", "2_PersonProfileFields.rcf2023_Year"), "group": "Work Experience 2"},
        field("Available Date Month", "rcf123_Month", type_="select"),
        field("Day", "rcf123_Date", type_="select"),
        field("Year", "rcf123_Year"),
    ]
    apply(parts, [])
    assert [f["label"] for f in parts[:3]] == ["Start Date Month", "Start Date Day", "Start Date Year"]
    assert all(f["date_parts"] == ["Day", "Month", "Year"] for f in parts[:3])
    assert all(f["date_parts"] == ["Month", "Year"] for f in parts[3:5])
    assert [f["label"] for f in parts[5:]] == ["Available Date Month", "Available Date Day", "Available Date Year"]


async def test_the_saved_login_needs_no_verified_answer(page, tmp_path):
    """Schwab 2026-10-21: read-back asked for "Login" ("Required field has no verified answer") on a signed-in profile."""
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    url = "https://career-schwab.icims.com/jobs/1/engineer/candidate"
    html = ("<html><body><a href='#'>Log Out</a><label>Login<input name='PersonProfileFields.Login' required "
            "value='saved1734895'></label><input type=submit value='Update Profile'></body></html>")

    async def serve(route):
        await route.fulfill(body=html, content_type="text/html")

    await page.route(url, serve)
    await page.goto(url)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF-1.4")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    await form.scan()
    report = await form.verify()
    assert not any("Login" in problem for problem in report["problems"])
    assert report["auth_required"] is False


async def test_acknowledgement_signature_boxes_are_named_as_signatures(service):
    """SAS, run 23585c8e6a9d452d8ff92c6986eefeb0, 2026-10-21: the acknowledgement packet's required "xSignature" and
    "Signature 1" boxes stayed empty, the browser held Submit, and nothing was sent."""
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    packet = "https://sas.icims.com/forms?isPacket=1&selected=candidate_ack_form_person_"
    fields = [
        {**field("xSignature", name="icims_f_signature", type_="checkbox", frame_url=packet), "id": "s0", "required": True},
        {**field("Signature 1", name="icims_f_Signature_1", type_="checkbox", frame_url=packet), "id": "s1", "required": True},
        {**field("Veteran", name="icims_f_Veteran", type_="checkbox", frame_url=packet), "id": "v"},
    ]
    apply(fields, [])
    assert [f["label"] for f in fields[:2]] == ["I acknowledge and sign this form electronically"] * 2
    assert fields[2]["label"] == "Veteran"
    resolver = Resolver(Profile(accept_all_application_terms=True), service.db, service.config,
                        job={"ats": "icims", "url": "https://careers-sas.icims.com/jobs/1/x/job"})
    for f in fields[:2]:
        f.update({"options": [], "value": "", "valid": True, "group": "", "section": ""})
    answers = await resolver.resolve(fields[:2])
    assert [(a.disposition, str(a.value).lower()) for a in answers] == [("answer", "true")] * 2


def test_a_length_the_server_refused_becomes_the_fields_limit():
    """Quest, run 362bd634a1d44f58a4c76854f90a5b27, 2026-10-21: no maxlength attribute; the server refused the answer
    three times with "Your answer is over the maximum length of 100 characters"."""
    question = "Will you now or in the future require employer sponsorship or support to obtain or maintain authorization to work in Canada?"
    fields = [
        {**field(question, name="Q1234", type_="textarea"), "maxlength": -1},
        {**field("Do you have experience with Microsoft Azure?", name="Q1235", type_="textarea"), "maxlength": -1},
    ]
    text = ("Error: Invalid Data Error. Please check all information before trying again. " + question
            + ": Your answer is over the maximum length of 100 characters. Do you have experience with Microsoft A")
    apply(fields, [], text)
    assert fields[0]["maxlength"] == 100 and fields[1]["maxlength"] == -1


FORM = "https://cooperhealth.icims.com/forms?module=Forms&action=showForm&form=OFCCP_Pre&portalid=17"


def test_the_forms_module_application_is_named_by_protocol():
    """Cooper Health's employment application (2026-10-22): every caption read as the bare field name, the employer
    rows were not history rows, and the run stopped on "ICertify; Month; Day; Emp1SupName"."""
    fields = [
        field("ICertify", "icims_f_ICertify", type_="checkbox", frame_url=FORM),
        field("Work In US status", "icims_f_Work_In_US", type_="radio", frame_url=FORM),
        field("Are you age 18 or over?", "icims_f_Over_18", type_="radio", frame_url=FORM),
        field("LastName", "icims_f_LastName", frame_url=FORM),
        field("EmpName1", "icims_f_EmpName1", frame_url=FORM),
        field("EmpCity1", "icims_f_EmpCity1", frame_url=FORM),
        field("Emp1SupName", "icims_f_Emp1SupName", frame_url=FORM),
        field("JobTitle2", "icims_f_JobTitle2", frame_url=FORM),
        field("MayWeContact", "icims_f_mayWeContact", type_="select", frame_url=FORM),
        field("SchoolMajor", "icims_f_SchoolMajor", frame_url=FORM),
        field("Month", "icims_f_Emp1From_Month", type_="select", frame_url=FORM),
        field("Day", "icims_f_Emp1From_Date", type_="select", frame_url=FORM),
        field("Year", "icims_f_Emp1From_Year", frame_url=FORM),
        field("Month", "icims_f_DateAvailableToStart_Month", type_="select", frame_url=FORM),
        field("Veteran status", "icims_f_Veteran", type_="radio", frame_url=FORM),
    ]
    apply(fields, [])
    named = {f["name"]: (f["label"], f.get("group", "")) for f in fields}
    assert named["icims_f_ICertify"][0].startswith("I certify")
    assert named["icims_f_Work_In_US"][0] == "Are you legally authorized to work in the United States?"
    assert named["icims_f_Over_18"][0] == "Are you age 18 or over?"  # the employer's caption is kept
    assert named["icims_f_LastName"] == ("Last Name", "")
    assert named["icims_f_EmpName1"] == ("Employer Name", "Work Experience 1")
    assert named["icims_f_EmpCity1"] == ("City", "Work Experience 1")
    assert named["icims_f_Emp1SupName"] == ("Supervisor Name", "Work Experience 1")
    assert named["icims_f_JobTitle2"] == ("Job Title", "Work Experience 2")
    assert named["icims_f_mayWeContact"] == ("May we contact this reference?", "Work Experience 1")
    assert named["icims_f_SchoolMajor"] == ("Major", "Education 1")
    assert [named[f"icims_f_Emp1From_{p}"] for p in ("Month", "Date", "Year")] == [
        ("Start Date Month", "Work Experience 1"), ("Start Date Day", "Work Experience 1"),
        ("Start Date Year", "Work Experience 1")]
    assert named["icims_f_DateAvailableToStart_Month"][0] == "Date Available To Start Month"
    assert named["icims_f_Veteran"][0] == "Veteran status"


def test_a_bare_date_beside_the_signature_is_todays_date(service):
    """Schwab (2026-10-23): the self-identification packet's Name, Date (Month/Day/Year) and Signature; "Date Year"
    went to the owner as a question."""
    from datetime import date

    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    packet = "https://schwab.icims.com/forms?isPacket=1&selected=Voluntary_Self_Identification"
    fields = [
        field("Month", "icims_f_Date_Month", type_="select", frame_url=packet),
        field("Day", "icims_f_Date_Date", type_="select", frame_url=packet),
        field("Year", "icims_f_Date_Year", frame_url=packet),
        field("Signature", "icims_f_signature", type_="checkbox", frame_url=packet),
    ]
    apply(fields, [])
    assert [f["label"] for f in fields[:3]] == ["Today's Date Month", "Today's Date Day", "Today's Date Year"]
    resolver = Resolver(Profile(), service.db, service.config)
    year = resolver.local({**fields[2], "id": "y", "options": [], "required": True})
    assert year.value == str(date.today().year)
