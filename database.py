import os
import json
from datetime import datetime, timezone
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Session, create_engine

# Path to the sqlite database
DATABASE_URL = "sqlite:///./db.sqlite"

# Ensure the database engine uses connect_args for sqlite to allow multi-threading in fastapi
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

class ApiKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    name: str = Field(default="Default Key")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = Field(default=True)

class ContentItem(SQLModel, table=True):
    id: str = Field(primary_key=True)  # Short alphanumeric ID
    type: str = Field(index=True)      # 'text', 'markdown', 'image', 'url', 'file'
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
    
    # # Create a default API key if none exists for easy testing
    # with Session(engine) as session:
    #     if not session.query(ApiKey).first():
    #         default_key = ApiKey(key="default_secret_key", name="Initial Key")
    #         session.add(default_key)
    #         session.commit()

def get_session():
    with Session(engine) as session:
        yield session
