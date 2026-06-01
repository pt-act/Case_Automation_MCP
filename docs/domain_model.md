# Domain Model

## `Contact`

A person involved in one or more matters (client, attorney, rep).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `source` | `string` | ✓ | System that owns this record, e.g. 'crm', 'case'. |
| `name` | `string` | ✓ | Full legal name. |
| `email` | `string | null` |  | Primary email address. |
| `phone` | `string | null` |  | Primary phone number. |
| `role` | `string | null` |  | Role in the firm's context, e.g. 'client', 'attorney'. |
| `external_ids` | `object` |  | Cross-system identifiers, e.g. {'crm': '...', 'case': '...'}. |

## `Matter`

A case / file / engagement (here: an immigration case).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `source` | `string` | ✓ | System that owns this record. |
| `reference` | `string` | ✓ | Firm-assigned matter reference number. |
| `title` | `string` | ✓ | Short descriptive title. |
| `status` | `string` | ✓ | Current matter status string (vendor-specific values normalised). |
| `practice_area` | `string | null` |  | Practice area, e.g. 'family-based', 'employment-based'. |
| `client` | `Contact` | ✓ | Primary client contact. |
| `responsible` | `string | null` |  | Responsible attorney id or name. |
| `opened_at` | `string` | ✓ | UTC instant the matter was opened. |
| `key_dates` | `array` |  | Tracked deadlines for this matter. |
| `external_ids` | `object` |  | Cross-system identifiers. |

## `Document`

A stored document associated with a matter.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `matter_id` | `string` | ✓ | Parent matter. |
| `name` | `string` | ✓ | File name. |
| `mime_type` | `string` | ✓ | MIME type, e.g. 'application/pdf'. |
| `uri` | `string` | ✓ | Storage URI (object store or doc-system path). |
| `classification` | `string | null` |  | Document class, e.g. 'engagement_letter', 'court_filing'. |
| `version` | `integer` | ✓ | Monotonically increasing version counter. |
| `privileged` | `boolean` |  | Attorney–client privilege flag. |
| `checksum` | `string` | ✓ | SHA-256 hex digest of the stored bytes. |
| `created_at` | `string` | ✓ | UTC creation instant. |

## `Deadline`

A date obligation tracked by the deadline engine.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `matter_id` | `string` | ✓ | Parent matter. |
| `name` | `string` | ✓ | Human-readable obligation name. |
| `due_at` | `string` | ✓ | UTC deadline instant. |
| `rule_id` | `string | null` |  | Versioned rule that computed this date. |
| `status` | `string` |  | Lifecycle status of the deadline. |
| `escalation_level` | `integer` |  | Monotonically increasing escalation counter. |

## `Communication`

An email or message, inbound or outbound.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `matter_id` | `string | null` |  | Associated matter (None before matter is created). |
| `direction` | `string` | ✓ | Message direction relative to the firm. |
| `channel` | `string` | ✓ | Delivery channel, e.g. 'email'. |
| `subject` | `string | null` |  | Message subject line. |
| `body` | `string` | ✓ | Message body text. |
| `status` | `string` | ✓ | Lifecycle status: 'draft' | 'pending_approval' | 'sent'. |
| `participants` | `array` |  | All contacts on this communication. |

## `Task`

An actionable task attached to a matter.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | ✓ | Internal canonical id. |
| `matter_id` | `string` | ✓ | Parent matter. |
| `title` | `string` | ✓ | Task description. |
| `assignee` | `string | null` |  | Assignee id or role. |
| `due_at` | `string | null` |  | Optional due date (UTC). |
| `status` | `string` | ✓ | Task status string, e.g. 'open', 'done'. |

## `AuditRecord`

One entry in the append-only, hash-chained audit log.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `integer` | ✓ | Monotonic PK / sequence — defines chain order. |
| `actor` | `string` | ✓ | Identity that performed the action. |
| `action` | `string` | ✓ | Action name, e.g. 'matter.create'. |
| `inputs` | `object` |  | PII-scrubbed action inputs (JSONB). |
| `outputs` | `object` |  | PII-scrubbed action outputs (JSONB). |
| `approval` | `object | null` |  | Gate outcome if this action required approval. |
| `timestamp` | `string` | ✓ | UTC server-sourced timestamp. |
| `run_id` | `string | null` |  | Workflow run correlation id. |
| `prev_hash` | `string` | ✓ | SHA-256 hex of the previous record (zeros for genesis). |
| `record_hash` | `string` | ✓ | SHA-256 hex of prev_hash + canonical(this record). |

## `FeatureFlag`

Per-workflow feature flag for wave-by-wave rollout.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | `string` | ✓ | Workflow or feature key, e.g. 'intake'. |
| `enabled` | `boolean` |  | Whether the feature is enabled. Default: off. |

## `ACL`

Access-control descriptor for DocStoreConnector.move. Owned by connector-framework.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `principals` | `array` |  | Principal ids with access. |
| `permission` | `string` |  | Permission level granted. |
| `external` | `boolean` |  | True if any principal is external to the firm. |
