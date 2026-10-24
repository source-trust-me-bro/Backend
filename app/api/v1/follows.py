from fastapi import APIRouter, Depends

from app.api.deps import Profile, get_current_profile, get_source_repo, get_user_repo
from app.core.exceptions import NotFoundError
from app.repositories.sources import SourceRepository
from app.repositories.users import UserRepository
from app.schemas.library import FlagUpdate, FollowResult
from app.schemas.source import SourceOut

router = APIRouter(prefix="/follows", tags=["follows"])


@router.get("/sources", response_model=list[SourceOut], summary="News sources the caller follows")
async def followed_sources(
    profile: Profile = Depends(get_current_profile),
    source_repo: SourceRepository = Depends(get_source_repo),
) -> list[SourceOut]:
    source_ids = [str(value) for value in profile.get("followed_source_ids") or []]
    documents = await source_repo.get_many(source_ids)
    return [SourceOut.model_validate(doc) for doc in documents]


@router.put(
    "/sources/{source_id}",
    response_model=FollowResult,
    summary="Follow or unfollow a news source",
)
async def set_following(
    source_id: str,
    payload: FlagUpdate,
    profile: Profile = Depends(get_current_profile),
    source_repo: SourceRepository = Depends(get_source_repo),
    user_repo: UserRepository = Depends(get_user_repo),
) -> FollowResult:
    if not await source_repo.exists(source_id):
        raise NotFoundError(f"Source '{source_id}' was not found.")
    updated = await user_repo.set_followed_source(
        profile["clerk_user_id"], source_id, payload.value
    )
    if updated is None:
        raise NotFoundError("User profile was not found.")
    return FollowResult(following=payload.value)
