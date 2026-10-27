"""Seeded generator for the full synthetic returns lifecycle."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .database import SCHEMA, TABLES, create_database
from .policy import (
    DISPOSITIONS,
    INSPECTION_CONDITIONS,
    RETURN_REASONS,
    calculate_refund_cents,
    disposition_for,
    is_eligible,
)

DEFAULT_SEED = 20261022
REFERENCE_DATE = date(2026, 10, 22)
ANOMALY_TYPES = (
    "quantity_exceeds_purchase",
    "refund_exceeds_paid_amount",
    "restock_before_inspection",
    "late_return_without_override",
    "duplicate_refund_attempt",
)
REGIONS = ("north", "south", "east", "west", "central")
SEGMENTS = ("standard", "loyalty", "premium")
CATEGORIES = ("apparel", "electronics", "home", "final_sale")
CONDITIONS = tuple(sorted(INSPECTION_CONDITIONS))
REASONS = tuple(sorted(RETURN_REASONS))
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "ground_truth": (
        "issue_id",
        "entity_type",
        "entity_id",
        "issue_type",
        "expected_decision",
        "details",
    )
}


def _id(prefix: str, counter: int) -> str:
    return f"{prefix}-2026-{counter:06d}"


def _date(value: date) -> str:
    return value.isoformat()


def _add_days(value: date, days: int) -> date:
    return value + timedelta(days=days)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _insert_many(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _event(
    event_number: int, request_id: str, at: date, kind: str, detail: str
) -> dict[str, Any]:
    return {
        "event_id": _id("EVT", event_number),
        "return_request_id": request_id,
        "event_at": _date(at),
        "event_type": kind,
        "event_detail": detail,
    }


def _ground_truth(
    issue_number: int,
    entity_type: str,
    entity_id: str,
    issue_type: str,
    expected_decision: str,
    details: str,
) -> dict[str, Any]:
    return {
        "issue_id": f"GT-{issue_number:03d}",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "issue_type": issue_type,
        "expected_decision": expected_decision,
        "details": details,
    }


def generate_dataset(
    output_dir: Path,
    *,
    order_count: int,
    request_count: int,
    return_line_count: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Generate a relational dataset and deterministic exports."""
    if order_count < 5 or request_count < 7 or return_line_count < request_count:
        raise ValueError("Require at least 5 orders, 7 requests, and one line per request.")
    if request_count > order_count * 3:
        raise ValueError("Request count must not exceed three times the order count.")
    if return_line_count > order_count * 4:
        raise ValueError("Return-line count cannot exceed the maximum generated order lines.")
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "returns.sqlite3"
    if db_path.exists():
        db_path.unlink()
    rng = random.Random(seed)
    connection = create_database(db_path)
    ground_truth: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    ids_by_request: dict[str, list[str]] = {}
    line_to_request: dict[str, str] = {}
    lines_by_id: dict[str, dict[str, Any]] = {}
    orders_by_id: dict[str, dict[str, Any]] = {}
    order_lines: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    return_lines: list[dict[str, Any]] = []
    authorizations: list[dict[str, Any]] = []
    shipments: list[dict[str, Any]] = []
    inspections: list[dict[str, Any]] = []
    refunds: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    customer_count = max(5, min(order_count // 3, 10_000))
    customers = [
        {
            "customer_id": f"CUS-{number:06d}",
            "created_date": _date(_add_days(date(2024, 1, 1), rng.randrange(0, 900))),
            "region": rng.choice(REGIONS),
            "customer_segment": rng.choice(SEGMENTS),
        }
        for number in range(1, customer_count + 1)
    ]

    start_date = date(2025, 10, 1)
    order_lines_by_order: dict[str, list[dict[str, Any]]] = {}
    for order_number in range(1, order_count + 1):
        order_id = _id("ORD", order_number)
        order_date = _add_days(start_date, rng.randrange(0, 335))
        customer_id = customers[(order_number - 1) % customer_count]["customer_id"]
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "order_date": _date(order_date),
            "currency": "USD",
            "order_total_cents": 0,
            "order_status": "delivered",
            "delivered_date": _date(_add_days(order_date, rng.randrange(1, 6))),
        }
        orders_by_id[order_id] = order
        line_count = rng.randint(1, 4)
        total = 0
        for line_number in range(1, line_count + 1):
            quantity = rng.randint(1, 5)
            unit_price = rng.choice((1299, 2499, 4999, 7999, 12999, 24999))
            category = rng.choices(CATEGORIES, weights=(35, 25, 25, 15), k=1)[0]
            gross = unit_price * quantity
            discount = (gross * rng.choice((0, 0, 5, 10, 15))) // 100
            tax = ((gross - discount) * 8) // 100
            paid = gross - discount + tax
            total += paid
            order_lines.append(
                {
                    "order_line_id": f"{order_id}-L{line_number:02d}",
                    "order_id": order_id,
                    "line_number": line_number,
                    "sku": f"SKU-{rng.randrange(1000, 10000):05d}",
                    "category": category,
                    "quantity": quantity,
                    "unit_price_cents": unit_price,
                    "discount_cents": discount,
                    "tax_cents": tax,
                    "paid_total_cents": paid,
                }
            )
            order_lines_by_order.setdefault(order_id, []).append(order_lines[-1])
        order["order_total_cents"] = total

    if request_count > len(order_lines) or return_line_count > len(order_lines):
        raise ValueError("Request and return-line counts cannot exceed generated order lines.")
    # Allocate each request to a single order and reserve distinct parents for edge cases.
    maximum_line_count = request_count + 3 * max(0, request_count - 5)
    if return_line_count > maximum_line_count:
        raise ValueError("Return lines exceed the supported per-request line allocation.")
    reserved_lines = []
    reserved_order_ids = set()
    for line in order_lines:
        if line["order_id"] not in reserved_order_ids:
            reserved_lines.append(line)
            reserved_order_ids.add(line["order_id"])
            if len(reserved_lines) == 5:
                break
    for line in reserved_lines:
        line["category"] = "apparel"
    line_counts = [1] * request_count
    remaining = return_line_count - request_count
    while remaining:
        request_index = rng.randrange(5, request_count)
        if line_counts[request_index] < 4:
            line_counts[request_index] += 1
            remaining -= 1

    chosen_orders: list[dict[str, Any] | None] = [None] * request_count
    chosen_lines: list[list[dict[str, Any]]] = [[] for _ in range(request_count)]
    free_lines_by_order = {
        order_id: list(lines) for order_id, lines in order_lines_by_order.items()
    }
    request_slots_by_order = {order_id: 0 for order_id in orders_by_id}
    for request_index, order_line in enumerate(reserved_lines):
        order_id = order_line["order_id"]
        chosen_orders[request_index] = orders_by_id[order_id]
        chosen_lines[request_index] = [order_line]
        free_lines_by_order[order_id].remove(order_line)
        request_slots_by_order[order_id] += 1

    eligible_orders = {capacity: set() for capacity in range(1, 5)}
    order_buckets = {capacity: [] for capacity in range(1, 5)}

    def update_order_capacity(order_id: str) -> None:
        for order_ids in eligible_orders.values():
            order_ids.discard(order_id)
        if request_slots_by_order[order_id] < 3:
            available_count = len(free_lines_by_order[order_id])
            for capacity in range(1, available_count + 1):
                eligible_orders[capacity].add(order_id)
                order_buckets[capacity].append(order_id)

    for order_id in orders_by_id:
        update_order_capacity(order_id)

    request_indices = list(range(5, request_count))
    rng.shuffle(request_indices)
    request_indices.sort(key=lambda index: line_counts[index], reverse=True)
    for request_index in request_indices:
        required_lines = line_counts[request_index]
        if not eligible_orders[required_lines]:
            raise ValueError(
                f"Cannot allocate {required_lines} distinct lines to request {request_index + 1}."
            )
        bucket = order_buckets[required_lines]
        order_id = rng.choice(bucket)
        while order_id not in eligible_orders[required_lines]:
            order_id = rng.choice(bucket)
        chosen_orders[request_index] = orders_by_id[order_id]
        chosen_lines[request_index] = rng.sample(
            free_lines_by_order[order_id], required_lines
        )
        for order_line in chosen_lines[request_index]:
            free_lines_by_order[order_id].remove(order_line)
        request_slots_by_order[order_id] += 1
        update_order_capacity(order_id)

    chosen_lines[5][0]["category"] = "apparel"
    chosen_lines[6][0]["category"] = "apparel"

    for request_number in range(1, request_count + 1):
        request_id = _id("RMA", request_number)
        selected_lines = chosen_lines[request_number - 1]
        order = chosen_orders[request_number - 1]
        ordered_at = date.fromisoformat(order["order_date"])
        if request_number in {1, 2, 3, 5, 6, 7}:
            requested_at = _add_days(ordered_at, 2)
            reason = (
                "damaged"
                if request_number == 2
                else "exchange"
                if request_number == 7
                else "no_longer_needed"
            )
            override = False
        elif request_number == 4:
            requested_at = _add_days(ordered_at, 75)
            reason = "no_longer_needed"
            override = False
        else:
            requested_at = _add_days(ordered_at, rng.randrange(2, 61))
            reason = rng.choice(REASONS)
            override = rng.random() < 0.06
        requests.append(
            {
                "return_request_id": request_id,
                "order_id": order["order_id"],
                "requested_at": _date(requested_at),
                "reason": reason,
                "manager_override": int(override),
            }
        )
        events.append(_event(len(events) + 1, request_id, requested_at, "requested", reason))
        approved_total = 0
        for line_number, order_line in enumerate(selected_lines, 1):
            line_id = f"RMAL-{request_id}-{line_number:02d}"
            eligible = is_eligible(
                order_line["category"],
                ordered_at,
                requested_at,
                override,
            )
            requested_quantity = rng.randint(1, order_line["quantity"])
            if request_number == 1 and line_number == 1:
                requested_quantity = order_line["quantity"] + 1
                eligible = False
            approved_quantity = requested_quantity if eligible else 0
            approved_total += approved_quantity
            row = {
                "return_line_id": line_id,
                "return_request_id": request_id,
                "order_line_id": order_line["order_line_id"],
                "line_number": line_number,
                "requested_quantity": requested_quantity,
                "approved_quantity": approved_quantity,
            }
            return_lines.append(row)
            lines_by_id[line_id] = row
            line_to_request[line_id] = request_id
            ids_by_request.setdefault(request_id, []).append(line_id)
        auth_id = _id("AUTH", request_number)
        auth_status = (
            "rejected"
            if approved_total == 0
            else "partial"
            if any(line["approved_quantity"] == 0 for line in return_lines[-len(selected_lines) :])
            else "approved"
        )
        authorizations.append(
            {
                "authorization_id": auth_id,
                "return_request_id": request_id,
                "authorized_at": _date(_add_days(requested_at, 1)),
                "authorization_status": auth_status,
            }
        )
        events.append(
            _event(
                len(events) + 1,
                request_id,
                _add_days(requested_at, 1),
                "authorized",
                auth_status,
            )
        )
        if request_number == 1:
            ground_truth.append(
                _ground_truth(
                    1,
                    "return_line",
                    ids_by_request[request_id][0],
                    ANOMALY_TYPES[0],
                    "reject",
                    "Requested quantity deliberately exceeds purchased quantity.",
                )
            )
        if request_number == 4:
            ground_truth.append(
                _ground_truth(
                    4,
                    "return_request",
                    request_id,
                    ANOMALY_TYPES[3],
                    "reject_without_override",
                    "Request date is outside its category window and has no override.",
                )
            )

        if approved_total == 0 or (
            request_number not in {2, 3, 5, 6, 7} and rng.random() < 0.035
        ):
            continue
        shipment_id = _id("RTS", len(shipments) + 1)
        shipped_at = _add_days(requested_at, 2)
        missing = request_number == 6 or (
            request_number not in {2, 3, 5, 7} and rng.random() < 0.025
        )
        received_at = None if missing else _date(_add_days(shipped_at, rng.randrange(2, 8)))
        shipments.append(
            {
                "shipment_id": shipment_id,
                "authorization_id": auth_id,
                "shipped_at": _date(shipped_at),
                "received_at": received_at,
                "shipment_status": "missing" if missing else "received",
            }
        )
        events.append(
            _event(
                len(events) + 1,
                request_id,
                shipped_at,
                "shipment_created",
                shipment_id,
            )
        )
        if missing:
            continue
        received_date = date.fromisoformat(received_at or _date(shipped_at))
        events.append(
            _event(len(events) + 1, request_id, received_date, "shipment_received", shipment_id)
        )
        for line_id in ids_by_request[request_id]:
            return_line = lines_by_id[line_id]
            if return_line["approved_quantity"] == 0:
                continue
            condition = (
                "sellable"
                if request_number == 3
                else rng.choices(CONDITIONS, weights=(72, 17, 8, 3), k=1)[0]
            )
            inspection_id = _id("INSP", len(inspections) + 1)
            inspected_at = _add_days(received_date, 1)
            inspections.append(
                {
                    "inspection_id": inspection_id,
                    "shipment_id": shipment_id,
                    "return_line_id": line_id,
                    "inspected_at": _date(inspected_at),
                    "condition": condition,
                    "inspection_notes": f"Synthetic inspection: {condition}.",
                }
            )
            events.append(
                _event(len(events) + 1, request_id, inspected_at, "inspected", condition)
            )
            order_line = next(
                line for line in order_lines if line["order_line_id"] == return_line["order_line_id"]
            )
            refund_amount = calculate_refund_cents(
                order_line["paid_total_cents"],
                return_line["approved_quantity"],
                order_line["quantity"],
                requests[request_number - 1]["reason"],
            )
            if requests[request_number - 1]["reason"] == "exchange":
                refund_amount = 0
            refund_id = _id("REF", len(refunds) + 1)
            refunds.append(
                {
                    "refund_id": refund_id,
                    "return_line_id": line_id,
                    "issued_at": _date(_add_days(inspected_at, 1)),
                    "amount_cents": refund_amount,
                    "refund_status": "paid",
                    "payment_reference": f"PAY-{refund_id}",
                }
            )
            events.append(
                _event(
                    len(events) + 1,
                    request_id,
                    _add_days(inspected_at, 1),
                    "refund_paid",
                    refund_id,
                )
            )
            disposition = disposition_for(condition)
            disposition_id = _id("DISP", len(dispositions) + 1)
            early_restock_case = (
                request_number == 3 and line_id == ids_by_request[request_id][0]
            )
            decided_at = (
                _add_days(received_date, -1)
                if early_restock_case
                else _add_days(inspected_at, 1)
            )
            movement_status = "blocked_sequence" if early_restock_case else "completed"
            dispositions.append(
                {
                    "disposition_id": disposition_id,
                    "return_line_id": line_id,
                    "inspection_id": inspection_id,
                    "decided_at": _date(decided_at),
                    "disposition": disposition,
                    "movement_status": movement_status,
                }
            )
            events.append(
                _event(
                    len(events) + 1,
                    request_id,
                    decided_at,
                    "disposition_decided",
                    f"{disposition}:{movement_status}",
                )
            )
            if early_restock_case:
                ground_truth.append(
                    _ground_truth(
                        3,
                        "inventory_disposition",
                        disposition_id,
                        ANOMALY_TYPES[2],
                        "block_inventory_movement",
                        "Disposition timestamp precedes inspection; movement is blocked.",
                    )
                )
            if request_number == 2 and line_id == ids_by_request[request_id][0]:
                attempted_id = _id("REF", len(refunds) + 1)
                refunds.append(
                    {
                        "refund_id": attempted_id,
                        "return_line_id": line_id,
                        "issued_at": _date(_add_days(inspected_at, 2)),
                        "amount_cents": order_line["paid_total_cents"] + 100,
                        "refund_status": "blocked_policy",
                        "payment_reference": None,
                    }
                )
                ground_truth.append(
                    _ground_truth(
                        2,
                        "refund",
                        attempted_id,
                        ANOMALY_TYPES[1],
                        "block_refund",
                        "Attempted amount exceeds the source order-line paid amount.",
                    )
                )
                events.append(
                    _event(
                        len(events) + 1,
                        request_id,
                        _add_days(inspected_at, 2),
                        "refund_blocked",
                        attempted_id,
                    )
                )
            if request_number == 5 and line_id == ids_by_request[request_id][0]:
                duplicate_id = _id("REF", len(refunds) + 1)
                refunds.append(
                    {
                        "refund_id": duplicate_id,
                        "return_line_id": line_id,
                        "issued_at": _date(_add_days(inspected_at, 3)),
                        "amount_cents": refund_amount,
                        "refund_status": "blocked_duplicate",
                        "payment_reference": None,
                    }
                )
                ground_truth.append(
                    _ground_truth(
                        5,
                        "refund",
                        duplicate_id,
                        ANOMALY_TYPES[4],
                        "block_duplicate_payout",
                        "A second payout attempt is recorded but never paid.",
                    )
                )
                events.append(
                    _event(
                        len(events) + 1,
                        request_id,
                        _add_days(inspected_at, 3),
                        "refund_blocked",
                        duplicate_id,
                    )
                )

    events.sort(key=lambda event: (event["return_request_id"], event["event_at"], event["event_type"]))
    for event_number, event in enumerate(events, 1):
        event["event_id"] = _id("EVT", event_number)

    order_columns = (
        "order_id",
        "customer_id",
        "order_date",
        "currency",
        "order_total_cents",
        "order_status",
        "delivered_date",
    )
    export_columns = {
        "customers": ("customer_id", "created_date", "region", "customer_segment"),
        "orders": order_columns,
        "order_lines": (
            "order_line_id",
            "order_id",
            "line_number",
            "sku",
            "category",
            "quantity",
            "unit_price_cents",
            "discount_cents",
            "tax_cents",
            "paid_total_cents",
        ),
        "return_requests": (
            "return_request_id",
            "order_id",
            "requested_at",
            "reason",
            "manager_override",
        ),
        "return_lines": (
            "return_line_id",
            "return_request_id",
            "order_line_id",
            "line_number",
            "requested_quantity",
            "approved_quantity",
        ),
        "return_authorizations": (
            "authorization_id",
            "return_request_id",
            "authorized_at",
            "authorization_status",
        ),
        "return_shipments": (
            "shipment_id",
            "authorization_id",
            "shipped_at",
            "received_at",
            "shipment_status",
        ),
        "inspections": (
            "inspection_id",
            "shipment_id",
            "return_line_id",
            "inspected_at",
            "condition",
            "inspection_notes",
        ),
        "refunds": (
            "refund_id",
            "return_line_id",
            "issued_at",
            "amount_cents",
            "refund_status",
            "payment_reference",
        ),
        "inventory_dispositions": (
            "disposition_id",
            "return_line_id",
            "inspection_id",
            "decided_at",
            "disposition",
            "movement_status",
        ),
        "return_events": (
            "event_id",
            "return_request_id",
            "event_at",
            "event_type",
            "event_detail",
        ),
    }
    data = {
        "customers": customers,
        "orders": list(orders_by_id.values()),
        "order_lines": order_lines,
        "return_requests": requests,
        "return_lines": return_lines,
        "return_authorizations": authorizations,
        "return_shipments": shipments,
        "inspections": inspections,
        "refunds": refunds,
        "inventory_dispositions": dispositions,
        "return_events": events,
    }
    try:
        with connection:
            for table, rows in data.items():
                _insert_many(connection, table, export_columns[table], rows)
        _write_exports(output_dir, connection, data, export_columns, ground_truth, seed)
    finally:
        connection.close()
    from .validation import validate_dataset

    summary = validate_dataset(output_dir, write_summary=True)
    return summary


def _write_exports(
    output_dir: Path,
    connection: sqlite3.Connection,
    data: dict[str, list[dict[str, Any]]],
    columns: dict[str, tuple[str, ...]],
    ground_truth: list[dict[str, Any]],
    seed: int,
) -> None:
    ground_truth.sort(key=lambda row: row["issue_id"])
    for table, rows in data.items():
        path = output_dir / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns[table], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    truth_path = output_dir / "ground_truth.csv"
    with truth_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=TABLE_COLUMNS["ground_truth"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(ground_truth)
    (output_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "schema.sql").write_text(SCHEMA.strip() + "\n", encoding="utf-8")
    sql_dump = "\n".join(connection.iterdump()) + "\n"
    (output_dir / "seed.sql").write_text(sql_dump, encoding="utf-8")
    (output_dir / "source_manifest.json").write_text(
        json.dumps(
            {
                "data_origin": "Entirely synthetic; generated locally by this repository.",
                "external_sources": [],
                "license_or_terms": "No external dataset or third-party source data is used.",
                "personal_data": "No real people, contact details, or externally sourced identifiers.",
                "transformations": "Deterministic seeded generation; currency represented as integer cents.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    counts = {
        table: len(data[table])
        for table in data
    }
    counts["ground_truth"] = len(ground_truth)
    checksums = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name not in {"manifest.json", "validation_summary.json"}:
            checksums[path.name] = _sha256(path)
    manifest = {
        "generator": "returns-workflow",
        "version": __version__,
        "seed": seed,
        "row_counts": counts,
        "checksums_sha256": checksums,
        "generated_at": "deterministic synthetic data; timestamps are domain data, not wall-clock time",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def verify_reproducibility(
    *, order_count: int, request_count: int, return_line_count: int, seed: int
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="returns-repro-") as temp_dir:
        first = Path(temp_dir) / "first"
        second = Path(temp_dir) / "second"
        generate_dataset(
            first,
            order_count=order_count,
            request_count=request_count,
            return_line_count=return_line_count,
            seed=seed,
        )
        generate_dataset(
            second,
            order_count=order_count,
            request_count=request_count,
            return_line_count=return_line_count,
            seed=seed,
        )
        first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        second_manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
        first_sums = first_manifest["checksums_sha256"]
        second_sums = second_manifest["checksums_sha256"]
        if first_sums != second_sums:
            raise RuntimeError("Reproducibility check failed: generated file checksums differ.")
        return {
            "reproducible": True,
            "seed": seed,
            "files_compared": len(first_sums),
            "row_counts": first_manifest["row_counts"],
        }
