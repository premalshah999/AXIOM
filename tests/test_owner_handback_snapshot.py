"""Take over's hand-back binds to the fields and values actually on the page the owner handed back (synthetic fields)."""

from jobpilot.forms import FormSession


def _field(i, label, value, ftype="text"):
    return {"id": i, "label": label, "type": ftype, "value": value, "valid": True, "required": True, "slot": 0}


def _form(fields, title="Application", headings=("Your details",)):
    form = FormSession.__new__(FormSession)
    form.owner_hands = True
    form.owner_snapshot = None
    form.meta = {"headings": list(headings), "title": title}
    form.fields = {f["id"]: f for f in fields}
    form.expected, form.expected_slots, form.ledger, form.events = {}, {}, {}, []
    form.emit = lambda *a, **k: form.events.append(a[0])
    form.record_answer = lambda f, v, ev, verified=False: form.ledger.__setitem__(
        f["id"], {"answer": v, "evidence_ids": ev, "verified": verified}
    )
    return form


def _step(form, fields):
    form.fields = {f["id"]: f for f in fields}
    form.expected, form.expected_slots = {}, {}
    form.adopt_owner_values()


def test_owner_edits_on_the_handed_back_page_stay():
    name, city = _field("a", "Preferred name", "Sam"), _field("b", "City", "Springfield")
    form = _form([name, city])
    form.adopt_owner_values()
    assert form.expected == {"a": "Sam", "b": "Springfield"}
    assert form.owner_handed(name) and form.owner_handed(city)
    # The next pass over the same page keeps them.
    _step(form, [name, city])
    assert form.owner_handed(name) and form.owner_handed(city)


def test_same_titled_next_step_unseen_default_is_not_the_owners():
    name = _field("a", "Preferred name", "Sam")
    form = _form([name])
    form.adopt_owner_values()
    # Next step, same title and headings: the owner's field is still present, plus a new preselected personal question.
    default = _field("c", "Do you have a disability?", "No", "radio")
    _step(form, [name, default])
    assert form.owner_handed(name), "the owner's own value stays"
    assert not form.owner_handed(default), "an unseen default goes through answer resolution"
    assert "c" not in form.expected and "c" not in form.ledger


def test_same_titled_next_step_with_no_handed_back_field_revokes():
    form = _form([_field("a", "Preferred name", "Sam")])
    form.adopt_owner_values()
    other = _field("c", "Do you have a disability?", "No", "radio")
    _step(form, [other])
    assert form.owner_hands is None and not form.owner_handed(other)
    assert form.expected == {}


def test_a_value_changed_since_hand_back_is_not_the_owners():
    name = _field("a", "Preferred name", "Sam")
    form = _form([name])
    form.adopt_owner_values()
    _step(form, [dict(name, value="Someone else")])
    assert not form.owner_handed(dict(name, value="Someone else"))
