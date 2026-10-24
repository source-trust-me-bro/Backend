from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    LanguageFilter,
    Profile,
    get_community_repo,
    get_current_user,
    get_search_service,
    get_topic_repo,
    require_editor,
)
from app.core.exceptions import NotFoundError
from app.repositories.communities import CommunityRepository
from app.repositories.topics import TopicRepository
from app.schemas.article import (
    ArticleSummary,
    SemanticHit,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from app.schemas.base import Page
from app.schemas.topic import TopicBranchCreate, TopicCreate, TopicNode, TopicOut, TopicUpdate
from app.schemas.user import ClerkPrincipal
from app.services.search_service import SearchService

router = APIRouter(prefix="/topics", tags=["topics"])
search_router = APIRouter(prefix="/search", tags=["search"])


@router.get("/tree", response_model=list[TopicNode], summary="Nested topic tree")
async def topic_tree(
    language: LanguageFilter,
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: TopicRepository = Depends(get_topic_repo),
) -> list[TopicNode]:
    documents = await repo.list_all(language=language)
    return [TopicNode.model_validate(node) for node in repo.build_tree(documents)]


@router.post(
    "",
    response_model=TopicOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a topic (editor only)",
)
async def create_topic(
    payload: TopicCreate,
    _editor: Profile = Depends(require_editor),
    repo: TopicRepository = Depends(get_topic_repo),
) -> TopicOut:
    created = await repo.create(payload.model_dump())
    return TopicOut.model_validate(created)


@router.patch("/{topic_id}", response_model=TopicOut, summary="Update a topic (editor only)")
async def update_topic(
    topic_id: str,
    payload: TopicUpdate,
    _editor: Profile = Depends(require_editor),
    repo: TopicRepository = Depends(get_topic_repo),
) -> TopicOut:
    fields = payload.model_dump(exclude_unset=True)
    updated = await repo.update(topic_id, fields) if fields else await repo.get_by_id(topic_id)
    if updated is None:
        raise NotFoundError(f"Topic '{topic_id}' was not found.")
    return TopicOut.model_validate(updated)


@router.post(
    "/{topic_id}/branches",
    response_model=TopicOut,
    summary="Link two topics as related branches (editor only)",
)
async def add_topic_branch(
    topic_id: str,
    payload: TopicBranchCreate,
    _editor: Profile = Depends(require_editor),
    repo: TopicRepository = Depends(get_topic_repo),
) -> TopicOut:
    """Create a symmetric topic-to-topic relationship.

    The operation is idempotent: linking two topics that are already linked
    returns the same representation without changing anything. Self-links and
    unknown topic IDs are rejected.
    """

    updated = await repo.add_branch(topic_id, payload.branch_id)
    return TopicOut.model_validate(updated)


@search_router.get("", response_model=Page[ArticleSummary], summary="Keyword article search")
async def search_articles(
    language: LanguageFilter,
    q: str = Query(min_length=1, max_length=500),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: ClerkPrincipal = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
) -> Page[ArticleSummary]:
    documents, total = await service.text_search(
        q, language=language, page=page, page_size=page_size
    )
    return Page.build(
        [ArticleSummary.model_validate(doc) for doc in documents], total, page, page_size
    )


@search_router.post(
    "/semantic",
    response_model=SemanticSearchResponse,
    summary="Semantic article search using stored embeddings",
)
async def semantic_search(
    payload: SemanticSearchRequest,
    _principal: ClerkPrincipal = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
    community_repo: CommunityRepository = Depends(get_community_repo),
) -> SemanticSearchResponse:
    topic_ids: list[str] | None = None
    if payload.community_id:
        community = await community_repo.get_by_id(payload.community_id)
        if community is None:
            raise NotFoundError(f"Community '{payload.community_id}' was not found.")
        topic_ids = [str(value) for value in community.get("topic_ids") or []] or None

    hits = await service.semantic_search(
        payload.query,
        language=payload.language,
        topic_ids=topic_ids,
        limit=payload.limit,
    )
    results = [
        SemanticHit(article=ArticleSummary.model_validate(article), score=round(score, 6))
        for article, score in hits
    ]
    return SemanticSearchResponse(
        query=payload.query, language=payload.language, results=results, total=len(results)
    )
