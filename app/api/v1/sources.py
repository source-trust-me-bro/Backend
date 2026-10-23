from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import LanguageFilter, Profile, get_current_user, get_source_repo, require_editor
from app.core.exceptions import NotFoundError
from app.repositories.sources import SourceRepository
from app.schemas.base import Page
from app.schemas.source import SourceCreate, SourceOut, SourceUpdate
from app.schemas.user import ClerkPrincipal

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post(
    "",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a news source and its RSS feed",
)
async def create_source(
    payload: SourceCreate,
    profile: Profile = Depends(require_editor),
    repo: SourceRepository = Depends(get_source_repo),
) -> SourceOut:
    created = await repo.create(payload.model_dump(), created_by=str(profile["_id"]))
    return SourceOut.model_validate(created)


@router.get("", response_model=Page[SourceOut], summary="List news sources")
async def list_sources(
    language: LanguageFilter,
    active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: SourceRepository = Depends(get_source_repo),
) -> Page[SourceOut]:
    documents, total = await repo.list(
        language=language, active=active, page=page, page_size=page_size
    )
    return Page.build([SourceOut.model_validate(doc) for doc in documents], total, page, page_size)


@router.get("/{source_id}", response_model=SourceOut, summary="Fetch a single news source")
async def get_source(
    source_id: str,
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: SourceRepository = Depends(get_source_repo),
) -> SourceOut:
    source = await repo.get_by_id(source_id)
    if source is None:
        raise NotFoundError(f"Source '{source_id}' was not found.")
    return SourceOut.model_validate(source)


@router.patch(
    "/{source_id}", response_model=SourceOut, summary="Update a news source (editor only)"
)
async def update_source(
    source_id: str,
    payload: SourceUpdate,
    _editor: Profile = Depends(require_editor),
    repo: SourceRepository = Depends(get_source_repo),
) -> SourceOut:
    fields = payload.model_dump(exclude_unset=True)
    updated = await repo.update(source_id, fields) if fields else await repo.get_by_id(source_id)
    if updated is None:
        raise NotFoundError(f"Source '{source_id}' was not found.")
    return SourceOut.model_validate(updated)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a news source (editor only)",
)
async def delete_source(
    source_id: str,
    _editor: Profile = Depends(require_editor),
    repo: SourceRepository = Depends(get_source_repo),
) -> Response:
    deleted = await repo.delete_by_id(source_id)
    if not deleted:
        raise NotFoundError(f"Source '{source_id}' was not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
