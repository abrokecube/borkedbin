import os
from fastapi import FastAPI, Depends, Form, File, UploadFile, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
import markdown
import json

from database import ContentItem, ApiKey, init_db, get_session
from dependencies import verify_api_key
from utils import generate_short_id, ensure_uploads_dir

# Initialize the DB tables if not present
init_db()
ensure_uploads_dir()

app = FastAPI(title="Brokebin - Public")

# Set up templates
if not os.path.exists("templates"):
    os.makedirs("templates")
templates = Jinja2Templates(directory="templates")

MAX_BLOB_SIZE = 100 * 1024  # 100 KB

@app.post("/api/add/text")
async def add_text(content: str = Form(...), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    item = ContentItem(id=item_id, type="text", content_text=content)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": f"/{item_id}"}


@app.post("/api/add/markdown")
async def add_markdown(content: str = Form(...), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    item = ContentItem(id=item_id, type="markdown", content_text=content)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": f"/{item_id}"}


@app.post("/api/add/url")
async def add_url(url: str = Form(...), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    item = ContentItem(id=item_id, type="url", content_text=url)
    session.add(item)
    session.commit()
    return {"id": item_id, "url": f"/{item_id}"}


@app.post("/api/add/image")
async def add_image(file: UploadFile = File(...), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    content = await file.read()
    size = len(content)
    
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": size
    }
    
    item = ContentItem(id=item_id, type="image", metadata_json=json.dumps(metadata))
    
    if size < MAX_BLOB_SIZE:
        item.content_blob = content
    else:
        file_path = os.path.join("uploads", f"{item_id}_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        item.file_path = file_path

    session.add(item)
    session.commit()
    return {"id": item_id, "url": f"/{item_id}"}


@app.post("/api/add/file")
async def add_file(file: UploadFile = File(...), session: Session = Depends(get_session), api_key: ApiKey = Depends(verify_api_key)):
    item_id = generate_short_id()
    content = await file.read()
    size = len(content)
    
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": size
    }
    
    item = ContentItem(id=item_id, type="file", metadata_json=json.dumps(metadata))
    
    if size < MAX_BLOB_SIZE:
        item.content_blob = content
    else:
        file_path = os.path.join("uploads", f"{item_id}_{file.filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        item.file_path = file_path

    session.add(item)
    session.commit()
    return {"id": item_id, "url": f"/{item_id}"}


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
