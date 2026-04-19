from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.services.key_management import rotate_signing_key

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/rotate-key")
async def rotate_key(x_admin_token: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin endpoint disabled")

    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")

    _, encrypted = rotate_signing_key(settings.did_signing_key_passphrase)
    # Operator must persist this value back into .env and restart to activate new key.
    return {"did_signing_key_encrypted": encrypted}
