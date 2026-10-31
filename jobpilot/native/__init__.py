"""The native app: one command, one process tree, no Docker (docs/NATIVE.md).

`axiom start` runs a small supervisor that owns PostgreSQL, the app and the Chromes, the way Compose owns the containers.
Everything in this package is standard library only: the supervisor lives as long as the installation does, and on a
16 GB Mac it must not cost what the things it supervises cost. Nothing here imports pydantic, SQLAlchemy or the app.
"""
