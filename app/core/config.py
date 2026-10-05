from pydantic_settings import BaseSettings, SettingsConfigDict



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