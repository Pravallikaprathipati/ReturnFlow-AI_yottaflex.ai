PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    created_date TEXT NOT NULL,
    region TEXT NOT NULL CHECK (region IN ('north', 'south', 'east', 'west', 'central')),
    customer_segment TEXT NOT NULL CHECK (customer_segment IN ('standard', 'loyalty', 'premium'))
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    order_total_cents INTEGER NOT NULL CHECK (order_total_cents >= 0),
    order_status TEXT NOT NULL CHECK (order_status IN ('delivered', 'cancelled')),
    delivered_date TEXT
);
CREATE TABLE order_lines (
    order_line_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    line_number INTEGER NOT NULL CHECK (line_number > 0),
    sku TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('apparel', 'electronics', 'home', 'final_sale')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    discount_cents INTEGER NOT NULL CHECK (discount_cents >= 0),
    tax_cents INTEGER NOT NULL CHECK (tax_cents >= 0),
    paid_total_cents INTEGER NOT NULL CHECK (paid_total_cents >= 0),
    UNIQUE (order_id, line_number)
);
CREATE TABLE return_requests (
    return_request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    requested_at TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('no_longer_needed', 'damaged', 'wrong_item', 'exchange')),
    manager_override INTEGER NOT NULL CHECK (manager_override IN (0, 1))
);
CREATE TABLE return_lines (
    return_line_id TEXT PRIMARY KEY,
    return_request_id TEXT NOT NULL REFERENCES return_requests(return_request_id),
    order_line_id TEXT NOT NULL REFERENCES order_lines(order_line_id),
    line_number INTEGER NOT NULL CHECK (line_number > 0),
    requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
    approved_quantity INTEGER NOT NULL CHECK (approved_quantity >= 0),
    UNIQUE (return_request_id, line_number)
);
CREATE TABLE return_authorizations (
    authorization_id TEXT PRIMARY KEY,
    return_request_id TEXT NOT NULL UNIQUE REFERENCES return_requests(return_request_id),
    authorized_at TEXT NOT NULL,
    authorization_status TEXT NOT NULL CHECK (authorization_status IN ('approved', 'partial', 'rejected'))
);
CREATE TABLE return_shipments (
    shipment_id TEXT PRIMARY KEY,
    authorization_id TEXT NOT NULL UNIQUE REFERENCES return_authorizations(authorization_id),
    shipped_at TEXT NOT NULL,
    received_at TEXT,
    shipment_status TEXT NOT NULL CHECK (shipment_status IN ('in_transit', 'received', 'missing'))
);
CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES return_shipments(shipment_id),
    return_line_id TEXT NOT NULL UNIQUE REFERENCES return_lines(return_line_id),
    inspected_at TEXT NOT NULL,
    condition TEXT NOT NULL CHECK (condition IN ('sellable', 'minor_damage', 'missing_parts', 'unsafe')),
    inspection_notes TEXT NOT NULL
);
CREATE TABLE refunds (
    refund_id TEXT PRIMARY KEY,
    return_line_id TEXT NOT NULL REFERENCES return_lines(return_line_id),
    issued_at TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    refund_status TEXT NOT NULL CHECK (refund_status IN ('paid', 'blocked_policy', 'blocked_duplicate')),
    payment_reference TEXT UNIQUE
);
CREATE UNIQUE INDEX one_paid_refund_per_return_line
    ON refunds(return_line_id) WHERE refund_status = 'paid';
CREATE TABLE inventory_dispositions (
    disposition_id TEXT PRIMARY KEY,
    return_line_id TEXT NOT NULL REFERENCES return_lines(return_line_id),
    inspection_id TEXT NOT NULL REFERENCES inspections(inspection_id),
    decided_at TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('restock', 'refurbish', 'quarantine', 'scrap')),
    movement_status TEXT NOT NULL CHECK (movement_status IN ('completed', 'blocked_sequence'))
);
CREATE TABLE return_events (
    event_id TEXT PRIMARY KEY,
    return_request_id TEXT NOT NULL REFERENCES return_requests(return_request_id),
    event_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'requested', 'authorized', 'shipment_created', 'shipment_received',
        'inspected', 'refund_paid', 'refund_blocked', 'disposition_decided'
    )),
    event_detail TEXT NOT NULL
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_order_lines_order ON order_lines(order_id);
CREATE INDEX idx_return_requests_order ON return_requests(order_id);
CREATE INDEX idx_return_lines_request ON return_lines(return_request_id);
CREATE INDEX idx_events_request_time ON return_events(return_request_id, event_at);
