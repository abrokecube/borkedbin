import html
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import (
    FastAPI,
    Depends,
    File,
    Form,
    UploadFile,
    Request,
    HTTPException,
    status,
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    FileResponse,
    Response,
    JSONResponse,
)
from fastapi.templating import Jinja2Templates
import magic
import markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from markdown.treeprocessors import Treeprocessor
from pydantic import BaseModel, Field
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from database import ContentItem, ApiKey, init_db, get_session
from dependencies import verify_api_key, check_upload_permission
from utils import generate_unique_short_id, is_valid_url
from config import settings


logger = logging.getLogger(__name__)


class TextRequest(BaseModel):
    content: str
    title: Optional[str] = Field(None, max_length=80)
    expires_in: Optional[int] = None


class MarkdownRequest(BaseModel):
    content: str
    title: Optional[str] = Field(None, max_length=80)
    expires_in: Optional[int] = None


class UrlRequest(BaseModel):
    url: str
    title: Optional[str] = Field(None, max_length=80)
    expires_in: Optional[int] = None


class FileRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=80)
    expires_in: Optional[int] = None


class ContentCreatedResponse(BaseModel):
    id: str
    url: str


def get_uploads_root() -> Path:
    """Return the canonical directory used for server-controlled file storage."""
    uploads_root = Path(settings.uploads_dir).resolve()
    uploads_root.mkdir(parents=True, exist_ok=True)
    return uploads_root


def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    get_uploads_root()


def _remove_partial_upload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove partial upload %s", path, exc_info=True)


def _attachment_headers(filename: object) -> dict[str, str]:
    original = filename if isinstance(filename, str) and filename else "file"
    download_name = original.replace("\\", "/").rsplit("/", 1)[-1] or "file"
    encoded_name = quote(download_name.encode("utf-8", errors="replace"), safe="")
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
    }


def _safe_uploaded_media_type(metadata: dict, item_type: str) -> str:
    detected_type = metadata.get("magic_mime")
    if not isinstance(detected_type, str):
        return "application/octet-stream"
    detected_type = detected_type.lower().split(";", 1)[0].strip()
    if item_type == "image" and detected_type in SAFE_RASTER_IMAGE_TYPES:
        return detected_type
    if item_type == "audio" and detected_type.startswith("audio/"):
        return detected_type
    if item_type == "video" and detected_type.startswith("video/"):
        return detected_type
    return "application/octet-stream"


def _contained_stored_path(file_path: str) -> Optional[Path]:
    try:
        stored_path = Path(file_path).resolve()
        stored_path.relative_to(get_uploads_root())
    except (OSError, ValueError):
        return None
    return stored_path


def get_full_url(item_id: str) -> str:
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}/{item_id}"
    return f"/{item_id}"


def get_content_cache_headers(item: ContentItem) -> dict[str, str]:
    if not item.expires_at:
        # Cache for the configured maximum duration if it never expires
        return {
            "Cache-Control": f"public, max-age={settings.maximum_cache_ttl_seconds}"
        }

    expires_at = (
        item.expires_at.replace(tzinfo=timezone.utc)
        if item.expires_at.tzinfo is None
        else item.expires_at
    )
    time_to_live = (expires_at - datetime.now(timezone.utc)).total_seconds()
    if time_to_live >= settings.minimum_cache_ttl_seconds:
        cache_ttl = min(int(time_to_live), settings.maximum_cache_ttl_seconds)
        return {"Cache-Control": f"public, max-age={cache_ttl}"}
    else:
        return {"Cache-Control": "no-cache, no-store, must-revalidate"}


def _is_safe_markdown_url(value: str, attribute: str) -> bool:
    decoded = html.unescape(value).strip()
    normalized = "".join(
        character for character in decoded if ord(character) > 0x20 and ord(character) != 0x7F
    )
    scheme = urlsplit(normalized).scheme.lower()
    allowed_schemes = {"http", "https", "mailto"} if attribute == "href" else {"http", "https"}
    return not scheme or scheme in allowed_schemes


class _RememberGeneratedHtml(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        # The fenced-code extension has already stashed its generated HTML at
        # this priority. Anything stashed later came from user-authored HTML.
        self.md.generated_html_count = len(self.md.htmlStash.rawHtmlBlocks)
        return lines


class _SanitizeMarkdownTree(Treeprocessor):
    def run(self, root):
        for element in root.iter():
            for attribute in ("href", "src"):
                value = element.get(attribute)
                if value is not None and not _is_safe_markdown_url(value, attribute):
                    del element.attrib[attribute]

        generated_html_count = getattr(self.md, "generated_html_count", 0)
        for index in range(generated_html_count, len(self.md.htmlStash.rawHtmlBlocks)):
            raw_html = self.md.htmlStash.rawHtmlBlocks[index]
            self.md.htmlStash.rawHtmlBlocks[index] = html.escape(raw_html, quote=False)
        return root


class _SafeMarkdownExtension(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(
            _RememberGeneratedHtml(md), "remember_generated_html", 21
        )
        md.treeprocessors.register(
            _SanitizeMarkdownTree(md), "sanitize_markdown", 15
        )


def render_markdown(content: str) -> str:
    return markdown.markdown(
        content,
        extensions=["fenced_code", "tables", _SafeMarkdownExtension()],
    )


def new_content_id(session: Session) -> str:
    return generate_unique_short_id(
        lambda candidate: session.get(ContentItem, candidate) is not None,
        settings.id_length,
    )


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Bound request bodies before FastAPI parses JSON or multipart forms."""

    def __init__(self, app: ASGIApp):
        self.app = app

    @staticmethod
    def _limit_for_path(path: str) -> Optional[int]:
        if path == "/api/add/file":
            return MAX_FILE_SIZE + MULTIPART_OVERHEAD
        if path in {"/api/add/text", "/api/add/markdown", "/api/add/url"}:
            # A JSON string can encode one input byte as a six-byte escape.
            return MAX_TEXT_SIZE * 6 + JSON_BODY_OVERHEAD
        return None

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body_limit = self._limit_for_path(scope.get("path", ""))
        if body_limit is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > body_limit:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0
        limit_exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal limit_exceeded, received
            if limit_exceeded:
                raise _RequestBodyTooLarge

            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > body_limit:
                    limit_exceeded = True
                    raise _RequestBodyTooLarge
            return message

        async def filtered_send(message: Message) -> None:
            nonlocal response_started
            if limit_exceeded and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, filtered_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise

        if limit_exceeded and not response_started:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": "Request body exceeds the configured size limit"},
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )
        await response(scope, receive, send)


# Initialize the DB tables if not present
init_db()
ensure_uploads_dir()

app = FastAPI(title="BorkedBin - Public")


def format_size(size: int) -> str:
    size_float = float(size)
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if size_float < 1024:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024
    return f"{size_float:.1f} TB"


templates = Jinja2Templates(directory="templates")
templates.env.filters["format_size"] = format_size


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.count("/") == 1:
        return FileResponse(
            "static/404.html",
            status_code=404,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return Response(content="404 Not Found", media_type="text/plain", status_code=404)


MAX_BLOB_SIZE = settings.max_blob_size_kb * 1024  # from KB to Bytes
MAX_FILE_SIZE = settings.max_file_size_mb * 1024 * 1024
MAX_TEXT_SIZE = settings.max_text_size_mb * 1024 * 1024
NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}
MULTIPART_OVERHEAD = 1024 * 1024
JSON_BODY_OVERHEAD = 64 * 1024
UPLOAD_CHUNK_SIZE = 64 * 1024
SAFE_RASTER_IMAGE_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/x-icon",
}

app.add_middleware(RequestBodyLimitMiddleware)


@app.get("/styles.css", include_in_schema=False)
async def get_styles_css():
    return FileResponse(
        "static/public_styles.css", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/theme.css", include_in_schema=False)
async def get_theme_css():
    return FileResponse(
        "static/theme.css", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    return FileResponse(
        "static/favicon.ico", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/api/config")
async def get_config():
    return JSONResponse(
        {
            "max_file_size_mb": settings.max_file_size_mb,
            "max_text_size_mb": settings.max_text_size_mb,
        },
        headers=NO_CACHE_HEADERS,
    )


@app.get("/api/key_permissions")
async def get_key_permissions(api_key: ApiKey = Depends(verify_api_key)):
    if api_key.allowed_types == "*":
        return JSONResponse({"allowed_types": ["*"]}, headers=NO_CACHE_HEADERS)
    return JSONResponse(
        {"allowed_types": [t.strip() for t in api_key.allowed_types.split(",")]},
        headers=NO_CACHE_HEADERS,
    )


@app.post("/api/add/text", response_model=ContentCreatedResponse)
async def add_text(
    request: TextRequest,
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(verify_api_key),
):
    check_upload_permission(api_key, "text")
    content = request.content
    if len(content.encode("utf-8")) > MAX_TEXT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Text content exceeds the maximum allowed size of {settings.max_text_size_mb}MB",
        )
    item_id = new_content_id(session)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=request.expires_in)
        if request.expires_in
        else None
    )
    item = ContentItem(
        id=item_id,
        type="text",
        title=request.title or None,
        content_text=content,
        expires_at=expires_at,
    )
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/markdown", response_model=ContentCreatedResponse)
async def add_markdown(
    request: MarkdownRequest,
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(verify_api_key),
):
    check_upload_permission(api_key, "markdown")
    content = request.content
    if len(content.encode("utf-8")) > MAX_TEXT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Text content exceeds the maximum allowed size of {settings.max_text_size_mb}MB",
        )
    item_id = new_content_id(session)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=request.expires_in)
        if request.expires_in
        else None
    )
    item = ContentItem(
        id=item_id,
        type="markdown",
        title=request.title or None,
        content_text=content,
        expires_at=expires_at,
    )
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/url", response_model=ContentCreatedResponse)
async def add_url(
    request: UrlRequest,
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(verify_api_key),
):
    check_upload_permission(api_key, "url")
    url = request.url
    if not is_valid_url(url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL. Must be a well-formed http(s) URL",
        )

    if len(url.encode("utf-8")) > MAX_TEXT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"URL exceeds the maximum allowed size of {settings.max_text_size_mb}MB",
        )
    item_id = new_content_id(session)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=request.expires_in)
        if request.expires_in
        else None
    )
    item = ContentItem(
        id=item_id,
        type="url",
        title=request.title or None,
        content_text=url,
        expires_at=expires_at,
    )
    session.add(item)
    session.commit()
    return {"id": item_id, "url": get_full_url(item_id)}


@app.post("/api/add/file", response_model=ContentCreatedResponse)
async def add_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None, max_length=80),
    expires_in: Optional[int] = Form(None),
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(verify_api_key),
):
    item_id = new_content_id(session)
    storage_path: Optional[Path] = None
    committed = False

    try:
        uploads_root = get_uploads_root()
        size = 0
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"{item_id}-",
            suffix=".upload",
            dir=uploads_root,
            delete=False,
        ) as stored_file:
            storage_path = Path(stored_file.name).resolve()
            try:
                storage_path.relative_to(uploads_root)
            except ValueError as exc:
                raise OSError("Upload storage escaped the configured directory") from exc

            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            "File exceeds the maximum allowed size of "
                            f"{settings.max_file_size_mb}MB"
                        ),
                    )
                stored_file.write(chunk)

        magic_mime = magic.from_file(str(storage_path), mime=True)
        magic_description = magic.from_file(str(storage_path))
        original_filename = file.filename or "file"
        metadata = {
            "filename": original_filename,
            "client_content_type": file.content_type,
            "size": size,
            "magic_mime": magic_mime,
            "magic_description": magic_description,
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
                content_text = storage_path.read_text(encoding="utf-8")
                item_type = "markdown"
            except UnicodeDecodeError:
                pass
        elif magic_mime.startswith("text/") or magic_mime in (
            "application/json",
            "application/xml",
        ):
            try:
                content_text = storage_path.read_text(encoding="utf-8")
                item_type = "text"
            except UnicodeDecodeError:
                pass

        check_upload_permission(api_key, item_type)

        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=expires_in)
            if expires_in
            else None
        )
        item = ContentItem(
            id=item_id,
            type=item_type,
            title=title or None,
            metadata_json=json.dumps(metadata),
            expires_at=expires_at,
        )

        if item_type in ("text", "markdown"):
            item.content_text = content_text
            storage_path.unlink()
            storage_path = None
        elif size < MAX_BLOB_SIZE:
            item.content_blob = storage_path.read_bytes()
            storage_path.unlink()
            storage_path = None
        else:
            item.file_path = str(storage_path)

        session.add(item)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save uploaded file",
            ) from exc

        committed = True
        return {"id": item_id, "url": get_full_url(item_id)}
    except HTTPException:
        if storage_path is not None and not committed:
            _remove_partial_upload(storage_path)
        raise
    except Exception as exc:
        session.rollback()
        if storage_path is not None and not committed:
            _remove_partial_upload(storage_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded file",
        ) from exc
    finally:
        try:
            await file.close()
        except Exception:
            logger.warning("Failed to close uploaded file", exc_info=True)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(
        "static/index.html", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/upload", include_in_schema=False)
async def upload():
    return FileResponse(
        "static/upload.html", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/{item_id}", response_class=HTMLResponse, include_in_schema=False)
async def get_item(
    request: Request, item_id: str, session: Session = Depends(get_session)
):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at:
        expires_at = (
            item.expires_at.replace(tzinfo=timezone.utc)
            if item.expires_at.tzinfo is None
            else item.expires_at
        )
        if datetime.now(timezone.utc) >= expires_at:
            raise HTTPException(status_code=410, detail="Item has expired")

    cache_headers = get_content_cache_headers(item)

    if item.content_text is not None:
        if item.type == "text":
            content_size = len(item.content_text.encode("utf-8"))
            return templates.TemplateResponse(
                request,
                "text.html",
                {
                    "content": item.content_text,
                    "item": item,
                    "content_size": content_size,
                },
                headers=cache_headers,
            )

        elif item.type == "markdown":
            content_size = len(item.content_text.encode("utf-8"))
            html_content = render_markdown(item.content_text)
            markdown_headers = {
                **cache_headers,
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'self'; "
                    "img-src 'self' http: https:; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'"
                ),
            }
            return templates.TemplateResponse(
                request,
                "markdown.html",
                {
                    "html_content": html_content,
                    "item": item,
                    "content_size": content_size,
                },
                headers=markdown_headers,
            )

        elif item.type == "url":
            return RedirectResponse(
                item.content_text, status_code=307, headers=cache_headers
            )

    elif item.type == "image":
        metadata = item.metadata_dict
        return templates.TemplateResponse(
            request,
            "image.html",
            {"item": item, "metadata": metadata},
            headers=cache_headers,
        )

    elif item.type == "audio":
        metadata = item.metadata_dict
        return templates.TemplateResponse(
            request,
            "audio.html",
            {"item": item, "metadata": metadata},
            headers=cache_headers,
        )

    elif item.type == "video":
        metadata = item.metadata_dict
        return templates.TemplateResponse(
            request,
            "video.html",
            {"item": item, "metadata": metadata},
            headers=cache_headers,
        )

    elif item.type == "file":
        metadata = item.metadata_dict
        return templates.TemplateResponse(
            request,
            "file.html",
            {"item": item, "metadata": metadata},
            headers=cache_headers,
        )

    return Response(status_code=500, content="Unknown type")


@app.get("/raw/{item_id}")
async def get_raw_item(item_id: str, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.expires_at:
        expires_at = (
            item.expires_at.replace(tzinfo=timezone.utc)
            if item.expires_at.tzinfo is None
            else item.expires_at
        )
        if datetime.now(timezone.utc) >= expires_at:
            raise HTTPException(status_code=410, detail="Item has expired")

    cache_headers = get_content_cache_headers(item)

    if item.type in ("text", "markdown"):
        headers = {**cache_headers, "X-Content-Type-Options": "nosniff"}
        metadata = item.metadata_dict
        if "filename" in metadata:
            headers.update(_attachment_headers(metadata.get("filename")))
        return Response(
            content=item.content_text, media_type="text/plain", headers=headers
        )

    elif item.type == "url":
        return Response(
            content=item.content_text,
            media_type="text/plain",
            headers={**cache_headers, "X-Content-Type-Options": "nosniff"},
        )

    elif item.type in ("file", "image", "audio", "video"):
        metadata = item.metadata_dict
        content_type = _safe_uploaded_media_type(metadata, item.type)
        headers = {
            **cache_headers,
            **_attachment_headers(metadata.get("filename")),
            "X-Content-Type-Options": "nosniff",
        }
        if item.content_blob is not None:
            return Response(
                content=item.content_blob, media_type=content_type, headers=headers
            )
        elif item.file_path:
            stored_path = _contained_stored_path(item.file_path)
            if stored_path is not None and stored_path.is_file():
                return FileResponse(
                    stored_path,
                    media_type=content_type,
                    headers=headers,
                )
            raise HTTPException(status_code=404, detail="File data not found")
        else:
            raise HTTPException(status_code=404, detail="File data not found")

    raise HTTPException(
        status_code=400, detail="Raw content not supported for this type"
    )
