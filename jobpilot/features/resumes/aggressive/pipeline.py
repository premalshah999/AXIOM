"""Resume 2 Steps 1–7 through AXIOM's model, compiler and shared rules.

Unconfirmed projects belong in the PREVIEW, per the owner's spec. check_rules
still marks that document unsendable. The shared artifact runner stores the
preview for review and checks the send decision separately.
"""

import json
import re
from pathlib import Path

from .. import ats, gates
from ..editor import Section, render, tailored_style
from ..layout import fit
from ..modes import Draft, ModeResult, ModeSpec
from ..rules import bullet_window, check_rules, live_bullets, live_entries
from ..validate import parse_check
from . import prompts, quality, store
from .schema import Brief, Critique, Plan, Revision, ShapeFix


def payload(source, profile, ctx, hard_text, brief, history, allowed):
    return {
        "brief": brief.model_dump(),
        "hard_reqs_text": hard_text,
        "source": source.doc.model_dump(),
        "allowed_skills": allowed,
        "bullet_windows": [
            {
                "id": b.id,
                "source": b.text,
                "min_chars": bullet_window(b.text)[0],
                "max_chars": bullet_window(b.text)[1],
            }
            for s in source.doc.sections
            if s.include and s.kind == "projects"
            for e in live_entries(s)
            for b in live_bullets(e)
        ],
        "recent_applications": history,
        "do_not_use_outside_proposals": [
            r["term"] for r in ctx.ats_source.get("keywords", []) if r.get("status") == "needed"
        ],
    }


def shape_brief(slots, shapes):
    """What a model needs to write a draft at the size of the project it replaces: the exact numbers for each bullet."""
    return [
        {
            "replaces": entry.heading or entry.subheading or entry.id,
            "bullets": [{"min_chars": low, "max_chars": high, "aim_chars": (low + high) // 2} for low, high in shape],
        }
        for entry, shape in zip(slots, shapes)
    ]


def sanitize_plan(plan):
    plan = plan.model_copy(deep=True)
    for p in plan.proposals:
        p.title = gates.sanitize(p.title)
        p.bullets = [gates.sanitize(b) for b in p.bullets]
        p.build_plan = [gates.sanitize(b) for b in p.build_plan]
    return plan


def with_projects(doc, workspace):
    doc = doc.model_copy(deep=True)
    removed = set(workspace["removed_projects"])
    for s in doc.sections:
        if s.kind == "projects":
            for e in s.entries:
                if e.id in removed:
                    e.include = False
    selected = [d for d in workspace["drafts"] if d["id"] in workspace["selected_drafts"]]
    leading = []
    for d in selected:
        # A draft sits where the project it replaces sat. One that replaces nothing leads the section.
        home = next(
            (s for s in doc.sections if s.kind == "projects" and any(e.id == d.get("replaces") for e in s.entries)),
            None,
        )
        if home is None:
            leading.append(store.entry(d))
        else:
            at = next(i for i, e in enumerate(home.entries) if e.id == d["replaces"])
            home.entries.insert(at, store.entry(d))
    if leading:
        section = next((s for s in doc.sections if s.include and s.kind == "projects"), None)
        if section is None:
            section = Section(id="aggressive-projects", title="Projects", kind="projects")
            doc.sections.append(section)
        section.entries = leading + section.entries
    return doc, selected


def rules_for(doc, source, selected, workspace, allowed, pdf=None, preview=False):
    return check_rules(
        doc,
        source.doc,
        pdf=pdf,
        allowed_skills=allowed,
        added_projects=[store.entry(d) for d in selected if preview or store.confirmed(d)],
        removed_projects=workspace["removed_projects"],
        unconfirmed=[] if preview else [d for d in selected if not store.confirmed(d)],
    )


async def compile_and_check(doc, source, ctx, selected, workspace, allowed, style):
    checks = []

    async def compile_checked(tex, **kwargs):
        result = await ctx.compile_fn(tex, **kwargs)
        if result.ok:
            parsed = parse_check(result.pdf, doc)
            checks.append({k: v for k, v in parsed.items() if k != "text"})
            if not parsed["ok"]:
                raise RuntimeError(
                    "ats_parse: " + "; ".join(i["label"] for i in parsed["items"] if not i["ok"])
                )
        return result

    fitted = await fit(doc, style, compile_checked)
    compiled = fitted["compiled"]
    if not compiled.ok:
        raise RuntimeError(f"{compiled.code}: {compiled.detail}")
    rules = rules_for(doc, source, selected, workspace, allowed, compiled.pdf, preview=True)
    if not rules["ok"]:
        raise RuntimeError("rules: " + "; ".join(p["message"] for p in rules["problems"][:4]))
    parsed = parse_check(compiled.pdf, doc)
    # A fresh model receives exactly what the recruiter sees, never the brief.
    from ..master import layout_text

    return {
        "pdf": compiled.pdf,
        "style": fitted["style"],
        "latex": render(doc, fitted["style"]),
        "parsed": parsed,
        "layout_text": layout_text(compiled.pdf, parsed["text"]),
        "rules": rules,
        "gap_pt": fitted["gap_pt"],
        "overflow": fitted["overflow"],
        "compiles": fitted["compiles"],
        "checks": checks,
    }


async def run(job, source, profile, ctx):
    if ctx.model is None:
        raise RuntimeError("no_model: Add your AI key in Settings > Connections")
    if not job.get("description", "").strip():
        raise RuntimeError("missing_description: This posting has no description")
    calls, attempts, usage = 0, [], []

    async def ask(kind, system, data):
        nonlocal calls
        from ....models import COST_METER

        calls += 1
        meter = COST_METER.get()
        token = None
        if meter is None:
            meter = []
            token = COST_METER.set(meter)
        start = len(meter)
        try:
            return await ctx.model(kind, system, json.dumps(data, ensure_ascii=False))
        finally:
            usage.extend(meter[start:])
            if token is not None:
                COST_METER.reset(token)

    blocks = ats.blocks(job["description"])
    hard = "\n".join(filter(None, [blocks["required"], blocks["duties"]]))
    brief = await ask(
        Brief,
        prompts.BRIEF,
        {"job_description": job["description"], "hard_reqs_text": hard, "title": job.get("title", "")},
    )
    # The model cannot smuggle a culture keyword into the coverage denominator.
    keywords = []
    for keyword in brief.hard_keywords:
        match = quality.literal_match(hard, keyword)
        if match and match.group(0).casefold() not in {k.casefold() for k in keywords}:
            keywords.append(match.group(0))
    for term in ctx.ats_terms:
        if len(keywords) >= 5:
            break
        match = quality.literal_match(hard, term["term"])
        if match and match.group(0).casefold() not in {k.casefold() for k in keywords}:
            keywords.append(match.group(0))
    brief.hard_keywords = keywords[:10]
    base, extra = quality.inventory(source.doc, profile, ctx.evidence)
    allowed = list(dict.fromkeys([*base, *extra]))
    history = store.recent(ctx.db, job)
    slots = quality.project_slots(source.doc)
    shapes = quality.project_shapes(source.doc)
    data = payload(source, profile, ctx, hard, brief, history, allowed)
    if shapes:
        data["project_shapes"] = shape_brief(slots, shapes)
    plan = sanitize_plan(await ask(Plan, prompts.WRITE, data))
    plan.proposals = quality.normalize_proposals(plan.proposals, shapes)
    forbidden = [r for r in ctx.ats_source.get("keywords", []) if r.get("status") == "needed"]
    real, refused = quality.apply_revision(source.doc, plan, allowed, hard, forbidden)
    posting = job["description"]
    issues = quality.proposal_issues(plan.proposals, history, posting, shapes)
    dropped = []
    # Lengths alone need no new plan: the short fix below gives those bullets their exact numbers, for far fewer tokens.
    if refused or set(issues) - set(quality.shape_issues(plan.proposals, shapes)):
        attempts.append({"stage": "generation", "refused_edits": refused, "proposal_issues": issues})
        plan = sanitize_plan(
            await ask(
                Plan,
                prompts.REPAIR,
                {**data, "previous_plan": plan.model_dump(), "issues": issues, "refused_edits": refused},
            )
        )
        plan.proposals = quality.normalize_proposals(plan.proposals, shapes)
        real, refused = quality.apply_revision(source.doc, plan, allowed, hard, forbidden)
    request = quality.shape_request(plan.proposals, shapes, history, posting)
    if request["bullets"]:
        # One bounded call. It is a second chance, so a failed one only leaves those drafts out.
        step = {"stage": "shape_repair", "issues": quality.shape_issues(plan.proposals, shapes)}
        attempts.append(step)
        try:
            fix = await ask(ShapeFix, prompts.SHAPE, request)
            quality.apply_shape_fix(plan.proposals, shapes, fix, history, posting)
            step["left"] = quality.shape_issues(plan.proposals, shapes)
        except Exception as exc:
            step["error"] = type(exc).__name__
    kept = plan.proposals
    if quality.proposal_issues(plan.proposals, history, posting, shapes):
        # Only the drafts that pass every check go on: with none left, the real projects carry the resume.
        kept, dropped = quality.keep_sound(plan.proposals, history, posting, shapes)
        attempts.append({"stage": "drafts_left_out", "drafts": dropped})
    # A draft takes the place of the project at its position, and a draft left out leaves that project where it is.
    # "Use only my original projects" brings them all back. Refused factual/length edits stay at source.
    still = {id(p) for p in kept}
    replaces = [entry.id for p, entry in zip(plan.proposals, slots) if id(p) in still]
    plan.proposals = kept
    workspace = store.create(ctx.db, job, source, profile, plan.proposals, brief, replaces=replaces)
    # A workspace can be an earlier run's, so its drafts say which sizes they keep.
    places = {entry.id: (entry, shape) for entry, shape in zip(slots, shapes)}
    held = [places[d["replaces"]] for d in workspace["drafts"] if d.get("replaces") in places]
    if len(held) != len(workspace["drafts"]):
        held = []
    draft_shapes = [shape for _, shape in held]
    doc, selected = with_projects(real, workspace)
    result = await compile_and_check(
        doc, source, ctx, selected, workspace, allowed, tailored_style(source.style)
    )
    before = quality.coverage(result["parsed"]["text"], brief.hard_keywords)
    critique = await ask(
        Critique,
        prompts.CRITIC,
        {"job_description": job["description"], "resume_text": result["layout_text"]},
    )
    fixes = critique.line_fixes + critique.what_would_change_the_verdict
    unresolved = list(critique.missing_requirements)
    fix_applied = False
    if fixes:
        revision = await ask(
            Revision,
            prompts.FIX,
            {
                **data,
                "project_shapes": shape_brief([entry for entry, _ in held], draft_shapes),
                "current_plan": plan.model_dump(),
                "drafts": workspace["drafts"],
                "critique": critique.model_dump(),
                "priority_fixes": fixes,
            },
        )
        revised_workspace = workspace
        if workspace["drafts"] and len(revision.proposals) == len(workspace["drafts"]):
            # Sanitize before re-running every proposal gate. The original
            # draft's bullet count is the fixed budget for critique edits.
            revised = sanitize_plan(Plan(proposals=revision.proposals)).proposals
            revised = quality.normalize_proposals(revised, draft_shapes)
            proposal_errors = quality.proposal_issues(revised, history, posting, draft_shapes)
            if [len(p.bullets) for p in revised] != [len(p["bullets"]) for p in workspace["drafts"]]:
                proposal_errors.append("Critique changes a draft project's bullet count")
            if proposal_errors:
                unresolved.extend(proposal_errors)
            else:
                # Only save after compile and coverage accept the correction.
                revised_workspace = store.revise(None, workspace, revised)
        # A partial correction retains previous accepted edits, keyed by ID.
        edits = {e.id: e for e in plan.bullets}
        edits.update({e.id: e for e in revision.bullets})
        merged = Revision(
            bullets=list(edits.values()),
            project_order=revision.project_order or plan.project_order,
            categories=revision.categories or plan.categories,
        )
        changed, fix_refused = quality.apply_revision(source.doc, merged, allowed, hard, forbidden)
        candidate, revised_selected = with_projects(changed, revised_workspace)
        after = await compile_and_check(
            candidate, source, ctx, revised_selected, revised_workspace, allowed, result["style"]
        )
        after_coverage = quality.coverage(after["parsed"]["text"], brief.hard_keywords)
        regression = (
            before["keyword_coverage"] is not None
            and after_coverage["keyword_coverage"] < before["keyword_coverage"]
        )
        if regression:
            unresolved.append("Critique edits were reverted because PDF hard-keyword coverage regressed")
        else:
            fix_applied = candidate.model_dump() != doc.model_dump()
            doc, result = candidate, after
            if not fix_applied:
                unresolved.append(
                    "The critique requested changes, but no supported wording change passed the gates."
                )
            if revised_workspace is not workspace:
                workspace = store.revise(ctx.db, workspace, revised)
                selected = revised_selected
        attempts.append(
            {"stage": "critique_fixes", "refused_edits": fix_refused, "coverage_regressed": regression}
        )
        unresolved += [f["reason"] for f in fix_refused]
    final_coverage = quality.coverage(result["parsed"]["text"], brief.hard_keywords)
    send_rules = rules_for(doc, source, selected, workspace, allowed, result["pdf"])
    quality_report = gates.quality(doc, source.doc)
    # Immutable source wording can have style warnings, but a generated edit may
    # never introduce a critical first-person issue.
    source_quality = gates.quality(source.doc)
    introduced = set(quality_report["critical"]) - set(source_quality["critical"])
    if introduced:
        raise RuntimeError("quality: " + "; ".join(sorted(introduced)))
    name = re.sub(r"[^\w -]", "", source.doc.name).strip().replace(" ", "_") or "Applicant"
    path = Path(ctx.work_dir) / f"{name}_Resume.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result["pdf"])
    report = {
        "workspace_id": workspace["id"],
        "workspace_revision": workspace["revision"],
        "brief": brief.model_dump(),
        "hard_reqs_text": hard,
        "steps": [
            "dream_candidate_brief",
            "three_project_archetypes",
            "skills_inventory",
            "quality_gates_and_sanitization",
            "compile_twice_and_page_fill",
            "ats_parse",
            "blind_recruiter_critique",
            "apply_fixes_and_recheck",
        ],
        "source_skills": base,
        "allowed_additions": extra,
        "quality": quality_report,
        "deterministic_repairs": attempts,
        "refused_edits": refused,
        "drafts_left_out": dropped,
        "critique": critique.model_dump(),
        "critique_text": json.dumps(critique.model_dump(), ensure_ascii=False, indent=2),
        "critique_fixes_applied": fix_applied,
        "unresolved": unresolved,
        "coverage_before_critique": before,
        **final_coverage,
        "parse": {k: v for k, v in result["parsed"].items() if k != "text"},
        "page_fill": {
            "gap_pt": result["gap_pt"],
            "compiles": result["compiles"],
            "overflow": result["overflow"],
        },
        "overflow_message": f"This resume runs to {result['parsed']['pages']} pages. Nothing was cut to fit."
        if result["overflow"]
        else "",
        "preview_rules": result["rules"],
        "send_rules": send_rules,
        "requires_project_approval": any(not store.confirmed(d) for d in selected),
        "removed_projects": workspace["removed_projects"],
        "history_checked": len(history),
        "model_calls": calls,
        "cost": {
            "actual_usd": round(sum(u["usd"] for u in usage), 8) if usage else None,
            "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usage),
            "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usage),
            "metered_requests": len(usage),
            "note": "Actual usage from the app's model meter."
            if usage
            else "No billed usage recorded (stub or unmetered model).",
        },
    }
    report["tailored_doc"] = real.model_dump()
    # If critique corrections were accepted, persist their real-project wording.
    if fix_applied:
        report["tailored_doc"] = changed.model_dump()
    report["style"] = result["style"].model_dump()
    store.save_report(ctx.db, workspace["id"], workspace["revision"], report)
    return ModeResult(
        latex=result["latex"],
        pdf_path=path,
        doc=doc,
        style=result["style"],
        allowed_skills=allowed,
        added_projects=[store.entry(d) for d in selected if store.confirmed(d)],
        preview_projects=[store.entry(d) for d in selected if not store.confirmed(d)],
        removed_projects=workspace["removed_projects"],
        drafts=[
            Draft(id=d["id"], title=d["title"], bullets=d["bullets"])
            for d in selected
            if not store.confirmed(d)
        ],
        report=report,
        model_calls=calls,
    )


def estimate_tokens(resume_chars, posting_chars):
    # Three required calls (brief/write/blind critic), one revision, one bounded repair and the far smaller
    # length fix. Includes schema overhead; the actual meter wins after execution.
    return int(11500 + resume_chars * 1.5 + posting_chars * 1.3), 7000


SPEC = ModeSpec(
    "aggressive",
    "Aggressive",
    "Job-specific proof points, project drafts, and an independent recruiter critique.",
    run,
    estimate_tokens=estimate_tokens,
    timeout_seconds=420,
)
