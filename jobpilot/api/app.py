"""The application factory: build the service, wire the middleware and mount one router per resource."""

from fastapi import FastAPI
from itsdangerous import URLSafeTimedSerializer

from ..config import Settings, settings
from ..db import Database
from ..service import Service
from . import middleware
from .lifespan import lifespan
from .routes import (
    accounts,
    archive,
    demo,
    frontend,
    health,
    job_board,
    jobs,
    knowledge,
    mail,
    owner,
    profile,
    resumes,
    runs,
    session,
    settings as owner_settings,
    sources,
    state,
)
from .snapshot import SnapshotCache


def create_app(config: Settings | None = None):
    config = config or settings()
    config.prepare()
    db = Database(config.database_url)
    service = Service(db, config)
    signer = URLSafeTimedSerializer(config.app_token, salt="applypilot-session-v1")
    cache = SnapshotCache(service)

    app = FastAPI(
        title="ApplyPilot Studio",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = service
    middleware.install(app, config, signer, cache)

    app.include_router(mail.router(service))
    app.include_router(job_board.router(service))
    from ..features import install as install_features

    install_features(app, service)
    # Owner sign-up/sign-in and onboarding (2026-10-15); routes before the frontend catch-all.
    app.include_router(owner.router(service, signer, config.secure_cookies))

    app.include_router(health.router(service))
    app.include_router(session.router(service, signer))
    app.include_router(state.router(service, cache))
    app.include_router(archive.router(service))
    app.include_router(profile.router(service))
    app.include_router(owner_settings.router(service))
    app.include_router(jobs.router(service))
    app.include_router(runs.router(service))
    app.include_router(knowledge.router(service))
    app.include_router(accounts.router(service))
    app.include_router(resumes.router(service))
    app.include_router(sources.router(service))
    app.include_router(demo.router(service))
    frontend.mount_assets(app)
    app.include_router(frontend.router())
    return app
