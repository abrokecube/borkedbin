import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, Form, File, UploadFile, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
import markdown
import json

from database import ContentItem, ApiKey, init_db, get_session
from dependencies import verify_api_key
from utils import generate_short_id
from config import settings

def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    if not os.path.exists(settings.uploads_dir):
        os.makedirs(settings.uploads_dir)

def get_full_url(item_id: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}/{item_id}"
    return f"/{item_id}"

# Initialize the DB tables if not present
init_db()
ensure_uploads_dir()

app = FastAPI(title="Brokebin - Public")

# Set up templates
if not os.path.exists("templates"):
    os.makedirs("templates")
templates = Jinja2Templates(directory="templates")

MAX_BLOB_SIZE = settings.max_blob_size_kb * 1024  # from KB to Bytes

@app.post("/api/add/text")
async def add_text(content: str = Form(...), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="text", content_text=content, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/markdown")
async def add_markdown(content: str = Form(...), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="markdown", content_text=content, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/url")
async def add_url(url: str = Form(...), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="url", content_text=url, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/image")
async def add_image(file: UploadFile = File(...), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    content = await file.read()
    size = len(content)
    
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": size
    }
    
    item = ContentItem(id=item_id, type="image", metadata_json=json.dumps(metadata), expires_at=expires_at)
    
    if size < MAX_BLOB_SIZE:
        item.content_blob = content
    else:
        file_path = os.path.join(settings.uploads_dir, f"{item_id}_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        item.file_path = file_path

    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/file")
async def add_file(file: UploadFile = File(...), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    content = await file.read()
    size = len(content)
    
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": size
    }
    
    item = ContentItem(id=item_id, type="file", metadata_json=json.dumps(metadata), expires_at=expires_at)
    
    if size < MAX_BLOB_SIZE:
        item.content_blob = content
    else:
        file_path = os.path.join(settings.uploads_dir, f"{item_id}_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        item.file_path = file_path

    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/upload", response_class=HTMLResponse)
async def upload(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})

@app.get("/{item_id}", response_class=HTMLResponse)
async def get_item(request: Request, item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at and datetime.now(timezone.utc) >= item.expires_at:
        raise HTTPException(status_code=410, detail="Item has expired")

    if item.type == "text":
        return templates.TemplateResponse("text.html", {"request": request, "content": item.content_text, "item": item})
    
    elif item.type == "markdown":
        html_content = markdown.markdown(item.content_text, extensions=['fenced_code', 'tables'])
        return templates.TemplateResponse("markdown.html", {"request": request, "html_content": html_content, "item": item})
        
    elif item.type == "url":
        return RedirectResponse(item.content_text, status_code=307)
        
    elif item.type == "image":
        return templates.TemplateResponse("image.html", {"request": request, "item": item})
        
    elif item.type == "file":
        metadata = item.metadata_dict
        return templates.TemplateResponse("file.html", {"request": request, "item": item, "metadata": metadata})

    return Response(status_code=500, content="Unknown type")

@app.get("/download/{item_id}")
async def download_item(item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at and datetime.now(timezone.utc) >= item.expires_at:
        raise HTTPException(status_code=410, detail="Item has expired")

    if item.type not in ("file", "image"):
        raise HTTPException(status_code=400, detail="Not a downloadable file")

    metadata = item.metadata_dict
    filename = metadata.get("filename", item_id)
    content_type = metadata.get("content_type", "application/octet-stream")

    if item.content_blob is not None:
        headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
        return Response(content=item.content_blob, media_type=content_type, headers=headers)
    elif item.file_path and os.path.exists(item.file_path):
        return FileResponse(item.file_path, filename=filename, media_type=content_type)
    else:
        raise HTTPException(status_code=404, detail="File data not found")
