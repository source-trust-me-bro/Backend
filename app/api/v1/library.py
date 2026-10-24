from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    Profile,
    get_article_repo,
    get_current_profile,
    get_library_repo,
)
from app.core.exceptions import NotFoundError
from app.core.utils import require_object_id
from app.repositories.articles import ArticleRepository
from app.repositories.library import Kind, LibraryRepository
from app.schemas.article import ArticleSummary
from app.schemas.base import Page
from app.schemas.library import DownloadedResult, FlagUpdate, LibraryState, SavedResult

router = APIRouter(prefix="/library", tags=["library"])


async def _require_article(article_id: str, repo: ArticleRepository) -> ObjectId:
    if not await repo.exists(article_id):
        raise NotFoundError(f"Article '{article_id}' was not found.")
    return require_object_id(article_id)


async def _article_page(
    kind: Kind,
    *,
    profile: Profile,
    library_repo: LibraryRepository,
    article_repo: ArticleRepository,
    page: int,
    page_size: int,
) -> Page[ArticleSummary]:
    ids = await library_repo.article_ids(profile["clerk_user_id"], kind)
    # Articles can be deleted after being saved or read; skip those and count what remains.
    existing = await article_repo.existing_ids(ids)
    visible = [article_id for article_id in ids if article_id in existing]
    window = visible[(page - 1) * page_size : page * page_size]
    documents = await article_repo.summaries_by_ids(window)
    items = [ArticleSummary.model_validate(documents[article_id]) for article_id in window]
    return Page.build(items, len(visible), page, page_size)


@router.get("/state", response_model=LibraryState, summary="Ids the caller saved or downloaded")
async def library_state(
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
) -> LibraryState:
    user_id = profile["clerk_user_id"]
    saved = await library_repo.article_ids(user_id, "saved")
    downloaded = await library_repo.article_ids(user_id, "downloaded")
    return LibraryState(
        saved=[str(article_id) for article_id in saved],
        downloaded=[str(article_id) for article_id in downloaded],
    )


@router.get("/saved", response_model=Page[ArticleSummary], summary="Saved articles")
async def saved_articles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    return await _article_page(
        "saved",
        profile=profile,
        library_repo=library_repo,
        article_repo=article_repo,
        page=page,
        page_size=page_size,
    )


@router.get("/downloaded", response_model=Page[ArticleSummary], summary="Downloaded articles")
async def downloaded_articles(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    return await _article_page(
        "downloaded",
        profile=profile,
        library_repo=library_repo,
        article_repo=article_repo,
        page=page,
        page_size=page_size,
    )


@router.get("/history", response_model=Page[ArticleSummary], summary="Reading history")
async def reading_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    return await _article_page(
        "history",
        profile=profile,
        library_repo=library_repo,
        article_repo=article_repo,
        page=page,
        page_size=page_size,
    )


@router.put("/{article_id}/saved", response_model=SavedResult, summary="Save or unsave an article")
async def set_saved(
    article_id: str,
    payload: FlagUpdate,
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> SavedResult:
    object_id = await _require_article(article_id, article_repo)
    await library_repo.set_flag(profile["clerk_user_id"], object_id, "saved", payload.value)
    return SavedResult(saved=payload.value)


@router.put(
    "/{article_id}/downloaded",
    response_model=DownloadedResult,
    summary="Mark an article as downloaded for offline reading, or remove it",
)
async def set_downloaded(
    article_id: str,
    payload: FlagUpdate,
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> DownloadedResult:
    object_id = await _require_article(article_id, article_repo)
    await library_repo.set_flag(profile["clerk_user_id"], object_id, "downloaded", payload.value)
    return DownloadedResult(downloaded=payload.value)


@router.post(
    "/{article_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record that the caller opened an article",
)
async def record_read(
    article_id: str,
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Response:
    object_id = await _require_article(article_id, article_repo)
    await library_repo.mark_read(profile["clerk_user_id"], object_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/history", status_code=status.HTTP_204_NO_CONTENT, summary="Clear the reading history"
)
async def clear_history(
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
) -> Response:
    await library_repo.clear_history(profile["clerk_user_id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/downloaded", status_code=status.HTTP_204_NO_CONTENT, summary="Remove all downloads"
)
async def clear_downloaded(
    profile: Profile = Depends(get_current_profile),
    library_repo: LibraryRepository = Depends(get_library_repo),
) -> Response:
    await library_repo.clear_downloaded(profile["clerk_user_id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
