REQUIRED_ENDPOINTS = {
    "/api/v1/health": {"get"},
    "/api/v1/health/ready": {"get"},
    "/api/v1/users/me": {"get", "patch"},
    "/api/v1/sources": {"get", "post"},
    "/api/v1/sources/{source_id}": {"get", "patch", "delete"},
    "/api/v1/ingestion/jobs": {"get", "post"},
    "/api/v1/ingestion/articles": {"post"},
    "/api/v1/articles": {"get"},
    "/api/v1/articles/trending": {"get"},
    "/api/v1/articles/{article_id}": {"get"},
    "/api/v1/articles/{article_id}/related": {"get"},
    "/api/v1/articles/{article_id}/translations": {"get", "post"},
    "/api/v1/topics/tree": {"get"},
    "/api/v1/topics": {"post"},
    "/api/v1/topics/{topic_id}": {"patch"},
    "/api/v1/search": {"get"},
    "/api/v1/search/semantic": {"post"},
    "/api/v1/communities": {"get", "post"},
    "/api/v1/communities/{community_id}": {"get", "patch"},
    "/api/v1/communities/{community_id}/feed": {"get"},
    "/api/v1/feeds/personalized": {"get"},
    "/api/v1/feeds/following": {"get"},
    "/api/v1/library/state": {"get"},
    "/api/v1/library/saved": {"get"},
    "/api/v1/library/downloaded": {"get", "delete"},
    "/api/v1/library/history": {"get", "delete"},
    "/api/v1/library/{article_id}/saved": {"put"},
    "/api/v1/library/{article_id}/downloaded": {"put"},
    "/api/v1/library/{article_id}/read": {"post"},
    "/api/v1/follows/sources": {"get"},
    "/api/v1/follows/sources/{source_id}": {"put"},
    "/api/v1/feedback": {"get", "post"},
}


def test_openapi_exposes_required_endpoints():
    from app.main import app

    paths = app.openapi()["paths"]
    for path, methods in REQUIRED_ENDPOINTS.items():
        assert path in paths, f"Missing path {path}"
        assert methods.issubset(paths[path]), (
            f"Missing methods {methods - set(paths[path])} on {path}"
        )
