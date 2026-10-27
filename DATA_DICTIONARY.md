# Schema and Data Dictionary

All data are synthetic. Database table names use `snake_case`; CSV files use the same names as their tables. Primary keys are immutable text IDs unless stated otherwise. Dates are ISO-8601 `YYYY-MM-DD`; monetary values are integer USD cents. Required means `NOT NULL` and/or guarded by a schema check. Empty values in CSV correspond to SQL `NULL`.

## Relationships and cardinality

| Relationship | Cardinality | Optionality / enforcement |
|---|---|---|
| Customer → Order | 1:N | Every order has exactly one customer; a customer may have no orders. |
| Order → OrderLine | 1:N | Every line belongs to one order; each order has one or more lines in generated data. |
| Order → ReturnRequest | 1:N | Every request belongs to one order; an order may have no requests. |
| ReturnRequest ↔ OrderLine, through ReturnLine | N:M | `return_lines` is the bridge and carries requested/approved quantities; each bridge row references exactly one request and one purchased line. |
| ReturnRequest → ReturnAuthorization | 1:1 | Exactly one authorization per request; the unique request FK enforces this. |
| ReturnAuthorization → ReturnShipment | 1:0..1 | A rejected request or lost/non-shipped return may have no shipment; unique FK allows at most one. |
| ReturnShipment → Inspection | 1:N | A received shipment can contain multiple inspected return lines; no inspection is created for a missing shipment. |
| ReturnLine → Inspection | 1:0..1 | Inspection is optional and unique per return line. |
| ReturnLine → Refund | 1:N | Refund attempts are retained; a partial unique index allows at most one `paid` refund. |
| ReturnLine → InventoryDisposition | 1:N | Disposition decisions are retained with inspection references; generated flow creates one decision per inspected line. |
| ReturnRequest → ReturnEvent | 1:N | Append-only dated lifecycle events; events are ordered by event date and ID. |

Lifecycle is represented with explicit statuses and immutable `return_events` entries. Authorization, shipment, refund, and movement statuses are distinct: a blocked attempt remains visible but is never counted as a completed payout or inventory movement. All foreign keys are enabled in the generated SQLite database.

## Field definitions

### Customer (`customers.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `customer_id` | TEXT; yes | `CUS-` plus six-digit counter | Primary key; immutable. |
| `created_date` | DATE; yes | Calendar date | Must parse as ISO date. |
| `region` | TEXT; yes | `north`, `south`, `east`, `west`, `central` | Enum check. |
| `customer_segment` | TEXT; yes | `standard`, `loyalty`, `premium` | Enum check. |

### Order (`orders.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `order_id` | TEXT; yes | `ORD-2026-` plus six-digit counter | Primary key. |
| `customer_id` | TEXT; yes | Existing customer ID | FK to `customers.customer_id`. |
| `order_date` | DATE; yes | Calendar date | Must parse; no earlier than customer creation is not required by the demo. |
| `currency` | TEXT; yes | `USD` | Enum check; one currency in this version. |
| `order_total_cents` | INTEGER; yes | Nonnegative cents | Equals sum of line paid totals. |
| `order_status` | TEXT; yes | `delivered`, `cancelled` | Enum check. Generated orders are delivered. |
| `delivered_date` | DATE; optional | Calendar date or NULL | If present, on/after order date. |

### OrderLine (`order_lines.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `order_line_id` | TEXT; yes | `<order_id>-L` plus two-digit line number | Primary key. |
| `order_id` | TEXT; yes | Existing order ID | FK to `orders.order_id`. |
| `line_number` | INTEGER; yes | Positive ordinal | Unique with `order_id`. |
| `sku` | TEXT; yes | Synthetic `SKU-` identifier | No real catalog data. |
| `category` | TEXT; yes | `apparel`, `electronics`, `home`, `final_sale` | Enum; determines return policy. |
| `quantity` | INTEGER; yes | Purchased units | Positive integer. |
| `unit_price_cents` | INTEGER; yes | Cents per unit | Nonnegative. |
| `discount_cents` | INTEGER; yes | Total line discount in cents | Nonnegative and no greater than gross in generated records. |
| `tax_cents` | INTEGER; yes | Total line tax in cents | Nonnegative. |
| `paid_total_cents` | INTEGER; yes | Net line amount in cents | `quantity × unit_price - discount + tax`. |

### ReturnRequest (`return_requests.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `return_request_id` | TEXT; yes | `RMA-2026-` plus six-digit counter | Primary key. |
| `order_id` | TEXT; yes | Existing order ID | FK to `orders.order_id`; request lines must be from this order. |
| `requested_at` | DATE; yes | Calendar date | Must be on/after order date. |
| `reason` | TEXT; yes | `no_longer_needed`, `damaged`, `wrong_item`, `exchange` | Enum check; damaged returns do not incur the sample return fee. |
| `manager_override` | INTEGER; yes | `0` or `1` | Boolean check; does not override final-sale exclusion. |

### ReturnLine (`return_lines.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `return_line_id` | TEXT; yes | `RMAL-<RMA ID>-` plus two-digit line number | Primary key. |
| `return_request_id` | TEXT; yes | Existing request ID | FK to `return_requests.return_request_id`. |
| `order_line_id` | TEXT; yes | Existing purchased-line ID | FK to `order_lines.order_line_id`; parent order must match request order. |
| `line_number` | INTEGER; yes | Positive ordinal within request | Unique with `return_request_id`. |
| `requested_quantity` | INTEGER; yes | Requested units | Positive. Must not exceed purchased quantity except one labelled edge case. |
| `approved_quantity` | INTEGER; yes | Approved units | Nonnegative; zero when ineligible or quantity-invalid; otherwise equals requested quantity. |

### ReturnAuthorization (`return_authorizations.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `authorization_id` | TEXT; yes | `AUTH-2026-` plus six-digit counter | Primary key. |
| `return_request_id` | TEXT; yes | Existing request ID | Unique FK; exactly one authorization per request. |
| `authorized_at` | DATE; yes | Calendar date | On/after request date. |
| `authorization_status` | TEXT; yes | `approved`, `partial`, `rejected` | Derived from approved line quantities. |

### ReturnShipment (`return_shipments.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `shipment_id` | TEXT; yes | `RTS-2026-` plus six-digit counter | Primary key. |
| `authorization_id` | TEXT; yes | Existing authorization ID | Unique FK; at most one shipment per authorization. |
| `shipped_at` | DATE; yes | Calendar date | On/after authorization. |
| `received_at` | DATE; optional | Calendar date or NULL | Required for `received`; on/after `shipped_at`. |
| `shipment_status` | TEXT; yes | `in_transit`, `received`, `missing` | Enum; missing shipments cannot be inspected. |

### Inspection (`inspections.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `inspection_id` | TEXT; yes | `INSP-2026-` plus six-digit counter | Primary key. |
| `shipment_id` | TEXT; yes | Existing shipment ID | FK to `return_shipments.shipment_id`; shipment must be received. |
| `return_line_id` | TEXT; yes | Existing return-line ID | Unique FK; at most one inspection per line. |
| `inspected_at` | DATE; yes | Calendar date | On/after shipment receipt. |
| `condition` | TEXT; yes | `sellable`, `minor_damage`, `missing_parts`, `unsafe` | Enum; drives disposition. |
| `inspection_notes` | TEXT; yes | Synthetic explanatory note | Required; no personal information. |

### Refund (`refunds.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `refund_id` | TEXT; yes | `REF-2026-` plus six-digit counter | Primary key. |
| `return_line_id` | TEXT; yes | Existing return-line ID | FK; multiple attempts retained per line. |
| `issued_at` | DATE; yes | Calendar date | On/after inspection for generated attempts. |
| `amount_cents` | INTEGER; yes | Attempted/refunded cents | Nonnegative; paid amount reconciles to approved quantity and never exceeds paid line amount. |
| `refund_status` | TEXT; yes | `paid`, `blocked_policy`, `blocked_duplicate` | Enum; only `paid` has a payment reference. |
| `payment_reference` | TEXT; optional | Synthetic `PAY-` reference or NULL | Unique when present; unique partial index permits one paid refund per return line. |

### InventoryDisposition (`inventory_dispositions.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `disposition_id` | TEXT; yes | `DISP-2026-` plus six-digit counter | Primary key. |
| `return_line_id` | TEXT; yes | Existing return-line ID | FK to `return_lines.return_line_id`. |
| `inspection_id` | TEXT; yes | Existing inspection ID | FK to `inspections.inspection_id`; condition must match the decision. |
| `decided_at` | DATE; yes | Calendar date | Must be on/after inspection before movement can complete. |
| `disposition` | TEXT; yes | `restock`, `refurbish`, `quarantine`, `scrap` | Condition-derived enum. |
| `movement_status` | TEXT; yes | `completed`, `blocked_sequence` | Only completed movements change inventory; early movement is blocked. |

### ReturnEvent (`return_events.csv`)

| Field | Type; required | Allowed values / units | Uniqueness and validation |
|---|---|---|---|
| `event_id` | TEXT; yes | `EVT-2026-` plus six-digit counter | Primary key. |
| `return_request_id` | TEXT; yes | Existing request ID | FK; append-only history for lifecycle transitions. |
| `event_at` | DATE; yes | Calendar date | On/after order date; events are exported chronologically per request. |
| `event_type` | TEXT; yes | `requested`, `authorized`, `shipment_created`, `shipment_received`, `inspected`, `refund_paid`, `refund_blocked`, `disposition_decided` | Enum check. |
| `event_detail` | TEXT; yes | Synthetic status or related identifier | Required, no personal information. |

### Ground truth (`ground_truth.csv`, `ground_truth.json`)

Ground truth is deliberately stored outside the normalized input tables. `issue_id` is a unique `GT-` label; `entity_type` and `entity_id` point to the affected input record; `issue_type` names one of the five deterministic anomaly classes; `expected_decision` records the rule-based outcome; `details` explains the known exception. Ground truth must not be joined into model input features. It is also checked against the primary keys in the corresponding entity tables.

## Effective dates and lifecycle

The sample policy is effective for all generated records: apparel 45 days, electronics/home 30 days, final-sale excluded; manager overrides allow late returns only for non-excluded categories. No historic policy-version table is needed because the simulator has a single documented policy period. A production extension should version policy rules and persist the policy version used by each decision.

Lifecycle: request → authorization → optional shipment → optional inspection → refund attempt and disposition decision. A missing shipment stops inspection, refund, and disposition. Rejected lines have zero approved units. State transitions are retained in `return_events`; historical facts are not overwritten.
