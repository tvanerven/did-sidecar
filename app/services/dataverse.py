import logging

import httpx

logger = logging.getLogger(__name__)


def _get_native_api(dataverse_url: str, api_token: str):
    from pyDataverse.api import NativeApi

    return NativeApi(dataverse_url, api_token)


def fetch_dataset_metadata(dataverse_url: str, api_token: str, dataset_pid: str) -> dict:
    api = _get_native_api(dataverse_url, api_token)
    response = api.get_dataset(dataset_pid, is_pid=True)
    return response.json()


def update_dataset_metadata_with_did(
    *,
    dataverse_url: str,
    api_token: str,
    dataset_pid: str,
    did: str,
    did_log_url: str,
    pid_url: str,
) -> None:
    api = _get_native_api(dataverse_url, api_token)
    payload = {
        "metadataBlocks": {
            "pid_did": {
                "fields": [
                    {"typeName": "didIdentifier", "value": did},
                    {"typeName": "didLogUrl", "value": did_log_url},
                    {"typeName": "pidUrl", "value": pid_url},
                ]
            }
        }
    }
    api.edit_dataset_metadata(dataset_pid, payload, is_pid=True)


async def release_workflow_lock(return_url: str, success: bool, reason: str | None = None) -> None:
    # http/sr requires text/plain; body must start with "OK" for success, anything else triggers rollback
    body = "OK" if success else f"FAILURE: {reason}" if reason else "FAILURE"

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            return_url,
            content=body.encode(),
            headers={"Content-Type": "text/plain"},
        )
        response.raise_for_status()
