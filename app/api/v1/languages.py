"""The languages readers can choose, with how many articles each language collection holds."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_article_repo
from app.config import Settings, get_settings
from app.core.security import get_current_user
from app.repositories.articles import ArticleRepository
from app.schemas.user import ClerkPrincipal

router = APIRouter(prefix="/languages", tags=["languages"])

# How each language names itself, so a reader can find theirs. Unknown codes show as the code.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "fi": "Suomi",
    "sv": "Svenska",
    "ar": "العربية",
    "se": "Davvisámegiella",
    "uk": "Українська",
    "ru": "Русский",
}
RIGHT_TO_LEFT = {"ar", "fa", "he", "ur"}


class LanguageOut(BaseModel):
    code: str
    label: str
    rtl: bool
    articles: int


@router.get("", response_model=list[LanguageOut], summary="Supported languages and their stories")
async def list_languages(
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: ArticleRepository = Depends(get_article_repo),
    settings: Settings = Depends(get_settings),
) -> list[LanguageOut]:
    counts = await repo.counts_by_language()
    return [
        LanguageOut(
            code=code,
            label=LANGUAGE_NAMES.get(code, code.upper()),
            rtl=code in RIGHT_TO_LEFT,
            articles=counts.get(code, 0),
        )
        for code in settings.supported_languages
    ]
