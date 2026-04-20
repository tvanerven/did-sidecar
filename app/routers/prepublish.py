import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Dataset, DidLogEntry, DidServiceEndpoint
from app.schemas.prepublish import PrepublishPayload
from app.services.dataverse import (
    fetch_dataset_metadata,
    release_workflow_lock,
    update_dataset_metadata_with_did,
)
from app.services.did_minting import build_did, create_genesis_log_entry, create_update_log_entry
from app.services.key_management import decrypt_signing_key

router = APIRouter()


def _is_major_version(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.0", version))


def _extract_services(log_entry: dict) -> list[dict]:
    return list(log_entry.get("state", {}).get("service", []))


@router.post("/prepublish")
async def prepublish(
    payload: PrepublishPayload,
    db: AsyncSession = Depends(get_db),
    x_dataverse_workflow_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    if settings.dataverse_workflow_token:
        presented = x_dataverse_workflow_token
        if not presented and authorization and authorization.lower().startswith("bearer "):
            presented = authorization.split(" ", 1)[1]
        if presented != settings.dataverse_workflow_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid workflow token")

    try:
        _ = fetch_dataset_metadata(settings.dataverse_url, settings.dataverse_api_token, payload.datasetGlobalId)

        result = await db.execute(select(Dataset).where(Dataset.dataverse_pid == payload.datasetGlobalId))
        dataset = result.scalar_one_or_none()
        signing_key = decrypt_signing_key(settings.did_signing_key_encrypted, settings.did_signing_key_passphrase)

        if dataset is None:
            dataset = Dataset(
                dataverse_pid=payload.datasetGlobalId,
                did="",
                pid_url=payload.datasetGlobalId,
            )
            db.add(dataset)
            await db.flush()

            dataset.did = build_did(payload.datasetGlobalId)

            genesis = create_genesis_log_entry(
                did=dataset.did,
                global_id_url=payload.datasetGlobalId,
                signing_key=signing_key,
            )
            did_log = DidLogEntry(
                dataset_id=dataset.id,
                version_number=1,
                dataverse_version=payload.datasetVersion,
                log_entry=genesis,
            )
            db.add(did_log)
            await db.flush()

            for service in _extract_services(genesis):
                db.add(
                    DidServiceEndpoint(
                        dataset_id=dataset.id,
                        log_entry_id=did_log.id,
                        endpoint_id=service["id"],
                        endpoint_type=service["type"],
                        endpoint_url=service["serviceEndpoint"],
                    )
                )
        elif _is_major_version(payload.datasetVersion):
            latest_version = await db.scalar(
                select(func.max(DidLogEntry.version_number)).where(DidLogEntry.dataset_id == dataset.id)
            )
            next_version = (latest_version or 1) + 1
            update_entry = create_update_log_entry(
                did=dataset.did,
                global_id_url=payload.datasetGlobalId,
                version_number=next_version,
                dataverse_version=payload.datasetVersion,
                signing_key=signing_key,
            )
            did_log = DidLogEntry(
                dataset_id=dataset.id,
                version_number=next_version,
                dataverse_version=payload.datasetVersion,
                log_entry=update_entry,
            )
            db.add(did_log)
            await db.flush()

            for service in _extract_services(update_entry):
                db.add(
                    DidServiceEndpoint(
                        dataset_id=dataset.id,
                        log_entry_id=did_log.id,
                        endpoint_id=service["id"],
                        endpoint_type=service["type"],
                        endpoint_url=service["serviceEndpoint"],
                    )
                )

        update_dataset_metadata_with_did(
            dataverse_url=settings.dataverse_url,
            api_token=settings.dataverse_api_token,
            dataset_global_id=payload.datasetGlobalId,
            did=dataset.did,
        )
        callback_url = f"{settings.dataverse_url.rstrip('/')}/api/workflows/{payload.invocationId}"
        await db.commit()
        await release_workflow_lock(callback_url, success=True)
        return {"status": "ok", "dataset_uuid": str(dataset.id), "did": dataset.did}
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        reason = str(exc)
        callback_url = f"{settings.dataverse_url.rstrip('/')}/api/workflows/{payload.invocationId}"
        try:
            await release_workflow_lock(callback_url, success=False, reason=reason)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Prepublish failed: {reason}") from exc
