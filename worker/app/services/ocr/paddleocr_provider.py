from pathlib import Path
import tempfile
from PIL import Image, ImageOps
from paddleocr import PaddleOCR

from ...config import OCR_MAX_SIDE


class PaddleOCRProvider:
    def __init__(self):
        self._ocr = PaddleOCR(
            lang="ch",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )

    def extract(self, path: Path) -> str:
        temporary_path: Path | None = None
        with Image.open(path) as image:
            normalized_image = ImageOps.exif_transpose(image)
            if max(normalized_image.size) > OCR_MAX_SIDE:
                normalized_image.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                normalized_image.save(temporary_path, format="PNG")
        try:
            result = self._ocr.predict(str(temporary_path or path))
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
        lines: list[str] = []
        for page in result:
            payload = page.json if hasattr(page, "json") else page
            text_data = payload.get("res", payload) if isinstance(payload, dict) else {}
            texts = text_data.get("rec_texts", [])
            lines.extend(str(text) for text in texts)
        return "\n".join(lines).strip()
