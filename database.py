import os
import json
from datetime import datetime, timezone
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Session, create_engine
from config import settings

DATABASE_URL = settings.database_url

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

class ApiKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    name: str = Field(default="Default Key")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)
    allowed_types: str = Field(default="*") # Comma-separated list or '*' for all

class ContentItem(SQLModel, table=True):
    id: str = Field(primary_key=True)  # Short alphanumeric ID
    type: str = Field(index=True)      # 'text', 'markdown', 'image', 'audio', 'video', 'url', 'file'
    title: Optional[str] = Field(default=None)  # Optional display title
    content_text: Optional[str] = Field(default=None) # For text, markdown, url
    content_blob: Optional[bytes] = Field(default=None) # For files < 100kb
    file_path: Optional[str] = Field(default=None)      # For files >= 100kb
    metadata_json: str = Field(default="{}")            # Stored as JSON string
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(default=None)

    @property
    def metadata_dict(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)

    @metadata_dict.setter
    def metadata_dict(self, value: dict[str, Any]):
        self.metadata_json = json.dumps(value)


def init_db():
    SQLModel.metadata.create_all(engine)
    
def get_session():
    with Session(engine) as session:
        yield session
