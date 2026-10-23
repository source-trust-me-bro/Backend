from fastapi import APIRouter, Depends

from app.api.deps import (
    Profile,
    get_community_repo,
    get_current_profile,
    get_topic_repo,
    get_user_repo,
    require_admin,
)
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.repositories.communities import CommunityRepository
from app.repositories.topics import TopicRepository
from app.repositories.users import UserRepository
from app.schemas.user import UserProfile, UserRolesUpdate, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfile, summary="Get the current profile")
async def read_me(profile: Profile = Depends(get_current_profile)) -> UserProfile:
    return UserProfile.model_validate(profile)


@router.patch("/me", response_model=UserProfile, summary="Update preferences for the current user")
async def update_me(
    payload: UserUpdate,
    profile: Profile = Depends(get_current_profile),
    user_repo: UserRepository = Depends(get_user_repo),
    topic_repo: TopicRepository = Depends(get_topic_repo),
    community_repo: CommunityRepository = Depends(get_community_repo),
) -> UserProfile:
    fields = payload.model_dump(exclude_unset=True)

    if "roles" in fields:
        if payload.roles is None:
            raise BadRequestError("roles must be provided as a non-empty list.")
        if "admin" not in (profile.get("roles") or []):
            raise ForbiddenError("Only admins can change user roles.")
        if not payload.roles:
            raise BadRequestError("roles must be provided as a non-empty list.")
        fields["roles"] = list(dict.fromkeys(payload.roles))

    if fields.get("community_id") is not None and not await community_repo.exists(
        fields["community_id"]
    ):
        raise NotFoundError(f"Community '{fields['community_id']}' was not found.")

    if fields.get("topic_ids") is not None:
        topic_ids = list(dict.fromkeys(fields["topic_ids"]))
        matched = await topic_repo.get_many(topic_ids)
        if len(matched) != len(topic_ids):
            raise BadRequestError("One or more topic_ids do not exist.")
        fields["topic_ids"] = topic_ids

    updated = await user_repo.update_fields(profile["clerk_user_id"], fields)
    if updated is None:
        raise NotFoundError("User profile was not found.")
    return UserProfile.model_validate(updated)


@router.patch(
    "/{clerk_user_id}/roles",
    response_model=UserProfile,
    summary="Assign roles to a user (admin only)",
)
async def set_user_roles(
    clerk_user_id: str,
    payload: UserRolesUpdate,
    _admin: Profile = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repo),
) -> UserProfile:
    roles = list(dict.fromkeys(payload.roles))
    updated = await user_repo.set_roles(clerk_user_id, roles)
    if updated is None:
        raise NotFoundError(f"User '{clerk_user_id}' was not found.")
    return UserProfile.model_validate(updated)
