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
    expected = settings.dataverse_workflow_token
    presented = x_dataverse_workflow_token
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1]

    if presented != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid workflow token")

    try:
        _ = fetch_dataset_metadata(settings.dataverse_url, settings.dataverse_api_token, payload.datasetPid)

        result = await db.execute(select(Dataset).where(Dataset.dataverse_pid == payload.datasetPid))
        dataset = result.scalar_one_or_none()
        signing_key = decrypt_signing_key(settings.did_signing_key_encrypted, settings.did_signing_key_passphrase)

        if dataset is None:
            dataset = Dataset(
                dataverse_pid=payload.datasetPid,
                did="",
                pid_url="",
            )
            db.add(dataset)
            await db.flush()

            dataset.did = build_did(settings.pid_base_url, dataset.id)
            dataset.pid_url = f"{settings.pid_base_url.rstrip('/')}/resolve/{dataset.id}"

            genesis = create_genesis_log_entry(
                did=dataset.did,
                dataverse_pid=dataset.dataverse_pid,
                dataverse_url=settings.dataverse_url,
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
                dataverse_pid=dataset.dataverse_pid,
                dataverse_url=settings.dataverse_url,
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

        did_log_url = f"{settings.pid_base_url.rstrip('/')}/datasets/{dataset.id}/did.jsonl"
        update_dataset_metadata_with_did(
            dataverse_url=settings.dataverse_url,
            api_token=settings.dataverse_api_token,
            dataset_pid=dataset.dataverse_pid,
            did=dataset.did,
            did_log_url=did_log_url,
            pid_url=dataset.pid_url,
        )
        await db.commit()
        await release_workflow_lock(payload.returnURL, success=True)
        return {"status": "ok", "dataset_uuid": str(dataset.id), "did": dataset.did}
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        reason = str(exc)
        try:
            await release_workflow_lock(payload.returnURL, success=False, reason=reason)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Prepublish failed: {reason}") from exc
