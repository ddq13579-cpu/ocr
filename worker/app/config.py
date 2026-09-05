import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/database/app.db")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash-lite")
ALIBABA_CLOUD_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_CLOUD_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "0.25"))
OCR_CONCURRENCY = int(os.getenv("OCR_CONCURRENCY", "1"))
GEMINI_CONCURRENCY = int(os.getenv("GEMINI_CONCURRENCY", "20"))
