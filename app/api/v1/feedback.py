from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Profile, get_current_profile, get_feedback_repo, require_editor
from app.repositories.feedback import FeedbackRepository
from app.schemas.base import Page
from app.schemas.feedback import FeedbackCreate, FeedbackCreated, FeedbackOut

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "",
    response_model=FeedbackCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Send feedback to the team",
)
async def create_feedback(
    payload: FeedbackCreate,
    profile: Profile = Depends(get_current_profile),
    repo: FeedbackRepository = Depends(get_feedback_repo),
) -> FeedbackCreated:
    document = await repo.create(
        {
            "clerk_user_id": profile["clerk_user_id"],
            "email": profile.get("email"),
            "display_name": profile.get("display_name"),
            "subject": payload.subject or None,
            "message": payload.message,
        }
    )
    return FeedbackCreated.model_validate(document)


@router.get("", response_model=Page[FeedbackOut], summary="List feedback (editor only)")
async def list_feedback(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _editor: Profile = Depends(require_editor),
    repo: FeedbackRepository = Depends(get_feedback_repo),
) -> Page[FeedbackOut]:
    documents, total = await repo.list(page=page, page_size=page_size)
    return Page.build(
        [FeedbackOut.model_validate(doc) for doc in documents], total, page, page_size
    )
