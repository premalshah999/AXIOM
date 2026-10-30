"""Applicant evidence for the page navigator: the confirmed facts and the retrieved knowledge for one page's questions.

(The model-led agent that once owned a whole application with one conversation was retired on 2026-10-14 and
deleted; the navigator in browser.py reads these helpers.)
"""


def evidence_context(resolver):
    from .answer_quality import context, SECRET

    facts, evidence, reviews = context(resolver)
    facts = {k: v for k, v in facts.items() if not SECRET.search(k)}
    return facts, evidence, reviews


def retrieve(form, field_ids):
    from .answer_quality import SECRET

    packets = {}
    for fid in field_ids:
        field = form.fields.get(fid)
        if not field or field["type"] in {"password", "file"} or SECRET.search(field["label"]):
            continue
        packet = form.resolver.question_context(field)
        packets[fid] = {
            "question": packet.question.model_dump(),
            "conflicts": packet.conflicts,
            "truncated": packet.truncated,
            "items": [
                {**item.model_dump(), "reference": "knowledge:" + item.fact_id}
                for item in packet.items
                if not SECRET.search(item.question)
            ],
        }
    return packets


def navigation_context(form, observation):
    """Bring current-question evidence to the owner before it chooses a tool."""
    from .answer_context import relevant_context
    from .answer_quality import SECRET
    from .prompt_layout import canonical, without_defaults

    fields = [
        f
        for f in observation.get("fields", [])
        if f.get("required")
        and f.get("type") not in {"file", "password"}
        and not SECRET.search(f.get("label", ""))
    ]
    fields.sort(key=lambda f: (bool(f.get("valid", True)), bool(f.get("value"))))
    # A full screening page commonly has more than six questions. Retrieve a
    # page-sized batch now; the owner should not spend a model turn discovering
    # that a supported fact exists for its seventh field.
    remaining = fields[20:]
    fields = fields[:20]
    facts, _, _ = evidence_context(form.resolver)
    facts = {
        key: (
            {k: v for k, v in value.items() if k != "description"}
            if key.startswith("profile:work.") and isinstance(value, dict)
            else value
        )
        for key, value in facts.items()
    }
    selected, _, _ = relevant_context(fields, facts, {}, form.resolver.job)
    packets = retrieve(form, [f["id"] for f in fields])
    knowledge = {}
    for packet in packets.values():
        items = packet.pop("items")
        packet["references"] = [item["reference"] for item in items]
        # The reference is the key; the id, empty scope fields and default flags are not facts (a False value stays).
        knowledge.update(
            {
                item["reference"]: without_defaults({k: v for k, v in item.items() if k not in {"fact_id", "reference"}})
                for item in items
            }
        )
        if "question" in packet:
            packet["question"] = without_defaults(packet["question"])
        for flag in ("conflicts", "truncated"):
            if not packet.get(flag):
                packet.pop(flag, None)
    return {
        # Sorted: the same profile renders the same bytes on every step and run, so the provider reuses its cache.
        "facts": canonical(selected),
        "questions": packets,
        "knowledge": knowledge,
        "remaining_question_ids": [f["id"] for f in remaining],
    }
