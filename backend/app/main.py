import hashlib
import json
import mimetypes
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from openpyxl import Workbook

from .config import EXPORT_DIR, SUPPORTED_EXTENSIONS, UPLOAD_DIR
from .database import Base, engine, get_db
from .models import Document, OCRResult, ProcessingLog, Record, Template, TemplateField
from .schemas import RecordUpdate, TemplateInput, TemplateOutput

app = FastAPI(title="Document AI")


@app.on_event("startup")
def startup():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def template_or_404(template_id: int, db: Session) -> Template:
    template = db.scalar(select(Template).options(selectinload(Template.fields)).where(Template.id == template_id))
    if not template:
        raise HTTPException(404, "Template not found")
    return template


def assign_template(template: Template, payload: TemplateInput):
    template.name, template.description = payload.name, payload.description
    template.fields.clear()
    for index, field in enumerate(payload.fields):
        template.fields.append(TemplateField(**field.model_dump(exclude={"sort_order"}), sort_order=field.sort_order or index))


def safe_relative_path(value: str, fallback: str) -> PurePosixPath:
    value = value.replace("\\", "/").strip("/")
    path = PurePosixPath(value or fallback)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise HTTPException(400, "Invalid relative path")
    return path


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/templates", response_model=list[TemplateOutput])
def list_templates(db: Session = Depends(get_db)):
    return db.scalars(select(Template).options(selectinload(Template.fields)).order_by(Template.name)).all()


@app.post("/api/templates", response_model=TemplateOutput, status_code=201)
def create_template(payload: TemplateInput, db: Session = Depends(get_db)):
    if db.scalar(select(Template).where(Template.name == payload.name)):
        raise HTTPException(409, "Template name already exists")
    template = Template()
    assign_template(template, payload)
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@app.get("/api/templates/{template_id}", response_model=TemplateOutput)
def get_template(template_id: int, db: Session = Depends(get_db)):
    return template_or_404(template_id, db)


@app.put("/api/templates/{template_id}", response_model=TemplateOutput)
def update_template(template_id: int, payload: TemplateInput, db: Session = Depends(get_db)):
    template = template_or_404(template_id, db)
    duplicate = db.scalar(select(Template).where(Template.name == payload.name, Template.id != template_id))
    if duplicate:
        raise HTTPException(409, "Template name already exists")
    assign_template(template, payload)
    db.commit()
    db.refresh(template)
    return template


@app.delete("/api/templates/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = template_or_404(template_id, db)
    if db.scalar(select(Document).where(Document.template_id == template_id).limit(1)):
        raise HTTPException(409, "Template is used by uploaded documents")
    db.delete(template)
    db.commit()


@app.post("/api/files/upload")
async def upload_files(
    template_id: Annotated[int, Form()],
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    db: Session = Depends(get_db),
):
    template_or_404(template_id, db)
    if len(files) != len(relative_paths):
        raise HTTPException(400, "Each file needs one relative path")
    uploaded, skipped, duplicates = [], [], []
    for file, supplied_path in zip(files, relative_paths):
        filename = Path(file.filename or "").name
        relative_path = safe_relative_path(supplied_path, filename)
        suffix = Path(filename).suffix.lower()
        if filename in {".DS_Store"} or "__MACOSX" in relative_path.parts:
            skipped.append(str(relative_path))
            continue
        if suffix not in SUPPORTED_EXTENSIONS:
            skipped.append(str(relative_path))
            continue
        target = UPLOAD_DIR / hashlib.sha256(str(relative_path).encode()).hexdigest()[:16] / relative_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                output.write(chunk)
        checksum = digest.hexdigest()
        existing = db.scalar(select(Document).where(Document.sha256 == checksum))
        if existing:
            target.unlink(missing_ok=True)
            duplicates.append({"path": str(relative_path), "document_id": existing.id})
            continue
        parts = relative_path.parts[:-1]
        document = Document(
            filename=filename, relative_path=str(relative_path), file_path=str(target), file_type=suffix[1:].upper(),
            mime_type=file.content_type or mimetypes.guess_type(filename)[0] or "", file_size=size, sha256=checksum,
            folder_level_1=parts[0] if parts else None, folder_level_2=parts[1] if len(parts) > 1 else None,
            status="pending", template_id=template_id,
        )
        db.add(document)
        db.flush()
        uploaded.append({"id": document.id, "path": str(relative_path)})
    db.commit()
    return {"uploaded": uploaded, "skipped": skipped, "duplicates": duplicates}


@app.post("/api/files/process")
def process_files(document_ids: list[int], db: Session = Depends(get_db)):
    documents = db.scalars(select(Document).where(Document.id.in_(document_ids))).all()
    for document in documents:
        if document.status in {"failed", "skipped", "completed"}:
            document.status, document.error_message = "pending", None
    db.commit()
    return {"queued": len(documents)}


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
    documents = db.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return [{"id": d.id, "filename": d.filename, "relative_path": d.relative_path, "file_type": d.file_type,
             "status": d.status, "template_id": d.template_id, "created_at": d.created_at, "updated_at": d.updated_at,
             "error_message": d.error_message} for d in documents]


@app.get("/api/documents/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")
    return {"id": document.id, "filename": document.filename, "relative_path": document.relative_path,
            "status": document.status, "raw_text": document.ocr_result.raw_text if document.ocr_result else None,
            "error_message": document.error_message}


@app.get("/api/documents/{document_id}/file")
def download_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document or not Path(document.file_path).is_file():
        raise HTTPException(404, "Document file not found")
    return FileResponse(document.file_path, filename=document.filename, media_type=document.mime_type)


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")
    if document.status in {"processing", "ai_processing"}:
        raise HTTPException(409, "A processing document cannot be deleted; retry after processing finishes")

    file_path = Path(document.file_path).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if not file_path.is_relative_to(upload_root):
        raise HTTPException(500, "Document path is outside the upload directory")

    db.query(ProcessingLog).filter(ProcessingLog.document_id == document_id).delete()
    db.delete(document)
    db.commit()
    file_path.unlink(missing_ok=True)
    return None


@app.get("/api/records")
def list_records(db: Session = Depends(get_db)):
    records = db.scalars(select(Record).order_by(Record.updated_at.desc())).all()
    return [{"id": r.id, "document_id": r.document_id, "template_id": r.template_id, "status": r.status,
             "json_data": json.loads(r.json_data), "updated_at": r.updated_at} for r in records]


@app.get("/api/records/{record_id}")
def get_record(record_id: int, db: Session = Depends(get_db)):
    record = db.get(Record, record_id)
    if not record:
        raise HTTPException(404, "Record not found")
    return {"id": record.id, "document_id": record.document_id, "template_id": record.template_id,
            "json_data": json.loads(record.json_data), "status": record.status}


@app.put("/api/records/{record_id}")
def update_record(record_id: int, payload: RecordUpdate, db: Session = Depends(get_db)):
    record = db.get(Record, record_id)
    if not record:
        raise HTTPException(404, "Record not found")
    template = template_or_404(record.template_id, db)
    allowed = {field.field_key for field in template.fields}
    if set(payload.json_data) != allowed:
        raise HTTPException(422, "Record keys must exactly match the template fields")
    record.json_data = json.dumps(payload.json_data, ensure_ascii=False)
    db.commit()
    return {"id": record.id, "json_data": payload.json_data}


@app.get("/api/export/excel")
def export_excel(template_id: int, db: Session = Depends(get_db)):
    template = template_or_404(template_id, db)
    records = db.scalars(select(Record).where(Record.template_id == template_id, Record.status == "completed")).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = template.name[:31]
    fields = sorted(template.fields, key=lambda field: field.sort_order)
    sheet.append(["folder_level_1", "folder_level_2", "filename", *[field.field_name for field in fields]])
    for record in records:
        document = db.get(Document, record.document_id)
        data = json.loads(record.json_data)
        sheet.append([document.folder_level_1 or "", document.folder_level_2 or "", document.filename,
                      *[data.get(field.field_key) for field in fields]])
    output = EXPORT_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', template.name)}-{datetime.utcnow():%Y%m%d%H%M%S}.xlsx"
    workbook.save(output)
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
