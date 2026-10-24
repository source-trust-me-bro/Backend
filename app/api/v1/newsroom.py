"""Community participation and the newsroom desk, relayed to the agentic newsroom.

The backend authenticates the person, decides their role, and passes only a pseudonymous
reference downstream. Reviewer names come from the authenticated account, never from the
request body, so every approval is traceable to a signed-in human.
"""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    Profile,
    get_article_repo,
    get_current_profile,
    get_embedding_service,
    get_source_repo,
    get_topic_repo,
    get_user_repo,
    require_admin,
    require_editor,
)
from app.config import Settings, get_settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.repositories.articles import ArticleRepository
from app.repositories.sources import SourceRepository
from app.repositories.topics import TopicRepository
from app.repositories.users import UserRepository
from app.services.embedding_service import EmbeddingService
from app.services.newsroom_client import NewsroomClient, user_ref
from app.services.newsroom_sync import NewsroomSync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/newsroom", tags=["newsroom"])

# Syncs started after a publish; kept referenced until they finish.
_sync_tasks: set[asyncio.Task[None]] = set()


def _sync_soon(sync: NewsroomSync) -> None:
    """Copy published stories to the reader dashboard in the background, so a publish answers
    at once. A failed copy must not undo the publish: the next sync picks it up."""

    async def run() -> None:
        try:
            await sync.run()
        except Exception:
            logger.warning("Copying published stories to the dashboard failed", exc_info=True)

    task = asyncio.create_task(run())
    _sync_tasks.add(task)
    task.add_done_callback(_sync_tasks.discard)


def get_client(settings: Settings = Depends(get_settings)) -> NewsroomClient:
    return NewsroomClient(settings)


def get_sync(
    client: NewsroomClient = Depends(get_client),
    article_repo: ArticleRepository = Depends(get_article_repo),
    topic_repo: TopicRepository = Depends(get_topic_repo),
    source_repo: SourceRepository = Depends(get_source_repo),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
) -> NewsroomSync:
    return NewsroomSync(
        client=client,
        article_repo=article_repo,
        topic_repo=topic_repo,
        source_repo=source_repo,
        embedding_service=embedding_service,
    )


def _ref(profile: Profile) -> str:
    return user_ref(profile["clerk_user_id"])


def _display_name(profile: Profile) -> str:
    return (profile.get("display_name") or "").strip() or f"Editor {_ref(profile)[:6]}"


class JoinIn(BaseModel):
    community_id: str
    language: str | None = None


class MessageIn(BaseModel):
    kind: Literal["question", "tip", "correction", "local_knowledge"]
    text: str = Field(min_length=1, max_length=4000)
    community_id: str | None = None
    language: str = "en"
    story_id: str | None = None


class CycleIn(BaseModel):
    community_ids: list[str] | None = None
    use_fixtures: bool = False
    news_count: Literal[10, 50, 100] | None = None


class DecisionIn(BaseModel):
    role: Literal["editor_in_chief", "language_reviewer", "community_reviewer", "subject_expert"]
    decision: Literal["approve", "reject", "request_changes"]
    notes: str = Field(default="", max_length=2000)
    # Only the editor-in-chief's approval uses this: where the story is published.
    destinations: list[Literal["dashboard", "community"]] = Field(
        default_factory=list, max_length=2
    )


class ReviewIn(BaseModel):
    decisions: list[DecisionIn] = Field(min_length=1)


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    official: bool = False


class CorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CommunityRoleIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)  # an id returned by the name search
    community_id: str | None = None  # platform admins choose it; community admins use their own
    community_role: Literal["member", "moderator", "admin"]
    review_roles: list[Literal["language_reviewer", "community_reviewer", "subject_expert"]] = (
        Field(default_factory=list)
    )
    review_languages: list[str] = Field(default_factory=list, max_length=20)


class LinkVoiceIn(BaseModel):
    clerk_user_id: str = Field(min_length=1)
    community_id: str
    voice_id: str


async def _member(client: NewsroomClient, profile: Profile) -> dict[str, Any]:
    return await client.request("GET", "/members/me", ref=_ref(profile))


@router.get("/communities", summary="Communities the newsroom serves")
async def communities(
    _profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/communities")


@router.get("/me", summary="Which community the signed-in user belongs to")
async def me(
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> dict[str, Any]:
    return await _member(client, profile)


@router.put("/me/community", summary="Join a community")
async def join(
    payload: JoinIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "PUT", "/members/me/community", ref=_ref(profile), json=payload.model_dump()
    )


@router.get("/feed", summary="Approved stories for a community")
async def feed(
    community_id: str | None = Query(default=None),
    destination: Literal["dashboard", "community"] | None = Query(default=None),
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    target = community_id or (await _member(client, profile)).get("community_id")
    if not target:
        return []
    return await client.request(
        "GET", f"/feeds/{target}", params={"destination": destination} if destination else None
    )


@router.post("/messages", status_code=201, summary="Ask, send a tip, or request a correction")
async def send_message(
    payload: MessageIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    community_id = payload.community_id or (await _member(client, profile)).get("community_id")
    if not community_id:
        raise ForbiddenError("Join a community first.")
    body = payload.model_dump()
    body["community_id"] = community_id
    return await client.request("POST", "/community/messages", ref=_ref(profile), json=body)


@router.get("/messages/mine", summary="Your own questions, tips and corrections")
async def my_messages(
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/community/messages/mine", ref=_ref(profile))


# ----------------------------------------------- following, reactions and sharing


class FollowIn(BaseModel):
    kind: Literal["community", "official"]
    target_id: str = Field(min_length=1, max_length=64)


class LanguageIn(BaseModel):
    language: str = Field(min_length=2, max_length=8)
    # Other languages the member reads too. Stories in any other language are never shown.
    also_reads: list[str] | None = Field(default=None, max_length=12)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    language: str = "en"
    community_id: str | None = None


class ShareIn(BaseModel):
    platform: Literal["facebook", "x", "whatsapp", "telegram", "linkedin", "email", "copy_link"]
    community_id: str | None = None


class PostSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    official: bool = False


class EditorPostIn(BaseModel):
    headline: str = Field(min_length=1, max_length=200)
    standfirst: str = Field(default="", max_length=600)
    body_md: str = Field(min_length=1, max_length=20000)
    language: str = Field(default="en", max_length=8)
    # Empty means every community.
    community_ids: list[str] | None = None
    destinations: list[Literal["dashboard", "community"]] = Field(
        default_factory=list, max_length=2
    )
    sources: list[PostSourceIn] = Field(default_factory=list, max_length=20)


@router.get("/directory", summary="Every community and official account, with your follows")
async def directory(
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/directory", ref=_ref(profile))


@router.get("/follows", summary="What you follow")
async def my_follows(
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/follows/me", ref=_ref(profile))


@router.post("/follows", status_code=201, summary="Follow a community or an official account")
async def follow(
    payload: FollowIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("POST", "/follows/me", ref=_ref(profile), json=payload.model_dump())


@router.delete("/follows/{kind}/{target_id}", summary="Stop following")
async def unfollow(
    kind: Literal["community", "official"],
    target_id: str,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("DELETE", f"/follows/me/{kind}/{target_id}", ref=_ref(profile))


@router.get("/follows/recommended", summary="What to follow next, with the reasons")
async def recommended_follows(
    limit: int = Query(default=8, ge=1, le=30),
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "GET", "/follows/recommended", ref=_ref(profile), params={"limit": limit}
    )


@router.put("/me/language", summary="The languages you read in")
async def set_language(
    payload: LanguageIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "PUT", "/members/me/language", ref=_ref(profile), json=payload.model_dump()
    )


@router.get("/feed/following", summary="Stories from what you follow, in your languages")
async def following_feed(
    language: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=50, ge=1, le=200),
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    params: dict[str, Any] = {"limit": limit}
    if language:
        params["language"] = language
    return await client.request("GET", "/feed/me", ref=_ref(profile), params=params)


@router.get("/top-news", summary="Most engaged stories, in your languages")
async def top_news(
    community_id: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    params: dict[str, Any] = {"days": days, "limit": limit}
    if community_id:
        params["community_id"] = community_id
    return await client.request("GET", "/top-news", ref=_ref(profile), params=params)


@router.put("/stories/{story_id}/like", summary="Like a story")
async def like(
    story_id: str,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("PUT", f"/stories/{story_id}/like", ref=_ref(profile))


@router.delete("/stories/{story_id}/like", summary="Remove your like")
async def unlike(
    story_id: str,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("DELETE", f"/stories/{story_id}/like", ref=_ref(profile))


@router.get("/stories/{story_id}/comments", summary="Comments on a story")
async def story_comments(
    story_id: str,
    _profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", f"/stories/{story_id}/comments")


@router.post("/stories/{story_id}/comments", status_code=201, summary="Comment on a story")
async def add_comment(
    story_id: str,
    payload: CommentIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "POST", f"/stories/{story_id}/comments", ref=_ref(profile), json=payload.model_dump()
    )


@router.get("/stories/{story_id}/share-links", summary="Links to share a story")
async def share_links(
    story_id: str,
    _profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", f"/stories/{story_id}/share-links")


@router.post("/stories/{story_id}/share", status_code=201, summary="Record a share")
async def share(
    story_id: str,
    payload: ShareIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "POST", f"/stories/{story_id}/share", ref=_ref(profile), json=payload.model_dump()
    )


@router.post("/staff/posts", status_code=201, summary="Post a story to communities (editor only)")
async def editor_post(
    payload: EditorPostIn,
    profile: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
    sync: NewsroomSync = Depends(get_sync),
) -> Any:
    """The editor's name comes from the signed-in account, never from the request."""

    body = payload.model_dump()
    body["editor"] = _display_name(profile)
    result = await client.request("POST", "/staff/posts", json=body)
    _sync_soon(sync)
    return result


@router.get("/dashboard/engagement", summary="Top news, communities and topics (editor only)")
async def engagement_dashboard(
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=100),
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "GET", "/dashboard/engagement", params={"days": days, "limit": limit}
    )


# ---------------------------------------------------------------------- newsroom desk


@router.post("/cycle", status_code=202, summary="Start a newsroom cycle (editor only)")
async def run_cycle(
    payload: CycleIn,
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    """Starts the cycle in the background and returns at once. A real model takes minutes."""

    return await client.request("POST", "/cycle/start", json=payload.model_dump())


@router.post("/cycle/stop", summary="Stop the running newsroom cycle (editor only)")
async def stop_cycle(
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("POST", "/cycle/stop")


@router.post("/connector", summary="Link related topics in the topic graph (editor only)")
async def run_connector(
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("POST", "/connector/run")


@router.get("/overview", summary="Sources, topics and recent cycles (editor only)")
async def overview(
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/overview")


@router.get("/cycle/latest", summary="Status of the latest newsroom cycle (editor only)")
async def latest_cycle(
    _editor: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request("GET", "/cycle/latest")


def _is_editor(profile: Profile) -> bool:
    return bool(set(profile.get("roles") or []) & {"editor", "admin"})


def _is_moderator(member: dict[str, Any]) -> bool:
    return member.get("community_role") in {"moderator", "admin"}


@router.get(
    "/review-queue",
    summary="Stories waiting for a human (editors: all; moderators: their community)",
)
async def review_queue(
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    member = await _member(client, profile)
    if not _is_editor(profile) and not _is_moderator(member):
        raise ForbiddenError("Only editors and community moderators can see the review queue.")
    queue = await client.request("GET", "/review-queue")
    if _is_editor(profile):
        return queue
    return [item for item in queue if item.get("community_id") == member.get("community_id")]


@router.post("/review/{pitch_id}", summary="Approve, reject or request changes (named human)")
async def submit_review(
    pitch_id: str,
    payload: ReviewIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
    sync: NewsroomSync = Depends(get_sync),
) -> Any:
    """The reviewer is always the signed-in account. Editors act as editor-in-chief under
    their own name. Community admins and moderators act in the community roles they were
    given, as the community's trusted voices; the newsroom checks those rights."""

    decisions = []
    for item in payload.decisions:
        if item.role == "editor_in_chief":
            if not _is_editor(profile):
                raise ForbiddenError("Only editors can act as editor-in-chief.")
            reviewer = _display_name(profile)
        else:
            reviewer = f"member:{_ref(profile)}"
        decisions.append({**item.model_dump(), "reviewer": reviewer})
    result = await client.request("POST", f"/review/{pitch_id}", json={"decisions": decisions})
    if isinstance(result, dict) and result.get("status") == "published":
        _sync_soon(sync)
    return result


@router.post("/sync", summary="Copy published stories to the reader dashboard (editor only)")
async def sync_dashboard(
    _editor: Profile = Depends(require_editor),
    sync: NewsroomSync = Depends(get_sync),
) -> dict[str, int]:
    return await sync.run()


@router.post(
    "/stories/{story_id}/sources",
    summary="Check the sources the agents attached and add more (editor only)",
)
async def add_source(
    story_id: str,
    payload: SourceIn,
    profile: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "POST",
        f"/stories/{story_id}/sources",
        json={**payload.model_dump(), "reviewer": _display_name(profile)},
    )


@router.post("/corrections/{correction_id}/publish", summary="Publish a correction (editor only)")
async def publish_correction(
    correction_id: str,
    payload: CorrectionIn,
    profile: Profile = Depends(require_editor),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "POST",
        f"/corrections/{correction_id}/publish",
        json={"reviewer": _display_name(profile), "text": payload.text},
    )


def _mask_email(email: str | None) -> str:
    """A hint to tell two people with the same name apart, without exposing the address."""

    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


@router.get(
    "/staff/users",
    summary="Search accounts by name to appoint trusted voices (editors, community admins)",
)
async def search_users(
    q: str = Query(min_length=2, max_length=80),
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
    users: UserRepository = Depends(get_user_repo),
) -> list[dict[str, str]]:
    member = await _member(client, profile)
    if not _is_editor(profile) and member.get("community_role") != "admin":
        raise ForbiddenError("Only editors and community admins can search accounts.")
    found = await users.search_by_name(q)
    return [
        {
            "id": str(doc["_id"]),
            "display_name": (doc.get("display_name") or "").strip(),
            "email_hint": _mask_email(doc.get("email")),
        }
        for doc in found
        if (doc.get("display_name") or "").strip()
    ]


@router.post(
    "/staff/community-role",
    summary="Appoint a community moderator or admin (editors, or the community's own admin)",
)
async def set_community_role(
    payload: CommunityRoleIn,
    profile: Profile = Depends(get_current_profile),
    client: NewsroomClient = Depends(get_client),
    users: UserRepository = Depends(get_user_repo),
) -> Any:
    """Community admins and moderators are the community's trusted voices.

    Editors and platform admins can appoint community admins and moderators in any community.
    A community admin can appoint moderators in their own community, but cannot create other
    admins.
    """

    me = await _member(client, profile)
    staff = _is_editor(profile)
    community_id = me.get("community_id")
    if not staff:
        if me.get("community_role") != "admin" or not community_id:
            raise ForbiddenError(
                "Only editors and community admins can appoint community moderators."
            )
        if payload.community_role == "admin":
            raise ForbiddenError("Only an editor can appoint a community admin.")
    target = await users.get_by_id(payload.user_id)
    if target is None:
        raise NotFoundError("That account was not found. They need to sign in once first.")
    target_community = payload.community_id if staff else community_id
    if not target_community:
        raise ForbiddenError("Choose the community to appoint for.")
    body = {
        "user_ref": user_ref(target["clerk_user_id"]),
        "community_id": target_community,
        "community_role": payload.community_role,
        "review_roles": payload.review_roles,
        "review_languages": payload.review_languages,
        "display_name": (target.get("display_name") or "").strip(),
    }
    return await client.request("POST", "/staff/community-role", json=body)


@router.post("/staff/link-voice", summary="Link an account to a trusted voice (admin only)")
async def link_voice(
    payload: LinkVoiceIn,
    _admin: Profile = Depends(require_admin),
    client: NewsroomClient = Depends(get_client),
) -> Any:
    return await client.request(
        "POST",
        "/staff/link-voice",
        json={
            "user_ref": user_ref(payload.clerk_user_id),
            "community_id": payload.community_id,
            "voice_id": payload.voice_id,
        },
    )
