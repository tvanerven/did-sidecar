# DID-Based PID Sidecar for Dataverse

A FastAPI sidecar service that intercepts Dataverse PrePublish workflow events to mint decentralized identifiers (DIDs) using the `did:webvh` method, manages an append-only DID log in PostgreSQL, and exposes a persistent identifier (PID) resolver endpoint.

## Overview

The sidecar orchestrates the following workflow:

1. **PrePublish Hook** — Dataverse calls `POST /prepublish` with dataset metadata
2. **DID Minting** — Generate `did:webvh:` identifier and log genesis entry (or append for new versions)
3. **Database Storage** — Persist DID and log entries in PostgreSQL
4. **Metadata Injection** — Update Dataverse with DID via custom metadata block
5. **Lock Release** — Signal Dataverse workflow completion (success or failure)
6. **PID Resolution** — Public `GET /resolve/{uuid}` endpoint redirects to dataset landing page
7. **DID Log Serving** — Public `GET /datasets/{uuid}/did.jsonl` for Universal Resolver verification

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Dataverse Instance                                          │
│  (with custom metadata block + PrePublish External Hook)   │
└────────────────────┬────────────────────────────────────────┘
                     │ POST /prepublish
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ DID Sidecar (FastAPI on :8000)                              │
│                                                              │
│  ├─ Config: pydantic-settings from .env                    │
│  ├─ Database: SQLAlchemy async + asyncpg                   │
│  │  ├─ datasets          (DID mappings)                    │
│  │  ├─ did_log_entries   (append-only log)                │
│  │  └─ did_service_endpoints (denormalised redirects)     │
│  │                                                          │
│  ├─ Services:                                              │
│  │  ├─ did_minting       (genesis & update entries)       │
│  │  ├─ key_management    (Fernet-encrypted signing key)   │
│  │  └─ dataverse         (PyDataverse + workflow lock)    │
│  │                                                          │
│  └─ Endpoints:                                             │
│     ├─ POST /prepublish   (Dataverse webhook)             │
│     ├─ GET /resolve/{uuid}  (PID redirect → 302)         │
│     └─ GET /datasets/{uuid}/did.jsonl (DID log)          │
└────────────┬─────────────────────────┬──────────────────────┘
             │                         │
             ▼                         ▼
    PostgreSQL Database        PyDataverse API
    (DID Storage)              (Metadata Update)
             │
             └─────────────────────┬──────────────────────┐
                                   │                      │
                                   ▼                      ▼
                    Universal Resolver        External PID Resolvers
                    (webvh driver)            (DOI, Handle, etc.)
```

## Project Structure

```
did-sidecar/
├── app/
│   ├── main.py                          # FastAPI app + startup
│   ├── config.py                        # Pydantic settings
│   ├── database.py                      # SQLAlchemy setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── dataset.py                   # ORM: datasets table
│   │   ├── did_log.py                   # ORM: did_log_entries table
│   │   └── service_endpoint.py          # ORM: did_service_endpoints table
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── prepublish.py                # Request body validation
│   │   └── did.py                       # DID document schemas
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── prepublish.py                # POST /prepublish handler
│   │   ├── resolve.py                   # GET /resolve/{uuid}
│   │   ├── did_log.py                   # GET /datasets/{uuid}/did.jsonl
│   │   └── admin.py                     # POST /admin/rotate-key
│   └── services/
│       ├── __init__.py
│       ├── did_minting.py               # DID + log entry construction
│       ├── did_update.py                # Update logic
│       ├── dataverse.py                 # PyDataverse + lock release
│       └── key_management.py            # Fernet encryption
├── docker-compose.yml                   # Development stack
├── Dockerfile                           # Container image
├── requirements.txt                     # Python dependencies
├── .env.example                         # Configuration template
├── did-sidecar-spec.md                  # Full specification
└── README.md                            # This file
```

## Setup & Configuration

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ (for local development)
- PostgreSQL 16+ (runs via Docker Compose)

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `DATAVERSE_URL` | Dataverse instance URL (e.g., `https://dataverse.your-institution.org`) |
| `DATAVERSE_API_TOKEN` | API token for PyDataverse metadata updates |
| `PID_BASE_URL` | PID resolver base URL (e.g., `https://pid.your-institution.org`) |
| `DATABASE_URL` | PostgreSQL connection string (asyncpg driver) |
| `DID_SIGNING_KEY_ENCRYPTED` | Fernet-encrypted Ed25519 private key |
| `DID_SIGNING_KEY_PASSPHRASE` | Passphrase for decrypting the signing key |
| `DATAVERSE_WORKFLOW_TOKEN` | Optional token for verifying inbound requests (manual/test calls only — Dataverse `http/sr` does not send auth headers; use IP whitelisting for production) |
| `ADMIN_TOKEN` | Optional token for admin endpoints (key rotation) |

### Signing Key Setup

Generate and encrypt a signing key:

```python
from app.services.key_management import encrypt_signing_key, generate_raw_signing_key

raw_key = generate_raw_signing_key()
passphrase = "your-secure-passphrase"
encrypted = encrypt_signing_key(raw_key, passphrase)

print(f"DID_SIGNING_KEY_ENCRYPTED={encrypted}")
print(f"DID_SIGNING_KEY_PASSPHRASE={passphrase}")
```

Add these values to `.env`.

## Running

### Docker Compose (Recommended)

```bash
# Build images
docker compose build

# Start all services
docker compose up
```

Services:
- **sidecar**: FastAPI on `http://localhost:8000`
- **db**: PostgreSQL on `localhost:5432` (username: `did_sidecar`, password: `did_sidecar`)
- **universal-resolver**: http://localhost:8080 (DID resolution, optional)
- **driver-webvh**: http://localhost:8081 (webvh driver, optional)

### Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start sidecar (requires .env configured with local DB)
uvicorn app.main:app --reload
```

## API Endpoints

### 1. POST /prepublish

**Dataverse PrePublish Webhook** — Called during dataset publication workflow.

**Request:**
```json
{
  "invocationId": "abc-123-def",
  "datasetId": "e1a2b3c4-d5e6-f7g8-h9i0-j1k2l3m4n5o6",
  "datasetPid": "doi:10.5281/zenodo.1234567",
  "datasetVersion": "1.0"
}
```

> **Note:** The body fields are determined by the workflow step `body` template configured in Dataverse (see Workflow Configuration). Dataverse's `http/sr` step does not support custom request headers, so no authentication header is sent. Secure the endpoint via IP whitelisting in Dataverse instead.

**Response (200 OK):**
```json
{
  "status": "ok",
  "dataset_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "did": "did:webvh:pid.your-institution.org:datasets:550e8400-e29b-41d4-a716-446655440000"
}
```

**Logic:**
- Validates workflow token
- Fetches full dataset metadata via PyDataverse
- **New dataset (first major publish)**: Mints DID, creates genesis log entry
- **Existing dataset (subsequent major publish)**: Appends new log entry, adds `#vN` service endpoint
- **Minor publish (1.1, 1.2, …)**: No DID log update
- Updates Dataverse custom metadata block with DID fields
- POSTs `OK` (text/plain) to `{DATAVERSE_URL}/api/workflows/{invocationId}` to release the lock, or a non-OK body on failure to trigger rollback

### 2. GET /resolve/{uuid}

**PID Redirect** — Public endpoint acting as a persistent identifier resolver.

**Request:**
```
GET /resolve/550e8400-e29b-41d4-a716-446655440000
```

**Response (302 Found):**
```
Location: https://dataverse.your-institution.org/dataset.xhtml?persistentId=doi:10.5281/zenodo.1234567
```

This URL is the PID and should be shared with users and cited in publications.

### 3. GET /datasets/{uuid}/did.jsonl

**DID Log Endpoint** — Serves newline-delimited JSON for Universal Resolver.

**Request:**
```
GET /datasets/550e8400-e29b-41d4-a716-446655440000/did.jsonl
```

**Response (200 OK, Content-Type: application/jsonl):**
```jsonl
{"versionId":"1-abc123...","versionTime":"2025-01-01T00:00:00Z","parameters":{...},"state":{...},"proof":{...}}
{"versionId":"2-def456...","versionTime":"2025-02-01T00:00:00Z","parameters":{...},"state":{...},"proof":{...}}
```

**URL structure must match the DID:**
```
DID:  did:webvh:pid.your-institution.org:datasets:550e8400-e29b-41d4-a716-446655440000
URL:  https://pid.your-institution.org/datasets/550e8400-e29b-41d4-a716-446655440000/did.jsonl
```

### 4. POST /admin/rotate-key

**Admin Endpoint** — Generate a new signing key (gated by `ADMIN_TOKEN`).

**Request:**
```bash
curl -X POST http://localhost:8000/admin/rotate-key \
  -H "X-Admin-Token: <ADMIN_TOKEN>"
```

**Response (200 OK):**
```json
{
  "did_signing_key_encrypted": "gAAAAABlJy8K..."
}
```

**Usage:** Copy the returned encrypted key back to `.env` as `DID_SIGNING_KEY_ENCRYPTED` and restart the sidecar.

### 5. GET /health

**Health Check** — Simple status endpoint.

**Response (200 OK):**
```json
{"status": "ok"}
```

## Database Schema

### datasets
```sql
CREATE TABLE datasets (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataverse_pid     TEXT NOT NULL UNIQUE,   -- e.g., doi:10.xxxx/... or hdl:...
    did               TEXT NOT NULL UNIQUE,   -- did:webvh:pid.your-inst.org:datasets:{id}
    pid_url           TEXT NOT NULL,          -- https://pid.your-inst.org/resolve/{id}
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### did_log_entries
```sql
CREATE TABLE did_log_entries (
    id                BIGSERIAL PRIMARY KEY,
    dataset_id        UUID NOT NULL REFERENCES datasets(id),
    version_number    INTEGER NOT NULL,           -- monotonic: 1, 2, 3, ...
    dataverse_version TEXT,                       -- e.g., "1.0", "2.0"
    log_entry         JSONB NOT NULL,             -- Complete DID log entry
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version_number)
);
```

### did_service_endpoints
```sql
CREATE TABLE did_service_endpoints (
    id                BIGSERIAL PRIMARY KEY,
    dataset_id        UUID NOT NULL REFERENCES datasets(id),
    log_entry_id      BIGINT NOT NULL REFERENCES did_log_entries(id),
    endpoint_id       TEXT NOT NULL,              -- e.g., "#dataset", "#v2"
    endpoint_type     TEXT NOT NULL,              -- e.g., "DataverseDataset"
    endpoint_url      TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Key Design Patterns:**
- `did_log_entries` is **append-only** — never update or delete rows
- `did_service_endpoints` is **denormalised** for fast O(1) redirect lookups
- `did.jsonl` is reconstructed on-the-fly from `log_entry` JSONB values, ordered by version

## Dataverse Integration

### Custom Metadata Block

Install on Dataverse before deploying the sidecar. Example TSV format:

```tsv
metadataBlock	name	dataverseAlias	displayName
		pid_did		DID Persistent Identifier

datasetField	name	title	description	fieldType	displayOrder	required
	didIdentifier	DID	Decentralised Identifier (did:webvh)	TEXT	0	FALSE
	didLogUrl	DID Log URL	URL to the did.jsonl log file	URL	1	FALSE
	pidUrl	PID URL	Persistent identifier URL	URL	2	FALSE
```

### Workflow Configuration

Use Dataverse's workflow API to deploy a PrePublish workflow that invokes the sidecar. The workflow uses two steps:

1. **log** — Log the workflow invocation
2. **http/sr** — Send HTTP request to sidecar and wait for response

**Workflow JSON:**

Save this as `workflow-prepublish-did.json`:

```json
{
  "steps": [
    {
      "provider": ":internal",
      "stepType": "log",
      "parameters": {
        "logMessage": "Starting DID minting workflow for ${dataset.displayName}"
      }
    },
    {
      "provider": ":internal",
      "stepType": "http/sr",
      "parameters": {
        "url": "http://sidecar:8000/prepublish",
        "method": "POST",
        "contentType": "application/json",
        "body": "{\"invocationId\":\"${invocationId}\",\"datasetId\":\"${dataset.id}\",\"datasetPid\":\"${dataset.identifier}\",\"datasetVersion\":\"${majorVersion}.${minorVersion}\"}",
        "expectedResponse": ".*ok.*",
        "rollbackUrl": "http://sidecar:8000/admin/rollback",
        "rollbackMethod": "POST"
      }
    }
  ]
}
```

**Workflow Variables Available:**
- `${invocationId}` — Unique workflow invocation identifier
- `${dataset.id}` — Dataset UUID
- `${dataset.identifier}` — Dataset PID (e.g., `doi:10.5281/...`)
- `${dataset.displayName}` — Dataset title
- `${majorVersion}` — Major version number
- `${minorVersion}` — Minor version number

**Deploy via Dataverse Native API:**

```bash
curl -X POST "https://dataverse.your-institution.org/api/admin/workflows/default/PrePublishDataset" \
  -H "X-Dataverse-key: ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d @workflow-prepublish-did.json
```

**Verify Deployment:**

```bash
curl -X GET "https://dataverse.your-institution.org/api/admin/workflows/default/PrePublishDataset" \
  -H "X-Dataverse-key: ${ADMIN_TOKEN}"
```

**IP Whitelist Configuration:**

Ensure the sidecar's IP address is whitelisted for sending workflow completion callbacks:

```bash
curl -X PUT "https://dataverse.your-institution.org/api/admin/workflows/ip-whitelist" \
  -H "X-Dataverse-key: ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '["127.0.0.1", "::1", "10.0.0.5"]'
```

(Replace `10.0.0.5` with the sidecar's actual IP/hostname.)

### Workflow Behavior

When a dataset is published:

1. **Log step** writes invocation details to Dataverse log
2. **http/sr step** POSTs workflow data to sidecar's `/prepublish` endpoint
3. Sidecar mints DID, updates metadata, and POSTs response back to Dataverse
4. Dataverse validates response matches regex `.*ok.*`
5. If match succeeds: workflow completes, dataset is published
6. If match fails or timeout: workflow rolls back, dataset publication is cancelled

**Error Handling:**

If the sidecar is unreachable or returns a non-matching response, the workflow fails and the dataset publication is rolled back. On internal errors the sidecar POSTs a `FAILURE: <reason>` body (text/plain) to the callback URL, triggering rollback. The invocationId persists in Dataverse logs for debugging.

## DID Format

### String Representation

```
did:webvh:pid.your-institution.org:datasets:{dataset-uuid}
```

Example:
```
did:webvh:pid.example.org:datasets:550e8400-e29b-41d4-a716-446655440000
```

### Log Entry Structure (Genesis)

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

### Update Entry (New Dataverse Version)

Appends a new entry with:
- Incremented `versionId`
- Updated `versionTime`
- Additional `#vN` service endpoint pointing to versioned dataset

## Implementation Notes

### Placeholder Signing

Currently, the `did_minting.py` uses a functional placeholder for proof generation. For production, integrate the canonical **trustDidWeb** library:
- Handles SCID derivation
- Computes proper hash chains
- Generates Ed25519 signatures
- Follows the did:webvh spec precisely

Swap the `_proof_for_entry()` and parameter construction functions once the library is available.

### Error Handling

All PrePublish failures automatically release the workflow lock with `{"status": "failure", "reason": "..."}` to prevent indefinite timeouts. Never silently fail—always signal Dataverse.

### Versioning Policy

| Dataverse Event | DID Action |
|---|---|
| First major publish (1.0) | Mint DID, create genesis entry |
| Subsequent major publish (2.0, 3.0, …) | Append log entry with `#vN` endpoint |
| Minor publish (1.1, 1.2, …) | No DID log update |
| Deaccession | Append tombstone entry with updated `#dataset` endpoint |

Version detection: parse `datasetVersion` for pattern `N.0` (major) vs `N.M` (minor).

### Key Rotation

Use `POST /admin/rotate-key` to generate a new encrypted key. Copy the result back to `.env` and restart the sidecar. Existing log entries remain valid; new entries use the new key.

## Testing

### Health Check

```bash
curl http://localhost:8000/health
```

### Resolve Endpoint (requires data)

```bash
curl -L http://localhost:8000/resolve/{uuid}
```

### DID Log Endpoint (requires data)

```bash
curl http://localhost:8000/datasets/{uuid}/did.jsonl
```

### Prepublish Simulation

```bash
curl -X POST http://localhost:8000/prepublish \
  -H "Content-Type: application/json" \
  -d '{
    "invocationId": "test-123",
    "datasetId": "test-uuid",
    "datasetPid": "doi:10.5281/test",
    "datasetVersion": "1.0"
  }'
```

## Troubleshooting

### Database Connection Refused

Ensure `DATABASE_URL` in `.env` uses the correct hostname:
- **Docker Compose:** `postgresql+asyncpg://did_sidecar:did_sidecar@db:5432/did_sidecar`
- **Local dev with remote DB:** `postgresql+asyncpg://user:pass@host:5432/db`

### Workflow Token Validation Fails

Check that the `X-Dataverse-Workflow-Token` header or `Authorization: Bearer <token>` matches `DATAVERSE_WORKFLOW_TOKEN` in `.env`.

### PyDataverse Errors

Verify:
- `DATAVERSE_URL` is reachable and correct
- `DATAVERSE_API_TOKEN` has sufficient permissions (metadata edit, dataset view)
- Dataset PID format matches Dataverse's configuration (DOI, Handle, etc.)

### Missing Custom Metadata Block

The sidecar will attempt to update the metadata block on every PrePublish. If the block is not installed:
1. Install the block on Dataverse (see Dataverse Integration section)
2. Restart the sidecar or retry the PrePublish

## Production Deployment

### Recommendations

1. **Signing Key Backup:** Export the encrypted private key to cold storage before going live
2. **SSL/TLS:** Use HTTPS for all external endpoints (PID resolver, DID log)
3. **Network:** Run sidecar and database on isolated network; allow Dataverse to reach only the public endpoints
4. **Monitoring:** Log all PrePublish requests and lock releases; monitor database growth
5. **Alembic Migrations:** Set up Alembic for schema versioning and zero-downtime deployments
6. **Database Backups:** Regular PostgreSQL backups of `did_log_entries` (append-only, immutable)

## License & Attribution

Implementation based on the [did:webvh specification](https://w3c-ccg.github.io/did-method-webvh/).

---
