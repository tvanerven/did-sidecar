# DID-Based PID Sidecar — Implementation Specification

## Overview

A FastAPI sidecar that intercepts Dataverse PrePublish workflow events, mints a `did:webvh` DID for each dataset, stores the DID log in PostgreSQL, injects the DID as custom metadata back into Dataverse, and exposes a redirect endpoint that acts as a DOI-like PID resolver.

The Universal Resolver (self-hosted, webvh driver) is available for external verification but is **not** in the user-facing resolution path.

---

## Architecture

```
Dataverse (PrePublish Hook)
        │
        ▼
POST /prepublish
        │
        ├─ 1. PyDataverse: fetch full dataset metadata
        ├─ 2. Mint DID → append did_log_entries in Postgres
        ├─ 3. PyDataverse: PATCH dataset with DID custom metadata
        ├─ 4. httpx: POST lock release → Dataverse proceeds to publish
        └─ 5. Return 200

GET /datasets/{uuid}/did.jsonl       ← Universal Resolver fetches this
GET /resolve/{uuid}                  ← PID redirect → Dataverse landing page
```

### Component Summary

| Component | Role |
|---|---|
| FastAPI | Orchestration, DID serving, PID redirect |
| PostgreSQL | Source of truth for datasets, DID log, service endpoints |
| PyDataverse | Dataset metadata read + custom metadata write |
| httpx | Dataverse workflow lock release |
| did-webvh lib | DID log construction and signing |
| Universal Resolver + webvh driver | External DID resolution / verification |
| `.env` | Signing key (encrypted), API tokens |

---

## Repository Structure

```
did-sidecar/
├── app/
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Settings via pydantic-settings
│   ├── database.py              # SQLAlchemy engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── dataset.py           # ORM: datasets table
│   │   ├── did_log.py           # ORM: did_log_entries table
│   │   └── service_endpoint.py  # ORM: did_service_endpoints table
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── prepublish.py        # Pydantic: incoming Dataverse payload
│   │   └── did.py               # Pydantic: DID document structures
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── prepublish.py        # POST /prepublish
│   │   ├── resolve.py           # GET /resolve/{uuid}
│   │   └── did_log.py           # GET /datasets/{uuid}/did.jsonl
│   ├── services/
│   │   ├── __init__.py
│   │   ├── did_minting.py       # DID creation + log entry construction
│   │   ├── did_update.py        # Append new log entry on re-publish
│   │   ├── dataverse.py         # PyDataverse + lock release wrappers
│   │   └── key_management.py    # Signing key load/encrypt/rotate
│   └── migrations/
│       └── (Alembic files)
├── tests/
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Environment Variables (`.env`)

```dotenv
# Dataverse
DATAVERSE_URL=https://dataverse.your-institution.org
DATAVERSE_API_TOKEN=...

# PID base URL (used to construct PID URLs and DID strings)
PID_BASE_URL=https://pid.your-institution.org

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/did_sidecar

# DID signing key (Fernet-encrypted private key)
DID_SIGNING_KEY_ENCRYPTED=...
DID_SIGNING_KEY_PASSPHRASE=...

# Dataverse workflow token (used to verify inbound PrePublish requests)
DATAVERSE_WORKFLOW_TOKEN=...
```

**Key storage note:** The private signing key should be stored Fernet-encrypted. The `.env` holds the passphrase, not the raw key. See `services/key_management.py`. Raw plaintext private keys in env vars are not acceptable for production.

---

## Database Schema

```sql
-- Core dataset / DID mapping
CREATE TABLE datasets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataverse_pid     TEXT NOT NULL UNIQUE,   -- e.g. doi:10.xxxx/... or hdl:...
    did               TEXT NOT NULL UNIQUE,   -- did:webvh:pid.your-inst.org:datasets:{id}
    pid_url           TEXT NOT NULL,          -- https://pid.your-inst.org/resolve/{id}
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only DID log (mirrors did.jsonl; never update existing rows)
CREATE TABLE did_log_entries (
    id                BIGSERIAL PRIMARY KEY,
    dataset_id        UUID NOT NULL REFERENCES datasets(id),
    version_number    INTEGER NOT NULL,           -- monotonic: 1, 2, 3...
    dataverse_version TEXT,                       -- e.g. "2.0" (major only)
    log_entry         JSONB NOT NULL,             -- raw did.jsonl line
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version_number)
);

-- Denormalised service endpoints for fast redirect lookups
CREATE TABLE did_service_endpoints (
    id                BIGSERIAL PRIMARY KEY,
    dataset_id        UUID NOT NULL REFERENCES datasets(id),
    log_entry_id      BIGINT NOT NULL REFERENCES did_log_entries(id),
    endpoint_id       TEXT NOT NULL,              -- e.g. "#dataset", "#v2"
    endpoint_type     TEXT NOT NULL,              -- e.g. "DataverseDataset"
    endpoint_url      TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX idx_datasets_dataverse_pid ON datasets(dataverse_pid);
CREATE INDEX idx_did_log_dataset_version ON did_log_entries(dataset_id, version_number);
CREATE INDEX idx_service_endpoints_dataset ON did_service_endpoints(dataset_id);
```

### Design Notes

- `did_log_entries` is the single source of truth. The `did.jsonl` endpoint reconstructs the file by ordering rows by `version_number` and serialising each `log_entry` JSONB value as a newline-delimited JSON line. No filesystem state.
- **Never update or delete rows in `did_log_entries`.** Append only.
- `did_service_endpoints` is denormalised for fast `O(1)` redirect lookups. On every log append, also insert the service endpoints from that entry into this table.

---

## API Endpoints

### `POST /prepublish`

Receives the Dataverse External Workflow invocation. Orchestrates the full mint-or-update flow.

**Request body (from Dataverse):**
```json
{
  "invocationId": "abc-123",
  "datasetId": "...",
  "datasetPid": "doi:10.xxxx/ABC123",
  "datasetVersion": "2.0",
  "returnURL": "https://dataverse.your-inst.org/api/workflows/invocations/abc-123"
}
```

**Logic:**
1. Verify `DATAVERSE_WORKFLOW_TOKEN` header.
2. Check if `datasetPid` already exists in `datasets` table.
   - **New dataset:** mint DID (genesis log entry), insert into `datasets` and `did_log_entries`.
   - **Existing dataset:** append new log entry to `did_log_entries` for the new version.
3. Upsert service endpoints in `did_service_endpoints`.
4. PyDataverse: `edit_dataset_metadata` to inject DID fields.
5. `httpx`: POST to `returnURL` to release the Dataverse workflow lock.
6. On any failure: POST lock release with `{"status": "failure", "reason": "..."}` — do not leave lock open.

**Response:** `200 OK` (Dataverse requires a synchronous response to acknowledge receipt, but the lock release is a separate outbound call.)

---

### `GET /resolve/{uuid}`

PID redirect endpoint. Acts like a DOI resolver.

**Logic:**
1. Look up `uuid` in `datasets`.
2. Fetch the latest `endpoint_url` from `did_service_endpoints` for that dataset.
3. Return `HTTP 302` → Dataverse dataset landing page URL.
4. Return `HTTP 404` if UUID not found.

**This is the URL used as the PID.** Format: `https://pid.your-institution.org/resolve/{uuid}`

---

### `GET /datasets/{uuid}/did.jsonl`

Serves the `did.jsonl` log for the Universal Resolver's webvh driver.

**Logic:**
1. Look up `uuid` in `datasets`.
2. Fetch all `did_log_entries` for that dataset ordered by `version_number ASC`.
3. Serialise each `log_entry` JSONB value as one line of newline-delimited JSON.
4. Return with `Content-Type: application/jsonl`.

**URL structure must match the DID string exactly:**
```
DID:  did:webvh:pid.your-institution.org:datasets:abc123
URL:  https://pid.your-institution.org/datasets/abc123/did.jsonl
```

---

## DID Minting Logic

### DID String Format

```
did:webvh:{PID_BASE_HOST}:datasets:{dataset_uuid}
```

Example: `did:webvh:pid.your-institution.org:datasets:550e8400-e29b-41d4-a716-446655440000`

### Genesis Log Entry Structure

```json
{
  "versionId": "1-{hash}",
  "versionTime": "2025-01-01T00:00:00Z",
  "parameters": {
    "method": "did:webvh:0.5",
    "scid": "...",
    "updateKeys": ["did:key:...#key-01"],
    "nextKeyHash": "...",
    "portable": false
  },
  "state": {
    "@context": ["https://www.w3.org/ns/did/v1"],
    "id": "did:webvh:pid.your-institution.org:datasets:{uuid}",
    "service": [
      {
        "id": "#dataset",
        "type": "DataverseDataset",
        "serviceEndpoint": "https://dataverse.your-institution.org/dataset.xhtml?persistentId=doi:..."
      }
    ]
  },
  "proof": { ... }
}
```

### Update Log Entry (new Dataverse version)

Append a new entry with:
- Incremented `versionId`
- Updated `versionTime`
- Rotated `updateKeys` / `nextKeyHash` if key rotation is scheduled
- Updated or additional `service` entries, e.g.:
```json
{
  "id": "#v2",
  "type": "DataverseDataset",
  "serviceEndpoint": "https://dataverse.your-institution.org/dataset.xhtml?persistentId=...&version=2.0"
}
```

**Use the `did-webvh` reference implementation** (Python: `trustDidWeb`) for log entry construction and signing. Do not hand-roll the SCID derivation, hash chaining, or proof format.

---

## Dataverse Custom Metadata Block

Install a custom metadata block on the Dataverse instance before deploying the sidecar. Example TSV schema:

```tsv
metadataBlock	name	dataverseAlias	displayName
		pid_did		DID Persistent Identifier

datasetField	name	title	description	fieldType	displayOrder	required
	didIdentifier	DID	Decentralised Identifier (did:webvh)	TEXT	0	FALSE
	didLogUrl	DID Log URL	URL to the did.jsonl log file	URL	1	FALSE
	pidUrl	PID URL	Persistent identifier URL	URL	2	FALSE
```

Install via Dataverse admin API:
```bash
curl -X POST -H "X-Dataverse-key: $TOKEN" \
  "https://dataverse.your-inst.org/api/admin/datasetfield/load" \
  -F "body=@pid_did_block.tsv"
```

PyDataverse metadata injection payload:
```python
{
    "metadataBlocks": {
        "pid_did": {
            "fields": [
                {"typeName": "didIdentifier", "value": did},
                {"typeName": "didLogUrl",     "value": did_log_url},
                {"typeName": "pidUrl",        "value": pid_url},
            ]
        }
    }
}
```

---

## Versioning Policy

| Dataverse event | DID log action |
|---|---|
| First major publish (1.0) | Genesis entry — mint DID |
| Subsequent major publish (2.0, 3.0, …) | Append new log entry, add `#vN` service endpoint |
| Minor publish (1.1, 1.2, …) | No DID log update (metadata-only changes) |
| Dataset deaccession | Append tombstone entry (update `#dataset` service endpoint to a tombstone page) |

The sidecar determines major vs minor by parsing the `datasetVersion` field in the invocation payload. Only versions matching `N.0` trigger a log append after genesis.

---

## Key Management

- Generate an Ed25519 key pair at sidecar initialisation (or separately via a setup script).
- Encrypt the private key with Fernet using the passphrase from `.env`.
- Store the encrypted blob as `DID_SIGNING_KEY_ENCRYPTED` in `.env`.
- `services/key_management.py` loads and decrypts the key on startup, caches it in memory.
- **Backup:** export an encrypted copy of the private key to cold storage before going to production. Key loss = inability to issue authoritative DID log updates.
- **Rotation:** did:webvh supports key rotation via `nextKeyHash`. Implement rotation as a separate maintenance endpoint (`POST /admin/rotate-key`) gated behind an admin token, for future use.

---

## Docker Compose (Development)

```yaml
services:
  sidecar:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      - db

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: did_sidecar
      POSTGRES_PASSWORD: did_sidecar
      POSTGRES_DB: did_sidecar
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  universal-resolver:
    image: universalresolver/uni-resolver-web:latest
    ports:
      - "8080:8080"
    depends_on:
      - driver-webvh

  driver-webvh:
    image: universalresolver/driver-did-webvh:latest
    ports:
      - "8081:8081"

volumes:
  pgdata:
```

**Networking note:** The webvh driver fetches `did.jsonl` files from your sidecar. Ensure the driver container can reach the sidecar's hostname. In Docker Compose, use the service name `sidecar` as the hostname rather than `pid.your-institution.org` in development.

---

## Python Dependencies (`requirements.txt`)

```
fastapi>=0.111
uvicorn[standard]
sqlalchemy[asyncio]>=2.0
asyncpg
alembic
pydantic-settings
pyDataverse
httpx
cryptography           # Fernet key encryption
trustDidWeb            # did:webvh log construction (verify package name on PyPI)
```

---

## Error Handling Policy

All failures inside the `/prepublish` handler must result in a lock release back to Dataverse — either success or explicit failure. Never let the lock time out silently.

```python
try:
    did = await mint_or_update_did(dataset_pid, dataset_version)
    await inject_metadata(dataset_pid, did)
    await release_lock(return_url, status="success")
except Exception as e:
    logger.error(f"PrePublish failed: {e}")
    await release_lock(return_url, status="failure", reason=str(e))
    raise
```

---

## Open Items / Decisions Needed

| Item | Status |
|---|---|
| Backup location for encrypted signing key | To be decided |
| Minor version handling — confirm no DID update | Assumed; verify with stakeholders |
| Tombstone page URL for deaccessioned datasets | To be designed |
| Production domain and TLS for `pid.your-institution.org` | Infrastructure prerequisite |
| Universal Resolver driver config for internal networking | Needs Docker networking plan |
