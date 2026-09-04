from pathlib import Path
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/database/app.db")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/data/exports"))
SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
