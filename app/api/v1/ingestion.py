from typing import Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    Profile,
    get_article_repo,
    get_ingestion_service,
    get_job_repo,
    require_editor,
)
from app.repositories.articles import ArticleRepository
from app.repositories.ingestion_jobs import IngestionJobRepository
from app.schemas.article import ArticleDetail, ArticleIngest
from app.schemas.base import Page
from app.schemas.ingestion import IngestionJobCreate, IngestionJobOut
from app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post(
    "/jobs",
    response_model=IngestionJobOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run an RSS ingestion job for a source",
)
async def create_job(
    payload: IngestionJobCreate,
    _editor: Profile = Depends(require_editor),
    service: IngestionService = Depends(get_ingestion_service),
) -> IngestionJobOut:
    job = await service.run_source(
        payload.source_id, trigger=payload.trigger, max_items=payload.max_items
    )
    return IngestionJobOut.model_validate(job)


@router.get("/jobs", response_model=Page[IngestionJobOut], summary="List ingestion jobs")
async def list_jobs(
    source_id: str | None = Query(default=None),
    job_status: Literal["queued", "running", "completed", "failed"] | None = Query(
        default=None, alias="status"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _editor: Profile = Depends(require_editor),
    repo: IngestionJobRepository = Depends(get_job_repo),
) -> Page[IngestionJobOut]:
    documents, total = await repo.list(
        source_id=source_id, status=job_status, page=page, page_size=page_size
    )
    return Page.build(
        [IngestionJobOut.model_validate(doc) for doc in documents], total, page, page_size
    )


@router.post(
    "/articles",
    response_model=ArticleDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Manually ingest a single article",
)
async def ingest_article(
    payload: ArticleIngest,
    _editor: Profile = Depends(require_editor),
    service: IngestionService = Depends(get_ingestion_service),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> ArticleDetail:
    article = await service.ingest_article(payload)
    stored = await article_repo.get_by_id(str(article["_id"])) or article
    return ArticleDetail.model_validate(stored)
