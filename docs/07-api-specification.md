# Huible REST API Specification v1.0

## 1. Overview

This document defines the complete REST interface contract for the Huible memory engine. All clients (web UI, CLI, integrations) communicate through this API.

### Base URL

```
https://{deployment-host}/api/v1
```

### Versioning

The API is versioned via URL path (`/api/v1`). Backwards-compatible additions may be made within a version. Breaking changes require a new version path (`/api/v2`).

### Content Type

All request and response bodies use `application/json` unless otherwise noted.

### Error Envelope

Every error response follows a consistent envelope:

```json
{
  "error": {
    "code": "MEMORY_NOT_FOUND",
    "status": 404,
    "message": "No memory found with id 'abc-123'",
    "details": {}
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `error.code` | `string` | Machine-readable error code (see Section 6) |
| `error.status` | `integer` | HTTP status code |
| `error.message` | `string` | Human-readable description |
| `error.details` | `object` | Optional key-value context (field-level errors, etc.) |

Successful responses return a top-level `data` envelope:

```json
{
  "data": { ... }
}
```

For list endpoints, responses include pagination metadata:

```json
{
  "data": [ ... ],
  "pagination": {
    "total": 142,
    "offset": 0,
    "limit": 25,
    "has_more": true
  }
}
```

### Timestamps

All timestamps are ISO 8601 in UTC: `2025-12-01T18:30:00Z`. Date fields (no time) use `YYYY-MM-DD`.

---

## 2. Authentication

All endpoints require an `Authorization` header:

```
Authorization: Bearer {api_key}
```

**API keys** are persona-scoped. A single key grants access to one persona's memory graph. Keys are managed out-of-band (admin CLI or database seed) and stored as `TEXT NOT NULL` in a `api_keys` table.

**Unauthenticated requests** return `401`:

```json
{
  "error": {
    "code": "AUTH_REQUIRED",
    "status": 401,
    "message": "Missing or invalid Authorization header"
  }
}
```

**Wrong persona scope** returns `403`:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "status": 403,
    "message": "API key does not grant access to this persona"
  }
}
```

Rate limiting is not enforced in Phase 1. A future `X-RateLimit-*` header contract is reserved.

---

## 3. Endpoints

### 3.1 Memory Ingestion

#### `POST /api/v1/memories` — Store a new memory

Creates a memory node and runs it through the five-gate firewall (INV-15). Processing is synchronous — the response reflects the gate outcome.

**Request**

```json
{
  "content": "Dad always made pancakes on Sunday mornings",
  "content_type": "narrative",
  "tier": "accrued",
  "source_type": "family_upload",
  "memory_date": "1998-06-14",
  "disclosure_scope": "family",
  "metadata": {
    "source_conversation_id": "conv-456"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | `string` | yes | Memory text content |
| `content_type` | `string` | no | One of: `narrative`, `fact`, `sensory`, `relationship`, `preference`. Default: `narrative` |
| `tier` | `string` | no | One of: `canonical`, `derived`, `accrued`, `world`. Default: `accrued` |
| `source_type` | `string` | no | One of: `extraction`, `family_upload`, `canonical_seed`, `inference`. Default: `extraction` |
| `memory_date` | `string(date)` | no | Approximate date of the remembered event |
| `disclosure_scope` | `string` | no | One of: `private`, `family`, `close_friends`, `all_contacts`. Default: `family` |
| `metadata` | `object` | no | Arbitrary key-value metadata |

Embeddings are generated server-side during ingestion. The `valid_from`, `valid_to`, `supersedes`, and `source_ref` fields are set by the system, not the client.

**Response — 201 Accepted**

```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "persona_id": "660e8400-e29b-41d4-a716-446655440001",
    "tier": "accrued",
    "content": "Dad always made pancakes on Sunday mornings",
    "content_type": "narrative",
    "memory_date": "1998-06-14",
    "disclosure_scope": "family",
    "source_type": "family_upload",
    "version": 1,
    "is_active": true,
    "created_at": "2025-12-01T18:30:00Z",
    "metadata": {}
  }
}
```

**Response — 202 Quarantined** (one or more gates returned AMBIGUOUS)

```json
{
  "data": {
    "id": "quarantine-entry-id",
    "quarantine_id": "770e8400-e29b-41d4-a716-446655440002",
    "status": "quarantined",
    "failed_gates": ["novelty", "pertinence"],
    "priority": "medium",
    "message": "Memory candidate quarantined pending adjudication"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `VALIDATION_ERROR` | Missing `content` field or invalid enum values |
| 409 | `DUPLICATE_MEMORY` | Deduplication gate: cosine similarity > 0.92 against existing memory |
| 422 | `SAFETY_REJECTION` | Safety gate: prompt injection or adversarial content detected |

---

#### `PUT /api/v1/memories/{memory_id}` — Update an existing memory

Creates a new version that supersedes the existing active version (INV-16: append-only). The old version is marked `is_active = false` with a `superseded_by` pointer.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `memory_id` | `UUID` | The memory to supersede |

**Request**

```json
{
  "content": "Dad made pancakes every Sunday morning with blueberries from the garden",
  "metadata": {
    "edit_reason": "Added detail about blueberries"
  }
}
```

Only the fields present in the request body are updated. Unspecified fields carry forward from the previous version.

**Response — 200 OK**

```json
{
  "data": {
    "id": "880e8400-e29b-41d4-a716-446655440003",
    "persona_id": "660e8400-e29b-41d4-a716-446655440001",
    "tier": "accrued",
    "content": "Dad made pancakes every Sunday morning with blueberries from the garden",
    "content_type": "narrative",
    "memory_date": "1998-06-14",
    "disclosure_scope": "family",
    "source_type": "extraction",
    "version": 2,
    "is_active": true,
    "supersedes": "550e8400-e29b-41d4-a716-446655440000",
    "created_at": "2025-12-02T10:00:00Z",
    "metadata": {
      "edit_reason": "Added detail about blueberries"
    }
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `VALIDATION_ERROR` | Empty `content` or invalid fields |
| 404 | `MEMORY_NOT_FOUND` | `memory_id` does not exist |
| 409 | `CONFLICT` | Memory already superseded (target is not `is_active`) |
| 422 | `IMMUTABLE_TIER` | Attempting to modify a `canonical` tier memory (INV-CI) |

---

#### `DELETE /api/v1/memories/{memory_id}` — Soft-delete a memory

Marks the memory as `is_active = false`. The record is preserved for audit; the active query set excludes it.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `memory_id` | `UUID` | The memory to soft-delete |

**Response — 200 OK**

```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "deleted",
    "is_active": false,
    "deleted_at": "2025-12-03T14:00:00Z"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `MEMORY_NOT_FOUND` | `memory_id` does not exist |
| 410 | `ALREADY_DELETED` | Memory is already `is_active = false` |

---

### 3.2 Retrieval & Search

#### `POST /api/v1/retrieve` — Semantic retrieval

Performs spreading activation retrieval (Section 3.3 of the engine spec). Returns ranked memories based on multi-vector similarity, graph traversal, motif escalation, and disclosure scoping.

**Request**

```json
{
  "query": "What did Dad like to do on weekends?",
  "disclosure_tier": "family",
  "top_k": 10,
  "exclude_memory_ids": ["550e8400-e29b-41d4-a716-446655440000"],
  "search_mode": "content"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | `string` | yes | Natural language query text. Server generates embeddings. |
| `disclosure_tier` | `string` | no | One of: `private`, `family`, `close_friends`, `all_contacts`. Default: `family` |
| `top_k` | `integer` | no | Maximum results returned. Default: `10`, max: `50` |
| `exclude_memory_ids` | `array[UUID]` | no | Memory IDs to suppress (feedback loop prevention, INV-FL) |
| `search_mode` | `string` | no | Embedding mode: `content` (default), `sensory`, `affect` |

**Response — 200 OK**

```json
{
  "data": {
    "results": [
      {
        "memory": {
          "id": "550e8400-e29b-41d4-a716-446655440000",
          "persona_id": "660e8400-e29b-41d4-a716-446655440001",
          "tier": "canonical",
          "content": "Dad made pancakes every Sunday morning with blueberries from the garden",
          "content_type": "narrative",
          "memory_date": "1998-06-14",
          "disclosure_scope": "family",
          "version": 2,
          "is_active": true
        },
        "activation_score": 0.92
      },
      {
        "memory": {
          "id": "990e8400-e29b-41d4-a716-446655440004",
          "persona_id": "660e8400-e29b-41d4-a716-446655440001",
          "tier": "accrued",
          "content": "He also loved fishing at the lake on Saturday mornings",
          "content_type": "narrative",
          "memory_date": "1995-07-20",
          "disclosure_scope": "family",
          "version": 1,
          "is_active": true
        },
        "activation_score": 0.78
      }
    ],
    "query": "What did Dad like to do on weekends?",
    "disclosure_tier": "family"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `VALIDATION_ERROR` | Missing `query` or invalid `search_mode` |

---

#### `GET /api/v1/memories/{memory_id}` — Fetch a single memory

Returns the current active version of a memory by ID.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `memory_id` | `UUID` | The memory to fetch |

**Response — 200 OK**

```json
{
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "persona_id": "660e8400-e29b-41d4-a716-446655440001",
    "tier": "canonical",
    "content": "Dad made pancakes every Sunday morning with blueberries from the garden",
    "content_type": "narrative",
    "valid_from": null,
    "valid_to": null,
    "memory_date": "1998-06-14",
    "source_date": "2025-12-01T18:30:00Z",
    "source_type": "family_upload",
    "source_ref": null,
    "disclosure_scope": "family",
    "supersedes": null,
    "superseded_by": null,
    "version": 1,
    "is_active": true,
    "approved_by": null,
    "approved_at": null,
    "created_at": "2025-12-01T18:30:00Z",
    "metadata": {}
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `MEMORY_NOT_FOUND` | `memory_id` does not exist or is not active |

---

#### `GET /api/v1/memories` — List memories with pagination and filters

**Query Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `offset` | `integer` | no | Pagination offset. Default: `0` |
| `limit` | `integer` | no | Page size. Default: `25`, max: `100` |
| `tier` | `string` | no | Filter by tier: `canonical`, `derived`, `accrued`, `world` |
| `content_type` | `string` | no | Filter by content type |
| `disclosure_scope` | `string` | no | Filter by disclosure scope |
| `sort` | `string` | no | Sort field: `created_at` (default), `memory_date`, `source_date` |
| `order` | `string` | no | `asc` or `desc`. Default: `desc` |
| `include_inactive` | `boolean` | no | Include `is_active = false` memories. Default: `false` |

**Response — 200 OK**

```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "persona_id": "660e8400-e29b-41d4-a716-446655440001",
      "tier": "canonical",
      "content": "Dad made pancakes every Sunday morning with blueberries from the garden",
      "content_type": "narrative",
      "memory_date": "1998-06-14",
      "disclosure_scope": "family",
      "version": 2,
      "is_active": true,
      "created_at": "2025-12-01T18:30:00Z"
    }
  ],
  "pagination": {
    "total": 142,
    "offset": 0,
    "limit": 25,
    "has_more": true
  }
}
```

---

### 3.3 Quarantine & Adjudication

#### `GET /api/v1/quarantine` — List quarantined memories

**Query Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `priority` | `string` | no | Filter by priority: `critical`, `high`, `medium`, `low` |
| `status` | `string` | no | Filter by status: `pending`, `adjudicated`, `promoted`, `rejected`. Default: `pending` |
| `offset` | `integer` | no | Pagination offset. Default: `0` |
| `limit` | `integer` | no | Page size. Default: `25`, max: `100` |

**Response — 200 OK**

```json
{
  "data": [
    {
      "id": "770e8400-e29b-41d4-a716-446655440002",
      "persona_id": "660e8400-e29b-41d4-a716-446655440001",
      "failed_gates": ["novelty", "pertinence"],
      "priority": "medium",
      "status": "pending",
      "candidate_data": {
        "content": "He sometimes said he didn't like the city",
        "content_type": "narrative",
        "tier": "accrued"
      },
      "adjudicated_by": null,
      "adjudicated_at": null,
      "created_at": "2025-12-01T19:00:00Z"
    }
  ],
  "pagination": {
    "total": 7,
    "offset": 0,
    "limit": 25,
    "has_more": false
  }
}
```

---

#### `POST /api/v1/quarantine/{quarantine_id}/accept` — Accept a quarantined memory

Promotes the quarantined candidate into the memory graph as an active node.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `quarantine_id` | `UUID` | Quarantine entry to accept |

**Request**

```json
{
  "note": "Confirmed by family — Dad did prefer the country"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `note` | `string` | no | Adjudication note for audit trail |

**Response — 200 OK**

```json
{
  "data": {
    "quarantine_id": "770e8400-e29b-41d4-a716-446655440002",
    "status": "promoted",
    "memory_id": "110e8400-e29b-41d4-a716-446655440005",
    "adjudicated_at": "2025-12-04T11:00:00Z"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `QUARANTINE_NOT_FOUND` | `quarantine_id` does not exist |
| 409 | `ALREADY_ADJUDICATED` | Entry is not in `pending` status |

---

#### `POST /api/v1/quarantine/{quarantine_id}/reject` — Permanently reject

Rejects the quarantined candidate. It will not enter the memory graph.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `quarantine_id` | `UUID` | Quarantine entry to reject |

**Request**

```json
{
  "note": "Duplicate of existing canonical memory about fishing"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `note` | `string` | no | Rejection reason for audit trail |

**Response — 200 OK**

```json
{
  "data": {
    "quarantine_id": "770e8400-e29b-41d4-a716-446655440002",
    "status": "rejected",
    "adjudicated_at": "2025-12-04T11:05:00Z"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `QUARANTINE_NOT_FOUND` | `quarantine_id` does not exist |
| 409 | `ALREADY_ADJUDICATED` | Entry is not in `pending` status |

---

#### `POST /api/v1/quarantine/{quarantine_id}/modify` — Accept with modifications

Accepts the quarantined candidate but applies modifications before promoting it to the memory graph.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `quarantine_id` | `UUID` | Quarantine entry to modify and accept |

**Request**

```json
{
  "modifications": {
    "content": "He preferred the countryside over the city",
    "content_type": "fact",
    "disclosure_scope": "close_friends"
  },
  "note": "Refined wording and broadened disclosure"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `modifications` | `object` | yes | Fields to merge into the candidate before promotion |
| `note` | `string` | no | Adjudication note for audit trail |

The `modifications` object supports the same fields as the `POST /api/v1/memories` request body (excluding `tier` for `canonical` — INV-CI).

**Response — 200 OK**

```json
{
  "data": {
    "quarantine_id": "770e8400-e29b-41d4-a716-446655440002",
    "status": "promoted",
    "memory_id": "120e8400-e29b-41d4-a716-446655440006",
    "applied_modifications": {
      "content": "He preferred the countryside over the city",
      "content_type": "fact",
      "disclosure_scope": "close_friends"
    },
    "adjudicated_at": "2025-12-04T11:10:00Z"
  }
}
```

**Error Responses**

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `VALIDATION_ERROR` | Empty `modifications` or invalid field values |
| 404 | `QUARANTINE_NOT_FOUND` | `quarantine_id` does not exist |
| 409 | `ALREADY_ADJUDICATED` | Entry is not in `pending` status |
| 422 | `IMMUTABLE_TIER` | Attempting to set `tier` to `canonical` via modification |

---

### 3.4 Health & System

#### `GET /api/v1/health` — Liveness and readiness probe

Returns the service health status. Intended for orchestration (Kubernetes, Docker healthcheck, load balancers).

**Response — 200 OK**

```json
{
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "checks": {
      "database": "ok",
      "embedding_service": "ok"
    },
    "uptime_seconds": 86400
  }
}
```

If a dependency is degraded, `status` is `"degraded"` and the failing check reflects the issue:

```json
{
  "data": {
    "status": "degraded",
    "version": "1.0.0",
    "checks": {
      "database": "ok",
      "embedding_service": "unavailable"
    },
    "uptime_seconds": 86400
  }
}
```

**Response — 503 Service Unavailable** (when critical dependency is down)

```json
{
  "data": {
    "status": "unavailable",
    "version": "1.0.0",
    "checks": {
      "database": "connection refused"
    },
    "uptime_seconds": 120
  }
}
```

---

#### `GET /api/v1/stats` — Memory engine statistics

Returns aggregate counts and index health for the persona's memory graph.

**Response — 200 OK**

```json
{
  "data": {
    "memories": {
      "total": 1423,
      "active": 1398,
      "by_tier": {
        "canonical": 45,
        "derived": 312,
        "accrued": 1011,
        "world": 30
      }
    },
    "quarantine": {
      "pending": 7,
      "by_priority": {
        "critical": 0,
        "high": 2,
        "medium": 3,
        "low": 2
      }
    },
    "indexes": {
      "content_embedding": {
        "type": "hnsw",
        "dimensions": 1536,
        "status": "healthy"
      },
      "sensory_embedding": {
        "type": "hnsw",
        "dimensions": 1536,
        "status": "healthy"
      },
      "affect_embedding": {
        "type": "hnsw",
        "dimensions": 512,
        "status": "healthy"
      }
    },
    "edges": {
      "total": 4821
    }
  }
}
```

---

## 4. OpenAPI 3.0 Summary

The above REST interface can be summarized as an OpenAPI 3.0 specification with the following structure:

```yaml
openapi: "3.0.3"
info:
  title: Huible Memory Engine API
  version: "1.0.0"
  description: REST interface for the Huible memory-driven persona engine.
servers:
  - url: /api/v1
security:
  - BearerAuth: []
paths:
  /memories:
    post:
      summary: Store a new memory
      operationId: createMemory
      tags: [Memories]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/CreateMemoryRequest"
      responses:
        "201":
          description: Memory accepted
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MemoryResponse"
        "202":
          description: Memory quarantined
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/QuarantineResponse"
        "400": { $ref: "#/components/responses/BadRequest" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "409": { $ref: "#/components/responses/Conflict" }
        "422": { $ref: "#/components/responses/UnprocessableEntity" }
    get:
      summary: List memories
      operationId: listMemories
      tags: [Memories]
      parameters:
        - $ref: "#/components/parameters/OffsetParam"
        - $ref: "#/components/parameters/LimitParam"
        - name: tier
          in: query
          schema:
            type: string
            enum: [canonical, derived, accrued, world]
        - name: content_type
          in: query
          schema:
            type: string
        - name: disclosure_scope
          in: query
          schema:
            type: string
            enum: [private, family, close_friends, all_contacts]
        - name: sort
          in: query
          schema:
            type: string
            enum: [created_at, memory_date, source_date]
        - name: order
          in: query
          schema:
            type: string
            enum: [asc, desc]
        - name: include_inactive
          in: query
          schema:
            type: boolean
      responses:
        "200":
          description: Paginated memory list
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MemoryListResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
  /memories/{memory_id}:
    parameters:
      - $ref: "#/components/parameters/MemoryIdParam"
    get:
      summary: Fetch a single memory
      operationId: getMemory
      tags: [Memories]
      responses:
        "200":
          description: Memory detail
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MemoryDetailResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
    put:
      summary: Update (supersede) a memory
      operationId: updateMemory
      tags: [Memories]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/UpdateMemoryRequest"
      responses:
        "200":
          description: Memory superseded
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MemoryResponse"
        "400": { $ref: "#/components/responses/BadRequest" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
        "409": { $ref: "#/components/responses/Conflict" }
        "422": { $ref: "#/components/responses/UnprocessableEntity" }
    delete:
      summary: Soft-delete a memory
      operationId: deleteMemory
      tags: [Memories]
      responses:
        "200":
          description: Memory soft-deleted
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DeleteMemoryResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
        "410": { $ref: "#/components/responses/Gone" }
  /retrieve:
    post:
      summary: Semantic retrieval via spreading activation
      operationId: retrieveMemories
      tags: [Retrieval]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/RetrieveRequest"
      responses:
        "200":
          description: Ranked retrieval results
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/RetrieveResponse"
        "400": { $ref: "#/components/responses/BadRequest" }
        "401": { $ref: "#/components/responses/Unauthorized" }
  /quarantine:
    get:
      summary: List quarantined memories
      operationId: listQuarantine
      tags: [Quarantine]
      parameters:
        - $ref: "#/components/parameters/OffsetParam"
        - $ref: "#/components/parameters/LimitParam"
        - name: priority
          in: query
          schema:
            type: string
            enum: [critical, high, medium, low]
        - name: status
          in: query
          schema:
            type: string
            enum: [pending, adjudicated, promoted, rejected]
      responses:
        "200":
          description: Quarantine list
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/QuarantineListResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
  /quarantine/{quarantine_id}/accept:
    post:
      summary: Accept quarantined memory
      operationId: acceptQuarantine
      tags: [Quarantine]
      parameters:
        - $ref: "#/components/parameters/QuarantineIdParam"
      requestBody:
        required: false
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AdjudicationNoteRequest"
      responses:
        "200":
          description: Accepted and promoted
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/AdjudicationResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
        "409": { $ref: "#/components/responses/Conflict" }
  /quarantine/{quarantine_id}/reject:
    post:
      summary: Reject quarantined memory
      operationId: rejectQuarantine
      tags: [Quarantine]
      parameters:
        - $ref: "#/components/parameters/QuarantineIdParam"
      requestBody:
        required: false
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AdjudicationNoteRequest"
      responses:
        "200":
          description: Rejected
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/AdjudicationResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
        "409": { $ref: "#/components/responses/Conflict" }
  /quarantine/{quarantine_id}/modify:
    post:
      summary: Accept with modifications
      operationId: modifyQuarantine
      tags: [Quarantine]
      parameters:
        - $ref: "#/components/parameters/QuarantineIdParam"
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ModifyRequest"
      responses:
        "200":
          description: Modified and promoted
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ModifyResponse"
        "400": { $ref: "#/components/responses/BadRequest" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "404": { $ref: "#/components/responses/NotFound" }
        "409": { $ref: "#/components/responses/Conflict" }
        "422": { $ref: "#/components/responses/UnprocessableEntity" }
  /health:
    get:
      summary: Liveness and readiness
      operationId: health
      tags: [System]
      responses:
        "200":
          description: Healthy or degraded
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/HealthResponse"
        "503":
          description: Unavailable
  /stats:
    get:
      summary: Engine statistics
      operationId: stats
      tags: [System]
      responses:
        "200":
          description: Stats
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/StatsResponse"
        "401": { $ref: "#/components/responses/Unauthorized" }

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
  parameters:
    MemoryIdParam:
      name: memory_id
      in: path
      required: true
      schema:
        type: string
        format: uuid
    QuarantineIdParam:
      name: quarantine_id
      in: path
      required: true
      schema:
        type: string
        format: uuid
    OffsetParam:
      name: offset
      in: query
      schema:
        type: integer
        default: 0
    LimitParam:
      name: limit
      in: query
      schema:
        type: integer
        default: 25
        maximum: 100
  schemas:
    CreateMemoryRequest:
      type: object
      required: [content]
      properties:
        content:
          type: string
          minLength: 1
        content_type:
          type: string
          enum: [narrative, fact, sensory, relationship, preference]
        tier:
          type: string
          enum: [canonical, derived, accrued, world]
        source_type:
          type: string
          enum: [extraction, family_upload, canonical_seed, inference]
        memory_date:
          type: string
          format: date
        disclosure_scope:
          type: string
          enum: [private, family, close_friends, all_contacts]
        metadata:
          type: object
    UpdateMemoryRequest:
      type: object
      properties:
        content:
          type: string
          minLength: 1
        content_type:
          type: string
          enum: [narrative, fact, sensory, relationship, preference]
        disclosure_scope:
          type: string
          enum: [private, family, close_friends, all_contacts]
        metadata:
          type: object
    RetrieveRequest:
      type: object
      required: [query]
      properties:
        query:
          type: string
          minLength: 1
        disclosure_tier:
          type: string
          enum: [private, family, close_friends, all_contacts]
        top_k:
          type: integer
          minimum: 1
          maximum: 50
        exclude_memory_ids:
          type: array
          items:
            type: string
            format: uuid
        search_mode:
          type: string
          enum: [content, sensory, affect]
    AdjudicationNoteRequest:
      type: object
      properties:
        note:
          type: string
    ModifyRequest:
      type: object
      required: [modifications]
      properties:
        modifications:
          type: object
          properties:
            content:
              type: string
            content_type:
              type: string
              enum: [narrative, fact, sensory, relationship, preference]
            disclosure_scope:
              type: string
              enum: [private, family, close_friends, all_contacts]
            memory_date:
              type: string
              format: date
        note:
          type: string
    ErrorEnvelope:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, status, message]
          properties:
            code:
              type: string
            status:
              type: integer
            message:
              type: string
            details:
              type: object
    MemoryResponse:
      type: object
      properties:
        data:
          $ref: "#/components/schemas/Memory"
    MemoryDetailResponse:
      type: object
      properties:
        data:
          $ref: "#/components/schemas/MemoryDetail"
    MemoryListResponse:
      type: object
      properties:
        data:
          type: array
          items:
            $ref: "#/components/schemas/Memory"
        pagination:
          $ref: "#/components/schemas/Pagination"
    QuarantineResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            id:
              type: string
            quarantine_id:
              type: string
            status:
              type: string
            failed_gates:
              type: array
              items:
                type: string
            priority:
              type: string
            message:
              type: string
    QuarantineListResponse:
      type: object
      properties:
        data:
          type: array
          items:
            $ref: "#/components/schemas/QuarantineEntry"
        pagination:
          $ref: "#/components/schemas/Pagination"
    AdjudicationResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            quarantine_id:
              type: string
            status:
              type: string
            memory_id:
              type: string
            adjudicated_at:
              type: string
              format: date-time
    ModifyResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            quarantine_id:
              type: string
            status:
              type: string
            memory_id:
              type: string
            applied_modifications:
              type: object
            adjudicated_at:
              type: string
              format: date-time
    RetrieveResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            results:
              type: array
              items:
                $ref: "#/components/schemas/ActivatedMemory"
            query:
              type: string
            disclosure_tier:
              type: string
    DeleteMemoryResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            id:
              type: string
            status:
              type: string
            is_active:
              type: boolean
            deleted_at:
              type: string
              format: date-time
    HealthResponse:
      type: object
      properties:
        data:
          type: object
          properties:
            status:
              type: string
            version:
              type: string
            checks:
              type: object
              additionalProperties:
                type: string
            uptime_seconds:
              type: integer
    StatsResponse:
      type: object
      properties:
        data:
          type: object
    Pagination:
      type: object
      properties:
        total:
          type: integer
        offset:
          type: integer
        limit:
          type: integer
        has_more:
          type: boolean
    Memory:
      type: object
      properties:
        id:
          type: string
          format: uuid
        persona_id:
          type: string
          format: uuid
        tier:
          type: string
        content:
          type: string
        content_type:
          type: string
        memory_date:
          type: string
          format: date
        disclosure_scope:
          type: string
        source_type:
          type: string
        version:
          type: integer
        is_active:
          type: boolean
        created_at:
          type: string
          format: date-time
    MemoryDetail:
      type: object
      properties:
        id:
          type: string
          format: uuid
        persona_id:
          type: string
          format: uuid
        tier:
          type: string
        content:
          type: string
        content_type:
          type: string
        valid_from:
          type: string
          format: date-time
        valid_to:
          type: string
          format: date-time
        memory_date:
          type: string
          format: date
        source_date:
          type: string
          format: date-time
        source_type:
          type: string
        source_ref:
          type: object
        disclosure_scope:
          type: string
        supersedes:
          type: string
          format: uuid
        superseded_by:
          type: string
          format: uuid
        version:
          type: integer
        is_active:
          type: boolean
        approved_by:
          type: string
          format: uuid
        approved_at:
          type: string
          format: date-time
        created_at:
          type: string
          format: date-time
        metadata:
          type: object
    QuarantineEntry:
      type: object
      properties:
        id:
          type: string
          format: uuid
        persona_id:
          type: string
          format: uuid
        failed_gates:
          type: array
          items:
            type: string
        priority:
          type: string
        status:
          type: string
        candidate_data:
          type: object
        adjudicated_by:
          type: string
          format: uuid
        adjudicated_at:
          type: string
          format: date-time
        created_at:
          type: string
          format: date-time
    ActivatedMemory:
      type: object
      properties:
        memory:
          $ref: "#/components/schemas/Memory"
        activation_score:
          type: number
          format: float
  responses:
    BadRequest:
      description: Validation error
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
    Unauthorized:
      description: Authentication required
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
    NotFound:
      description: Resource not found
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
    Conflict:
      description: State conflict
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
    Gone:
      description: Resource already deleted
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
    UnprocessableEntity:
      description: Business rule violation
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorEnvelope"
```

---

## 5. Data Model Mapping

The API maps directly to the internal data model defined in `src/huible/memory/protocol.py` and `src/huible/memory/models.py`:

| API Field | Protocol Type | DB Column |
|-----------|--------------|-----------|
| `tier` | `MemoryTier` | `memories.tier` |
| `content_type` | `ContentType` | `memories.content_type` |
| `source_type` | `SourceType` | `memories.source_type` |
| `disclosure_scope` | `DisclosureScope` | `memories.disclosure_scope` |
| `priority` | `QuarantinePriority` | `quarantine.priority` |
| `status` (quarantine) | `QuarantineStatus` | `quarantine.status` |

All enum values are transmitted as lowercase strings matching the enum member name.

---

## 6. Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `AUTH_REQUIRED` | 401 | Missing or invalid `Authorization` header |
| `FORBIDDEN` | 403 | API key persona scope mismatch |
| `VALIDATION_ERROR` | 400 | Malformed request body, missing required fields, invalid enum values |
| `MEMORY_NOT_FOUND` | 404 | Memory ID does not exist or is not active |
| `QUARANTINE_NOT_FOUND` | 404 | Quarantine entry ID does not exist |
| `DUPLICATE_MEMORY` | 409 | Near-duplicate detected (cosine similarity > 0.92) |
| `CONFLICT` | 409 | Target memory already superseded or quarantined entry already adjudicated |
| `ALREADY_ADJUDICATED` | 409 | Quarantine entry not in `pending` status |
| `ALREADY_DELETED` | 410 | Memory already soft-deleted (`is_active = false`) |
| `SAFETY_REJECTION` | 422 | Safety gate failure: adversarial or injection content |
| `IMMUTABLE_TIER` | 422 | Attempt to modify a canonical-tier memory (INV-CI) |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

---

## 7. References

- Engine Specification: `docs/ENGINE_SPEC.md`
- Data Model: `src/huible/memory/models.py`, `src/huible/memory/protocol.py`
- Memory Retrieval (Spreading Activation): `src/huible/memory/retrieval.py`
- Ingestion Pipeline (Five-Gate Firewall): `src/huible/ingestion/pipeline.py`
- Quarantine Queue: `src/huible/ingestion/quarantine.py`
- Adjudication API (internal): `src/huible/api/adjudication.py`
