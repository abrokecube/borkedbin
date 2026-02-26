import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, Form, File, UploadFile, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
import markdown
import json
import magic

from database import ContentItem, ApiKey, init_db, get_session
from dependencies import verify_api_key, check_upload_permission
from utils import generate_short_id, is_valid_url
from config import settings

def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    if not os.path.exists(settings.uploads_dir):
        os.makedirs(settings.uploads_dir)

def get_full_url(item_id: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}/{item_id}"
    return f"/{item_id}"

def get_content_cache_headers(item: ContentItem) -> dict[str, str]:
    if not item.expires_at:
        # Cache for the configured maximum duration if it never expires
        return {"Cache-Control": f"public, max-age={settings.maximum_cache_ttl_seconds}"}
    
    expires_at = item.expires_at.replace(tzinfo=timezone.utc) if item.expires_at.tzinfo is None else item.expires_at
    time_to_live = (expires_at - datetime.now(timezone.utc)).total_seconds()
    if time_to_live >= settings.minimum_cache_ttl_seconds:
        cache_ttl = min(int(time_to_live), settings.maximum_cache_ttl_seconds)
        return {"Cache-Control": f"public, max-age={cache_ttl}"}
    else:
        return {"Cache-Control": "no-cache, no-store, must-revalidate"}

# Initialize the DB tables if not present
init_db()
ensure_uploads_dir()

app = FastAPI(title="BorkedBin - Public")

templates = Jinja2Templates(directory="templates")

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.count("/") == 1:
        return FileResponse("static/404.html", status_code=404, headers={"Cache-Control": "public, max-age=3600"})
    return Response(content="404 Not Found", media_type="text/plain", status_code=404)

MAX_BLOB_SIZE = settings.max_blob_size_kb * 1024  # from KB to Bytes
MAX_FILE_SIZE = settings.max_file_size_mb * 1024 * 1024
MAX_TEXT_SIZE = settings.max_text_size_mb * 1024 * 1024
NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}

@app.get("/styles.css", include_in_schema=False)
async def get_styles_css():
    return FileResponse("static/public_styles.css", headers={"Cache-Control": "public, max-age=3600"})

@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "max_file_size_mb": settings.max_file_size_mb,
        "max_text_size_mb": settings.max_text_size_mb
    }, headers=NO_CACHE_HEADERS)

@app.get("/api/key_permissions")
async def get_key_permissions(api_key: ApiKey = Depends(verify_api_key)):
    if api_key.allowed_types == "*":
        return JSONResponse({"allowed_types": ["*"]}, headers=NO_CACHE_HEADERS)
    return JSONResponse({"allowed_types": [t.strip() for t in api_key.allowed_types.split(",")]}, headers=NO_CACHE_HEADERS)


@app.post("/api/add/text")
async def add_text(content: str = Form(...), title: Optional[str] = Form(None), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    check_upload_permission(api_key, "text")
    if len(content.encode('utf-8')) > MAX_TEXT_SIZE:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"Text content exceeds the maximum allowed size of {settings.max_text_size_mb}MB")
    item_id = generate_short_id(settings.id_length)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="text", title=title or None, content_text=content, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/markdown")
async def add_markdown(content: str = Form(...), title: Optional[str] = Form(None), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    check_upload_permission(api_key, "markdown")
    if len(content.encode('utf-8')) > MAX_TEXT_SIZE:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"Text content exceeds the maximum allowed size of {settings.max_text_size_mb}MB")
    item_id = generate_short_id(settings.id_length)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="markdown", title=title or None, content_text=content, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/url")
async def add_url(url: str = Form(...), title: Optional[str] = Form(None), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    check_upload_permission(api_key, "url")
    # Basic syntactic validation for submitted URLs
    if not is_valid_url(url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid URL. Must be a well-formed http(s) URL")

    if len(url.encode('utf-8')) > MAX_TEXT_SIZE:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"URL exceeds the maximum allowed size of {settings.max_text_size_mb}MB")
    item_id = generate_short_id(settings.id_length)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    item = ContentItem(id=item_id, type="url", title=title or None, content_text=url, expires_at=expires_at)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/file")
async def add_file(file: UploadFile = File(...), title: Optional[str] = Form(None), expires_in: Optional[int] = Form(None), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    content = await file.read()
    size = len(content)
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"File exceeds the maximum allowed size of {settings.max_file_size_mb}MB")
        
    item_id = generate_short_id(settings.id_length)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in) if expires_in else None
    
    # Detect file type using python-magic
    magic_mime = magic.from_buffer(content, mime=True)
    magic_description = magic.from_buffer(content)
    
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": size,
        "magic_mime": magic_mime,
        "magic_description": magic_description
    }
    
    item_type = "file"
    content_text = None
    
    if magic_mime.startswith("image/"):
        item_type = "image"
    elif magic_mime.startswith("audio/"):
        item_type = "audio"
    elif magic_mime.startswith("video/"):
        item_type = "video"
    elif magic_mime in ("text/markdown", "text/x-markdown"):
        try:
            content_text = content.decode("utf-8")
            item_type = "markdown"
        except UnicodeDecodeError:
            pass
    elif magic_mime.startswith("text/") or magic_mime in ("application/json", "application/xml"):
        try:
            content_text = content.decode("utf-8")
            item_type = "text"
        except UnicodeDecodeError:
            pass

    check_upload_permission(api_key, item_type)

    item = ContentItem(id=item_id, type=item_type, title=title or None, metadata_json=json.dumps(metadata), expires_at=expires_at)
    
    if item_type in ("text", "markdown"):
        item.content_text = content_text
    elif size < MAX_BLOB_SIZE:
        item.content_blob = content
    else:
        file_path = os.path.join(settings.uploads_dir, f"{item_id}_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        item.file_path = file_path

    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse("static/index.html", headers={"Cache-Control": "public, max-age=3600"})

@app.get("/upload", include_in_schema=False)
async def upload():
    return FileResponse("static/upload.html", headers={"Cache-Control": "public, max-age=3600"})

@app.get("/{item_id}", response_class=HTMLResponse, include_in_schema=False)
async def get_item(request: Request, item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at:
        expires_at = item.expires_at.replace(tzinfo=timezone.utc) if item.expires_at.tzinfo is None else item.expires_at
        if datetime.now(timezone.utc) >= expires_at:
            raise HTTPException(status_code=410, detail="Item has expired")

    cache_headers = get_content_cache_headers(item)

    if item.content_text is not None:
        if item.type == "text":
            return templates.TemplateResponse("text.html", {"request": request, "content": item.content_text, "item": item}, headers=cache_headers)
        
        elif item.type == "markdown":
            html_content = markdown.markdown(item.content_text, extensions=['fenced_code', 'tables'])
            return templates.TemplateResponse("markdown.html", {"request": request, "html_content": html_content, "item": item}, headers=cache_headers)
            
        elif item.type == "url":
            return RedirectResponse(item.content_text, status_code=307, headers=cache_headers)
        
    elif item.type == "image":
        return templates.TemplateResponse("image.html", {"request": request, "item": item}, headers=cache_headers)
    
    elif item.type == "audio":
        metadata = item.metadata_dict
        return templates.TemplateResponse("audio.html", {"request": request, "item": item, "metadata": metadata}, headers=cache_headers)
    
    elif item.type == "video":
        metadata = item.metadata_dict
        return templates.TemplateResponse("video.html", {"request": request, "item": item, "metadata": metadata}, headers=cache_headers)
        
    elif item.type == "file":
        metadata = item.metadata_dict
        return templates.TemplateResponse("file.html", {"request": request, "item": item, "metadata": metadata}, headers=cache_headers)

    return Response(status_code=500, content="Unknown type")

@app.get("/raw/{item_id}")
async def get_raw_item(item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at:
        expires_at = item.expires_at.replace(tzinfo=timezone.utc) if item.expires_at.tzinfo is None else item.expires_at
        if datetime.now(timezone.utc) >= expires_at:
            raise HTTPException(status_code=410, detail="Item has expired")

    cache_headers = get_content_cache_headers(item)

    if item.type in ("text", "markdown"):
        return Response(content=item.content_text, media_type="text/plain", headers=cache_headers)
    
    elif item.type == "url":
        return Response(content=item.content_text, media_type="text/plain", headers=cache_headers)
        
    elif item.type in ("file", "image", "audio", "video"):
        metadata = item.metadata_dict
        content_type = metadata.get("content_type", "application/octet-stream")
        if item.content_blob is not None:
            headers = {'Content-Disposition': f'attachment; filename="{metadata.get("filename", "file")}"'}
            headers.update(cache_headers)
            return Response(content=item.content_blob, media_type=content_type, headers=headers)
        elif item.file_path and os.path.exists(item.file_path):
            return FileResponse(item.file_path, media_type=content_type, headers=cache_headers)
        else:
            raise HTTPException(status_code=404, detail="File data not found")

    raise HTTPException(status_code=400, detail="Raw content not supported for this type")
