"""Normal mode (placeholder until its pipeline lands in the next commit)."""

from .modes import ModeSpec


async def run(job, source, profile, ctx):
    raise NotImplementedError


SPEC = ModeSpec("normal", "Normal", "A summary for this job, and your real keywords placed where they fit.", run)
