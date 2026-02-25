import os
from fastapi import FastAPI, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlmodel import Session, select
from typing import List

from database import ContentItem, ApiKey, get_session
from utils import generate_short_id

app = FastAPI(title="Brokebin - Admin")

templates = Jinja2Templates(directory="templates")

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def admin_dashboard(request: Request, session: Session = Depends(get_session)):
    items = session.exec(select(ContentItem).order_by(ContentItem.created_at.desc())).all()
    keys = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    
    # Calculate some basic stats
    total_items = len(items)
    total_keys = len(keys)
    active_keys = sum(1 for k in keys if k.is_active)
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "items": items,
        "keys": keys,
        "stats": {
            "total_items": total_items,
            "total_keys": total_keys,
            "active_keys": active_keys
        }
    })

@app.post("/items/{item_id}/delete")
async def delete_item(item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    # Delete file from disk if it exists
    if item.file_path and os.path.exists(item.file_path):
        try:
            os.remove(item.file_path)
        except OSError:
            pass # Best effort
            
    session.delete(item)
    session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/keys/create")
async def create_key(name: str = Form(...), session: Session = Depends(get_session)):
    new_key = generate_short_id(32) # longer for API keys
    key_record = ApiKey(key=new_key, name=name)
    session.add(key_record)
    session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/keys/{key_id}/toggle")
async def toggle_key(key_id: int, session: Session = Depends(get_session)):
    key_record = session.get(ApiKey, key_id)
    if not key_record:
        raise HTTPException(status_code=404, detail="Key not found")
        
    key_record.is_active = not key_record.is_active
    session.add(key_record)
    session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/keys/{key_id}/delete")
async def delete_key(key_id: int, session: Session = Depends(get_session)):
    key_record = session.get(ApiKey, key_id)
    if not key_record:
        raise HTTPException(status_code=404, detail="Key not found")
        
    session.delete(key_record)
    session.commit()
    return RedirectResponse(url="/", status_code=303)
