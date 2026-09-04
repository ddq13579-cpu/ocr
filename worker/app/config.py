import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/database/app.db")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash-lite")
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "0.25"))
OCR_MAX_SIDE = int(os.getenv("OCR_MAX_SIDE", "2500"))
