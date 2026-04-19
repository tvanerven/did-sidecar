import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID
from urllib.parse import urlparse


def build_did(pid_base_url: str, dataset_uuid: UUID) -> str:
    host = urlparse(pid_base_url).netloc
    return f"did:webvh:{host}:datasets:{dataset_uuid}"


def _make_entry_hash(payload: dict) -> str:
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(packed).hexdigest()[:16]


def _proof_for_entry(payload: dict, signing_key: str) -> dict:
    # Placeholder detached proof for local development if trustDidWeb signatures are unavailable.
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hashlib.sha256(signing_key.encode("utf-8") + packed).hexdigest()
    return {
        "type": "Ed25519Signature2020",
        "created": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "proofPurpose": "authentication",
        "verificationMethod": "did:key:z6MkExample#key-01",
        "jws": signature,
    }


def _base_parameters(signing_key: str) -> dict:
    key_hash = hashlib.sha256(signing_key.encode("utf-8")).hexdigest()
    next_key_hash = hashlib.sha256((key_hash + "-next").encode("utf-8")).hexdigest()
    return {
        "method": "did:webvh:0.5",
        "scid": key_hash[:32],
        "updateKeys": ["did:key:z6MkExample#key-01"],
        "nextKeyHash": next_key_hash,
        "portable": False,
    }


def create_genesis_log_entry(
    *,
    did: str,
    dataverse_pid: str,
    dataverse_url: str,
    signing_key: str,
) -> dict:
    state = {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": did,
        "service": [
            {
                "id": "#dataset",
                "type": "DataverseDataset",
                "serviceEndpoint": f"{dataverse_url.rstrip('/')}/dataset.xhtml?persistentId={dataverse_pid}",
            }
        ],
    }
    body = {
        "versionTime": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "parameters": _base_parameters(signing_key),
        "state": state,
    }
    body["versionId"] = f"1-{_make_entry_hash(body)}"
    body["proof"] = _proof_for_entry(body, signing_key)
    return body


def create_update_log_entry(
    *,
    did: str,
    dataverse_pid: str,
    dataverse_url: str,
    version_number: int,
    dataverse_version: str,
    signing_key: str,
) -> dict:
    state = {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": did,
        "service": [
            {
                "id": "#dataset",
                "type": "DataverseDataset",
                "serviceEndpoint": f"{dataverse_url.rstrip('/')}/dataset.xhtml?persistentId={dataverse_pid}",
            },
            {
                "id": f"#v{version_number}",
                "type": "DataverseDataset",
                "serviceEndpoint": (
                    f"{dataverse_url.rstrip('/')}/dataset.xhtml"
                    f"?persistentId={dataverse_pid}&version={dataverse_version}"
                ),
            },
        ],
    }
    body = {
        "versionTime": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "parameters": _base_parameters(signing_key),
        "state": state,
    }
    body["versionId"] = f"{version_number}-{_make_entry_hash(body)}"
    body["proof"] = _proof_for_entry(body, signing_key)
    return body
