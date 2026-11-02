"""Hook for the shared send guard: approvals cannot outlive project choices."""

from . import store


def send_problem(session, artifact):
    validation = artifact.validation or {}
    report = validation.get("report") or {}
    if (
        artifact.policy_version != "tailor-aggressive-v1"
        and validation.get("mode") != "aggressive"
        and not report.get("workspace_id")
    ):
        return None
    workspace_id = report.get("workspace_id")
    workspace = session.get(store.Workspace, workspace_id) if workspace_id else None
    if (
        workspace is None
        or workspace.revision != report.get("workspace_revision")
        or workspace.source_sha != artifact.source_resume_sha
    ):
        return (
            "tailoring_stale",
            "Your project choices changed. Press Update project PDF, then review it before applying.",
        )
    selected = set(workspace.payload["selected_drafts"])
    if any(d["id"] in selected and not store.confirmed(d) for d in workspace.payload["drafts"]):
        return (
            "tailoring_unconfirmed",
            "Approve the selected project drafts, or use your original projects, before applying.",
        )
    return None
