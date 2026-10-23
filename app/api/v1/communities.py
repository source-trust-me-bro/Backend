from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    LanguageFilter,
    Profile,
    get_article_repo,
    get_community_repo,
    get_current_user,
    get_topic_repo,
    get_user_repo,
    require_editor,
)
from app.core.exceptions import BadRequestError, NotFoundError
from app.repositories.articles import ArticleRepository
from app.repositories.communities import CommunityRepository
from app.repositories.topics import TopicRepository
from app.repositories.users import UserRepository
from app.schemas.article import ArticleSummary
from app.schemas.base import Page
from app.schemas.community import CommunityCreate, CommunityOut, CommunityUpdate
from app.schemas.user import ClerkPrincipal

router = APIRouter(prefix="/communities", tags=["communities"])


async def _validate_references(
    *,
    topic_ids: list[str] | None,
    editor_user_ids: list[str] | None,
    topic_repo: TopicRepository,
    user_repo: UserRepository,
) -> tuple[list[str] | None, list[str] | None]:
    if topic_ids is not None:
        topic_ids = list(dict.fromkeys(topic_ids))
        if len(await topic_repo.get_many(topic_ids)) != len(topic_ids):
            raise BadRequestError("One or more topic_ids do not exist.")
    if editor_user_ids is not None:
        editor_user_ids = list(dict.fromkeys(editor_user_ids))
        if len(await user_repo.get_by_ids(editor_user_ids)) != len(editor_user_ids):
            raise BadRequestError("One or more editor_user_ids do not exist.")
    return topic_ids, editor_user_ids


@router.post(
    "",
    response_model=CommunityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a community (editor only)",
)
async def create_community(
    payload: CommunityCreate,
    profile: Profile = Depends(require_editor),
    repo: CommunityRepository = Depends(get_community_repo),
    topic_repo: TopicRepository = Depends(get_topic_repo),
    user_repo: UserRepository = Depends(get_user_repo),
) -> CommunityOut:
    data = payload.model_dump()
    topic_ids, editor_user_ids = await _validate_references(
        topic_ids=data["topic_ids"],
        editor_user_ids=data["editor_user_ids"],
        topic_repo=topic_repo,
        user_repo=user_repo,
    )
    data["topic_ids"] = topic_ids
    data["editor_user_ids"] = editor_user_ids
    created = await repo.create(data, created_by=str(profile["_id"]))
    return CommunityOut.model_validate(created)


@router.get("", response_model=Page[CommunityOut], summary="List communities")
async def list_communities(
    language: LanguageFilter,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: CommunityRepository = Depends(get_community_repo),
) -> Page[CommunityOut]:
    documents, total = await repo.list(language=language, page=page, page_size=page_size)
    return Page.build(
        [CommunityOut.model_validate(doc) for doc in documents], total, page, page_size
    )


@router.get("/{community_id}", response_model=CommunityOut, summary="Fetch a community")
async def get_community(
    community_id: str,
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: CommunityRepository = Depends(get_community_repo),
) -> CommunityOut:
    community = await repo.get_by_id(community_id)
    if community is None:
        raise NotFoundError(f"Community '{community_id}' was not found.")
    return CommunityOut.model_validate(community)


@router.patch(
    "/{community_id}",
    response_model=CommunityOut,
    summary="Update a community or assign editors (editor only)",
)
async def update_community(
    community_id: str,
    payload: CommunityUpdate,
    _editor: Profile = Depends(require_editor),
    repo: CommunityRepository = Depends(get_community_repo),
    topic_repo: TopicRepository = Depends(get_topic_repo),
    user_repo: UserRepository = Depends(get_user_repo),
) -> CommunityOut:
    fields = payload.model_dump(exclude_unset=True)
    topic_ids, editor_user_ids = await _validate_references(
        topic_ids=fields.get("topic_ids"),
        editor_user_ids=fields.get("editor_user_ids"),
        topic_repo=topic_repo,
        user_repo=user_repo,
    )
    if "topic_ids" in fields:
        fields["topic_ids"] = topic_ids
    if "editor_user_ids" in fields:
        fields["editor_user_ids"] = editor_user_ids

    updated = (
        await repo.update(community_id, fields) if fields else await repo.get_by_id(community_id)
    )
    if updated is None:
        raise NotFoundError(f"Community '{community_id}' was not found.")
    return CommunityOut.model_validate(updated)


@router.get(
    "/{community_id}/feed",
    response_model=Page[ArticleSummary],
    summary="Community-curated article feed",
)
async def community_feed(
    community_id: str,
    language: LanguageFilter,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: CommunityRepository = Depends(get_community_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    community = await repo.get_by_id(community_id)
    if community is None:
        raise NotFoundError(f"Community '{community_id}' was not found.")

    topic_ids = [str(value) for value in community.get("topic_ids") or []]
    documents, total = await article_repo.list_articles(
        language=language or community.get("language"),
        topic_ids=topic_ids or None,
        page=page,
        page_size=page_size,
    )
    return Page.build(
        [ArticleSummary.model_validate(doc) for doc in documents], total, page, page_size
    )
