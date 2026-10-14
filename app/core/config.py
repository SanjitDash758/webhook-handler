from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Priority order (highest to lowest):
    1. Environment variables
    2. .env file
    3. Default values defined here
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    # ============================================
    # APPLICATION
    # ============================================
    APP_NAME: str = "webhook-handler"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    
    # ============================================
    # DATABASE
    # ============================================
    DATABASE_URL: str
    
    # ============================================
    # REDIS
    # ============================================
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # ============================================
    # CELERY
    # ============================================
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    
    # ============================================
    # STRIPE
    # ============================================
    STRIPE_WEBHOOK_SECRET: str
    # ============================================
    # STRIPE IP ALLOWLIST
    # ============================================
    # Defense-in-depth: restrict Stripe webhook traffic to Stripe's
    # published IP ranges. This is NOT authentication -- the HMAC
    # signature is. The allowlist reduces attack surface (blocks
    # scanners, casual abuse) but does not prove sender identity.
    #
    # Enabled by default in production, disabled in development.
    STRIPE_IP_ALLOWLIST_ENABLED: bool = False

    # Path to the JSON file containing the IP allowlist.
    # Versioned at data/stripe_ips.json; refreshed via
    # scripts/refresh_stripe_ips.py.
    STRIPE_IP_ALLOWLIST_FILE: str = "data/stripe_ips.json"

    # Emergency escape hatch. If the allowlist is misconfigured and
    # legitimate traffic is being rejected, set this to true to
    # bypass the check at startup. Logs CRITICAL when used -- should
    # trigger an alert in production monitoring.
    STRIPE_IP_ALLOWLIST_OVERRIDE: bool = False
    # ============================================
    # ADMIN API KEY
    # ============================================
    ADMIN_API_KEY: str
    WS_PUBLIC_TOKEN: str 
    # ============================================
    # IDEMPOTENCY TTLs (in seconds)
    # ============================================
    IDEMPOTENCY_TTL_STRIPE: int = 86400      # 24 hours
    IDEMPOTENCY_TTL_GENERIC: int = 604800    # 7 days
    
    # ============================================
    # RATE LIMITS (requests per minute)
    # ============================================
    RATE_LIMIT_STRIPE: int = 100
    RATE_LIMIT_GENERIC: int = 50
    
    # ============================================
    # CELERY TASK CONFIG
    # ============================================
    CELERY_MAX_RETRIES: int = 5
    CELERY_RETRY_BASE_DELAY: int = 60        # seconds
    CELERY_TASK_TIME_LIMIT: int = 300        # 5 minutes
    CELERY_TASK_SOFT_TIME_LIMIT: int = 280   # 4m40s
    
    # ============================================
    # RECONCILIATION SWEEP
    # ============================================
    SWEEP_INTERVAL_SECONDS: int = 300        # 5 minutes
    SWEEP_STUCK_THRESHOLD_SECONDS: int = 300 # 5 minutes
    MAX_SWEEP_ATTEMPTS: int = 3

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
# Singleton instance — imported everywhere
settings = Settings()