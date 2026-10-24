from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "multilingual-news"
    app_version: str = "1.0.0"
    app_env: str = "development"
    debug: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "multilingual_news"
    mongodb_timeout_ms: int = 5000
    startup_db_check: bool = True

    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""
    clerk_issuer: str = ""
    clerk_jwks_url: str = ""
    clerk_jwks_cache_seconds: int = 300
    # Clerk session tokens live about a minute, so tolerance beyond a few seconds only
    # makes sense on a development machine with a drifting clock.
    clerk_clock_skew_seconds: int = 30

    # The agentic newsroom service (community stories, review desk). Server-to-server only:
    # browsers never see this key, and user identity is passed as a pseudonymous reference.
    newsroom_url: str = ""
    newsroom_api_key: str = ""
    newsroom_timeout_seconds: float = 150.0

    news_languages: str = "en,fi,sv,ar,se,uk,ru"
    default_language: str = "en"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    enable_sentence_transformers: bool = False

    ingestion_max_items: int = 20
    fetch_article_content: bool = True
    max_content_chars: int = 20000
    http_timeout_seconds: float = 15.0
    feed_user_agent: str = "MultilingualNewsBot/1.0 (+https://example.com/bot)"

    search_max_tokens: int = 8
    semantic_candidate_limit: int = 500

    @property
    def supported_languages(self) -> list[str]:
        return [item.strip().lower() for item in self.news_languages.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def jwt_leeway_seconds(self) -> int:
        """Allowed clock skew when validating tokens; capped outside development."""

        requested = max(0, self.clerk_clock_skew_seconds)
        if self.app_env.lower() in {"development", "dev", "local", "test"}:
            return requested
        return min(requested, 30)

    @property
    def jwks_url(self) -> str:
        if self.clerk_jwks_url:
            return self.clerk_jwks_url
        if not self.clerk_issuer:
            return ""
        return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
