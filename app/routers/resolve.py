from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Dataset, DidServiceEndpoint

router = APIRouter()


@router.get("/resolve/{dataset_uuid}")
async def resolve_dataset(dataset_uuid: str, db: AsyncSession = Depends(get_db)):
    try:
        parsed_uuid = UUID(dataset_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Dataset UUID not found") from exc

    dataset = await db.scalar(select(Dataset).where(Dataset.id == parsed_uuid))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset UUID not found")

    endpoint = await db.scalar(
        select(DidServiceEndpoint)
        .where(DidServiceEndpoint.dataset_id == dataset.id)
        .order_by(DidServiceEndpoint.id.desc())
        .limit(1)
    )
    if not endpoint:
        raise HTTPException(status_code=404, detail="No service endpoint found")

    return RedirectResponse(url=endpoint.endpoint_url, status_code=302)
