import os
import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Public server and admin server ports
    public_host: str = Field(default="0.0.0.0", description="Host to bind the public server to")
    public_port: int = Field(default=8000, description="Port for the public server")
    public_base_url: str = Field(default="", description="Base URL for generated public links (e.g. https://borkedbin.com)")
    admin_host: str = Field(default="127.0.0.1", description="Host to bind the admin server to")
    admin_port: int = Field(default=8001, description="Port for the admin server")
    
    # DB and File Storage configuration
    database_url: str = Field(default="sqlite:///./db.sqlite", description="SQLAlchemy connection URI for SQLite")
    uploads_dir: str = Field(default="uploads", description="Directory to store uploaded large files")
    max_blob_size_kb: int = Field(default=100, description="Max size in KB for storing files directly in the DB")
    max_file_size_mb: int = Field(default=50, description="Max size in MB for file/image uploads")
    max_text_size_mb: int = Field(default=10, description="Max size in MB for text/markdown uploads")
    
    # Pruning task
    prune_interval_seconds: int = Field(default=60, description="How often the expired item pruning loop runs (seconds)")

    # Caching
    minimum_cache_ttl_seconds: int = Field(default=7200, description="Minimum time to expiry before caching is allowed (seconds). E.g. Cloudflare free tier min TTL is 2 hours (7200s).")
    maximum_cache_ttl_seconds: int = Field(default=86400, description="Maximum cache duration allowed (seconds). Default 24 hours.")

    # ID generation
    id_length: int = Field(default=8, description="Length of generated IDs")

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore", 
        env_prefix="BORKEDBIN_"  # E.g., BORKEDBIN_PUBLIC_PORT
    )

    @classmethod
    def load_config(cls, yaml_path: str = "config.yaml"):
        yaml_config = {}
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, dict):
                        yaml_config = content
            except Exception as e:
                import logging
                logging.warning(f"Failed to parse {yaml_path}: {e}")
                pass

def get_settings(yaml_path: str = "config.yaml") -> Settings:
    yaml_config = {}
    if os.path.exists(yaml_path):
         try:
             with open(yaml_path, "r", encoding="utf-8") as f:
                 content = yaml.safe_load(f)
                 if isinstance(content, dict):
                     yaml_config = content
         except Exception as e:
             import logging
             logging.warning(f"Failed to parse {yaml_path}: {e}")
             
    env_settings = Settings() 
        
    for key, value in yaml_config.items():
        env_var_name = f"BORKEDBIN_{key.upper()}"
        if env_var_name not in os.environ:
            # Override with yaml if not in env
            if hasattr(env_settings, key):
                setattr(env_settings, key, value)
                
    return env_settings

settings = get_settings()
