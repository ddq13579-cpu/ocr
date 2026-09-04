import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import WORKER_POLL_SECONDS
from .database import SessionLocal
from .models import Document, OCRResult, ProcessingLog, Record, TemplateField
from .services.ai.gemini import GeminiProvider
from .services.ocr.paddleocr_provider import PaddleOCRProvider
from .services.pdf_extractor import extract_pdf_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
ocr_provider: PaddleOCRProvider | None = None
ai_provider: GeminiProvider | None = None


def log(db, document_id: int, stage: str, level: str, message: str):
    logger.log(getattr(logging, level.upper(), logging.INFO), "document=%s %s", document_id, message)
    db.add(ProcessingLog(document_id=document_id, stage=stage, level=level, message=message))


def validate_data(data: dict[str, Any], fields: list[TemplateField]) -> dict[str, Any]:
    expected = {field.field_key for field in fields}
    if set(data) != expected:
        raise ValueError("Gemini response keys do not exactly match template fields")
    for field in fields:
        value = data[field.field_key]
        if value is None:
            continue
        valid = {
            "text": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "date": isinstance(value, str) and len(value) == 10,
            "boolean": isinstance(value, bool),
        }[field.field_type]
        if not valid:
            raise ValueError(f"Invalid type for {field.field_key}")
    return data


def process_document(document_id: int):
    global ai_provider, ocr_provider
    with SessionLocal() as db:
        started_at = time.monotonic()
        document = db.get(Document, document_id)
        if not document or document.status != "pending":
            return
        document.status = "processing"
        log(db, document.id, "start", "info", f"Started processing {document.filename} ({document.file_type})")
        db.commit()
        try:
            path = Path(document.file_path)
            if document.file_type.lower() == "pdf":
                raw_text, engine = extract_pdf_text(path), "pymupdf"
                if not raw_text:
                    raise ValueError("PDF has no extractable text; scanned PDF OCR is not supported in V1")
            else:
                if ocr_provider is None:
                    ocr_provider = PaddleOCRProvider()
                raw_text, engine = ocr_provider.extract(path), "paddleocr"
                if not raw_text:
                    raise ValueError("PaddleOCR returned no text")
            ocr_result = db.scalar(select(OCRResult).where(OCRResult.document_id == document.id))
            if ocr_result:
                ocr_result.engine, ocr_result.raw_text = engine, raw_text
            else:
                db.add(OCRResult(document_id=document.id, engine=engine, raw_text=raw_text))
            document.status = "ocr_completed"
            log(db, document.id, "ocr", "info", f"{engine} completed; text length: {len(raw_text)}")
            db.commit()

            fields = db.scalars(select(TemplateField).where(TemplateField.template_id == document.template_id).order_by(TemplateField.sort_order)).all()
            if not fields:
                raise ValueError("Template has no fields")
            if ai_provider is None:
                ai_provider = GeminiProvider()
            document.status = "ai_processing"
            log(db, document.id, "ai", "info", "Gemini request started")
            db.commit()
            data = ai_provider.extract(raw_text, [{"field_key": f.field_key, "field_type": f.field_type, "description": f.description, "required": f.required} for f in fields])
            validate_data(data, fields)
            record = db.scalar(select(Record).where(
                Record.document_id == document.id, Record.template_id == document.template_id
            ))
            if record:
                record.json_data, record.status = json.dumps(data, ensure_ascii=False), "completed"
            else:
                db.add(Record(
                    document_id=document.id,
                    template_id=document.template_id,
                    json_data=json.dumps(data, ensure_ascii=False),
                    status="completed",
                ))
            document.status = "completed"
            elapsed_seconds = time.monotonic() - started_at
            log(db, document.id, "complete", "info", f"Processing completed in {elapsed_seconds:.2f}s")
            db.commit()
        except Exception as exc:
            db.rollback()
            document = db.get(Document, document_id)
            document.status, document.error_message = "failed", str(exc)
            log(db, document_id, "failed", "error", str(exc))
            db.commit()


def run():
    while True:
        with SessionLocal() as db:
            document_id = db.scalar(select(Document.id).where(Document.status == "pending").order_by(Document.created_at).limit(1))
        if document_id:
            process_document(document_id)
        else:
            time.sleep(WORKER_POLL_SECONDS)


if __name__ == "__main__":
    run()
