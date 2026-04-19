import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Dataset, DidLogEntry

router = APIRouter()


@router.get("/datasets/{dataset_uuid}/did.jsonl")
async def did_log(dataset_uuid: str, db: AsyncSession = Depends(get_db)):
    try:
        parsed_uuid = UUID(dataset_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Dataset UUID not found") from exc

    dataset = await db.scalar(select(Dataset).where(Dataset.id == parsed_uuid))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset UUID not found")

    result = await db.execute(
        select(DidLogEntry).where(DidLogEntry.dataset_id == dataset.id).order_by(DidLogEntry.version_number.asc())
    )
    entries = result.scalars().all()
    body = "\n".join(json.dumps(entry.log_entry, separators=(",", ":")) for entry in entries)
    return Response(content=body, media_type="application/jsonl")
