import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import GEMINI_CONCURRENCY, OCR_CONCURRENCY, WORKER_POLL_SECONDS
from .database import SessionLocal
from .models import Document, OCRResult, ProcessingLog, Record, TemplateField
from .services.ai.gemini import GeminiProvider
from .services.ocr.alibaba_ocr_provider import AlibabaOCRProvider
from .services.pdf_extractor import extract_pdf_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def log(db, document_id: int, stage: str, level: str, message: str):
    logger.log(getattr(logging, level.upper(), logging.INFO), "document=%s %s", document_id, message)
    db.add(ProcessingLog(document_id=document_id, stage=stage, level=level, message=message))


def mark_failed(document_id: int, stage: str, error: Exception):
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document:
            document.status, document.error_message = "failed", str(error)
            log(db, document_id, stage, "error", str(error))
            db.commit()


@lru_cache
def get_ocr_provider() -> AlibabaOCRProvider:
    return AlibabaOCRProvider()


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


def claim_pending_batch() -> list[int]:
    with SessionLocal() as db:
        documents = db.scalars(
            select(Document).where(Document.status == "pending").order_by(Document.created_at)
        ).all()
        for document in documents:
            document.status = "processing"
            log(db, document.id, "start", "info", f"Added to OCR batch: {document.filename} ({document.file_type})")
        db.commit()
        return [document.id for document in documents]


def extract_ocr(document_id: int) -> int | None:
    try:
        with SessionLocal() as db:
            document = db.get(Document, document_id)
            if not document or document.status != "processing":
                return None
            path = Path(document.file_path)
            if document.file_type.lower() == "pdf":
                raw_text, engine = extract_pdf_text(path), "pymupdf"
                if not raw_text:
                    raise ValueError("PDF has no extractable text; scanned PDF OCR is not supported in V1")
            else:
                raw_text, engine = get_ocr_provider().extract(path), "alibaba_ocr"
                if not raw_text:
                    raise ValueError("Alibaba Cloud OCR returned no text")
            ocr_result = db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
            if ocr_result:
                ocr_result.engine, ocr_result.raw_text = engine, raw_text
            else:
                db.add(OCRResult(document_id=document_id, engine=engine, raw_text=raw_text))
            document.status = "ocr_completed"
            log(db, document_id, "ocr", "info", f"{engine} completed; text length: {len(raw_text)}")
            db.commit()
            return document_id
    except Exception as error:
        mark_failed(document_id, "ocr", error)
        return None


def extract_with_gemini(document_id: int, batch_started_at: float):
    try:
        with SessionLocal() as db:
            document = db.get(Document, document_id)
            ocr_result = db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
            if not document or not ocr_result or document.status != "ocr_completed":
                return
            fields = db.scalars(
                select(TemplateField)
                .where(TemplateField.template_id == document.template_id)
                .order_by(TemplateField.sort_order)
            ).all()
            if not fields:
                raise ValueError("Template has no fields")
            document.status = "ai_processing"
            log(db, document_id, "ai", "info", "Gemini batch request started")
            db.commit()
            raw_text = ocr_result.raw_text
            field_data = [
                {
                    "field_key": field.field_key,
                    "field_type": field.field_type,
                    "description": field.description,
                    "required": field.required,
                }
                for field in fields
            ]

        data = GeminiProvider().extract(raw_text, field_data)
        validate_data(data, fields)

        with SessionLocal() as db:
            document = db.get(Document, document_id)
            record = db.scalar(
                select(Record).where(Record.document_id == document_id, Record.template_id == document.template_id)
            )
            if record:
                record.json_data, record.status = json.dumps(data, ensure_ascii=False), "completed"
            else:
                db.add(Record(
                    document_id=document_id,
                    template_id=document.template_id,
                    json_data=json.dumps(data, ensure_ascii=False),
                    status="completed",
                ))
            document.status, document.error_message = "completed", None
            elapsed_seconds = time.monotonic() - batch_started_at
            log(db, document_id, "complete", "info", f"Batch processing completed in {elapsed_seconds:.2f}s")
            db.commit()
    except Exception as error:
        mark_failed(document_id, "ai", error)


def process_batch(document_ids: list[int], ocr_executor: ThreadPoolExecutor):
    batch_started_at = time.monotonic()
    logger.info("Starting batch: %s documents, OCR concurrency=%s, Gemini concurrency=%s",
                len(document_ids), OCR_CONCURRENCY, GEMINI_CONCURRENCY)
    ocr_completed: list[int] = []
    futures = [ocr_executor.submit(extract_ocr, document_id) for document_id in document_ids]
    for future in as_completed(futures):
        document_id = future.result()
        if document_id:
            ocr_completed.append(document_id)

    logger.info("OCR batch completed: %s/%s documents; starting Gemini batch", len(ocr_completed), len(document_ids))
    with ThreadPoolExecutor(max_workers=GEMINI_CONCURRENCY, thread_name_prefix="gemini") as executor:
        futures = [executor.submit(extract_with_gemini, document_id, batch_started_at) for document_id in ocr_completed]
        for future in as_completed(futures):
            future.result()
    logger.info("Batch completed in %.2fs", time.monotonic() - batch_started_at)


def run():
    with ThreadPoolExecutor(max_workers=OCR_CONCURRENCY, thread_name_prefix="ocr") as ocr_executor:
        while True:
            document_ids = claim_pending_batch()
            if document_ids:
                process_batch(document_ids, ocr_executor)
            else:
                time.sleep(WORKER_POLL_SECONDS)


if __name__ == "__main__":
    run()
