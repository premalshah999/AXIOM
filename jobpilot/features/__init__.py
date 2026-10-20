"""Feature packages owned by the services track: knowledge, resumes, discovery.

Mount with one call from the application factory (see DEVELOPER.md):

    from .features import install as install_features
    install_features(app, service)

Routers are mounted under /api/v2/... inside the existing app, so the session
authentication, origin and JSON/multipart mutation checks apply unchanged.
"""


def install(app, service):
    from . import discovery, knowledge, resumes
    from ._store import create_tables

    create_tables(service.db.engine)  # additive: creates missing kn_/rs_/dc_ tables only
    for module in (knowledge, resumes, discovery):
        app.include_router(module.router(service))
