from fastapi import Depends, HTTPException, status, Header
from sqlmodel import Session
from database import engine, ApiKey

def get_session():
    with Session(engine) as session:
        yield session

def verify_api_key(x_api_key: str = Header(None), session: Session = Depends(get_session)):
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key header: X-API-Key",
        )
    key_record = session.query(ApiKey).filter(ApiKey.key == x_api_key, ApiKey.is_active == True).first()
    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API Key",
        )
    return key_record
