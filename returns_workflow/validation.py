"""Integrity, policy, arithmetic, and lifecycle checks for generated datasets."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .database import TABLES, connect
from .generator import ANOMALY_TYPES
from .policy import calculate_refund_cents, is_eligible


def validate_dataset(output_dir: Path, *, write_summary: bool = True) -> dict[str, Any]:
    db_path = output_dir / "returns.sqlite3"
    if not db_path.exists():
        raise FileNotFoundError(f"Dataset database not found: {db_path}")
    connection = connect(db_path)
    checks: list[dict[str, Any]] = []
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        checks.append(
            _check("foreign_keys", not fk_errors, f"{len(fk_errors)} foreign-key violations")
        )

        order_math_errors = connection.execute(
            """
            SELECT COUNT(*) FROM order_lines
            WHERE paid_total_cents != quantity * unit_price_cents - discount_cents + tax_cents
            """
        ).fetchone()[0]
        checks.append(
            _check("order_line_arithmetic", order_math_errors == 0, f"{order_math_errors} mismatches")
        )
        order_total_errors = connection.execute(
            """
            SELECT COUNT(*) FROM orders o
            WHERE order_total_cents != (
                SELECT COALESCE(SUM(ol.paid_total_cents), 0)
                FROM order_lines ol WHERE ol.order_id = o.order_id
            )
            """
        ).fetchone()[0]
        checks.append(
            _check("order_totals", order_total_errors == 0, f"{order_total_errors} mismatches")
        )

        relationship_errors = connection.execute(
            """
            SELECT COUNT(*) FROM return_lines rl
            JOIN return_requests rr USING (return_request_id)
            JOIN orders o USING (order_id)
            JOIN order_lines ol USING (order_line_id)
            WHERE rr.order_id != ol.order_id
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "return_line_parent_order",
                relationship_errors == 0,
                f"{relationship_errors} lines reference a different order",
            )
        )

        quantity_issues = connection.execute(
            """
            SELECT COUNT(*) FROM return_lines rl
            JOIN order_lines ol USING (order_line_id)
            WHERE rl.requested_quantity > ol.quantity
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "quantity_over_purchase_labelled",
                quantity_issues == 1,
                f"{quantity_issues} labelled quantity exception(s)",
            )
        )
        cumulative_quantity_issues = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT ol.order_line_id, ol.quantity, SUM(rl.requested_quantity) AS requested
                FROM order_lines ol JOIN return_lines rl USING (order_line_id)
                GROUP BY ol.order_line_id, ol.quantity
                HAVING requested > quantity
            )
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "cumulative_return_quantity",
                cumulative_quantity_issues == 1,
                f"{cumulative_quantity_issues} purchased line(s) exceed cumulative quantity",
            )
        )

        auth_errors = connection.execute(
            """
            SELECT COUNT(*) FROM return_authorizations a
            JOIN return_requests rr USING (return_request_id)
            JOIN return_lines rl USING (return_request_id)
            JOIN order_lines ol USING (order_line_id)
            JOIN orders o USING (order_id)
            GROUP BY a.return_request_id
            HAVING (
                SUM(CASE WHEN rl.approved_quantity > 0 THEN 1 ELSE 0 END) = 0
                AND a.authorization_status != 'rejected'
            ) OR (
                SUM(CASE WHEN rl.approved_quantity > 0 THEN 1 ELSE 0 END) > 0
                AND SUM(CASE WHEN rl.approved_quantity = 0 THEN 1 ELSE 0 END) > 0
                AND a.authorization_status != 'partial'
            ) OR (
                SUM(CASE WHEN rl.approved_quantity > 0 THEN 1 ELSE 0 END) > 0
                AND SUM(CASE WHEN rl.approved_quantity = 0 THEN 1 ELSE 0 END) = 0
                AND a.authorization_status != 'approved'
            )
            """
        ).fetchall()
        checks.append(
            _check("authorization_statuses", not auth_errors, f"{len(auth_errors)} mismatches")
        )
        eligibility_errors = 0
        for row in connection.execute(
            """
            SELECT rl.approved_quantity, rl.requested_quantity, ol.category, o.order_date, rr.requested_at,
                   rr.manager_override, ol.quantity
            FROM return_lines rl
            JOIN return_requests rr USING (return_request_id)
            JOIN orders o USING (order_id)
            JOIN order_lines ol USING (order_line_id)
            """
        ):
            expected = is_eligible(
                row["category"],
                date.fromisoformat(row["order_date"]),
                date.fromisoformat(row["requested_at"]),
                bool(row["manager_override"]),
            )
            eligible_quantity = (
                row["requested_quantity"]
                if expected and row["requested_quantity"] <= row["quantity"]
                else 0
            )
            if row["approved_quantity"] != eligible_quantity or (
                row["approved_quantity"] > row["quantity"]
            ):
                eligibility_errors += 1
        checks.append(
            _check("return_eligibility", eligibility_errors == 0, f"{eligibility_errors} mismatches")
        )

        refund_rows = connection.execute(
            """
            SELECT r.amount_cents, r.refund_status, rl.approved_quantity,
                   rr.reason, ol.paid_total_cents, ol.quantity
            FROM refunds r
            JOIN return_lines rl USING (return_line_id)
            JOIN return_requests rr USING (return_request_id)
            JOIN order_lines ol USING (order_line_id)
            """
        ).fetchall()
        paid_refund_errors = 0
        for row in refund_rows:
            if row["refund_status"] == "paid":
                expected = calculate_refund_cents(
                    row["paid_total_cents"],
                    row["approved_quantity"],
                    row["quantity"],
                    row["reason"],
                )
                if row["reason"] == "exchange":
                    expected = 0
                if row["amount_cents"] != expected:
                    paid_refund_errors += 1
        paid_excess = connection.execute(
            """
            SELECT COUNT(*) FROM refunds r
            JOIN return_lines rl USING (return_line_id)
            JOIN order_lines ol USING (order_line_id)
            WHERE r.refund_status = 'paid'
              AND r.amount_cents > ol.paid_total_cents
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "refund_reconciliation",
                paid_refund_errors == 0 and paid_excess == 0,
                f"{paid_refund_errors} arithmetic mismatches; {paid_excess} overpayments",
            )
        )
        date_order_errors = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT o.order_id AS record_id
                FROM orders o
                WHERE o.delivered_date IS NOT NULL AND o.delivered_date < o.order_date
                UNION ALL
                SELECT rr.return_request_id
                FROM return_requests rr JOIN orders o USING (order_id)
                WHERE rr.requested_at < o.order_date
                UNION ALL
                SELECT a.authorization_id
                FROM return_authorizations a JOIN return_requests rr USING (return_request_id)
                WHERE a.authorized_at < rr.requested_at
                UNION ALL
                SELECT s.shipment_id
                FROM return_shipments s JOIN return_authorizations a USING (authorization_id)
                WHERE s.shipped_at < a.authorized_at
                   OR (s.received_at IS NOT NULL AND s.received_at < s.shipped_at)
                   OR (s.shipment_status = 'received' AND s.received_at IS NULL)
                   OR (s.shipment_status = 'missing' AND s.received_at IS NOT NULL)
                UNION ALL
                SELECT i.inspection_id
                FROM inspections i JOIN return_shipments s USING (shipment_id)
                WHERE s.shipment_status != 'received'
                   OR s.received_at IS NULL
                   OR i.inspected_at < s.received_at
                UNION ALL
                SELECT r.refund_id
                FROM refunds r JOIN inspections i USING (return_line_id)
                WHERE r.refund_status = 'paid' AND r.issued_at < i.inspected_at
            )
            """
        ).fetchone()[0]
        authorization_count_errors = connection.execute(
            """
            SELECT ABS(
                (SELECT COUNT(*) FROM return_requests) -
                (SELECT COUNT(*) FROM return_authorizations)
            )
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "date_ordering_and_authorization_coverage",
                date_order_errors == 0 and authorization_count_errors == 0,
                f"{date_order_errors} date/lifecycle errors; "
                f"{authorization_count_errors} missing authorizations",
            )
        )

        early_movement = connection.execute(
            """
            SELECT COUNT(*) FROM inventory_dispositions d
            JOIN inspections i USING (inspection_id)
            WHERE d.movement_status = 'completed' AND d.decided_at < i.inspected_at
            """
        ).fetchone()[0]
        blocked_early = connection.execute(
            """
            SELECT COUNT(*) FROM inventory_dispositions d
            JOIN inspections i USING (inspection_id)
            WHERE d.movement_status = 'blocked_sequence' AND d.decided_at < i.inspected_at
            """
        ).fetchone()[0]
        mapping_errors = connection.execute(
            """
            SELECT COUNT(*) FROM inventory_dispositions d
            JOIN inspections i USING (inspection_id)
            WHERE d.disposition != CASE i.condition
                WHEN 'sellable' THEN 'restock'
                WHEN 'minor_damage' THEN 'refurbish'
                WHEN 'missing_parts' THEN 'quarantine'
                WHEN 'unsafe' THEN 'scrap'
            END
            """
        ).fetchone()[0]
        disposition_relationship_errors = connection.execute(
            """
            SELECT COUNT(*) FROM inventory_dispositions d
            JOIN inspections i USING (inspection_id)
            WHERE d.return_line_id != i.return_line_id
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "disposition_controls",
                early_movement == 0
                and blocked_early == 1
                and mapping_errors == 0
                and disposition_relationship_errors == 0,
                f"{early_movement} unblocked early movements; {blocked_early} blocked; "
                f"{mapping_errors} condition mismatches; "
                f"{disposition_relationship_errors} inspection-line mismatches",
            )
        )

        duplicate_paid = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT return_line_id FROM refunds WHERE refund_status = 'paid'
                GROUP BY return_line_id HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        blocked_duplicates = connection.execute(
            "SELECT COUNT(*) FROM refunds WHERE refund_status = 'blocked_duplicate'"
        ).fetchone()[0]
        checks.append(
            _check(
                "duplicate_payout_blocked",
                duplicate_paid == 0 and blocked_duplicates == 1,
                f"{duplicate_paid} duplicate paid line(s); {blocked_duplicates} blocked attempts",
            )
        )
        event_errors = connection.execute(
            """
            SELECT COUNT(*) FROM return_events e
            JOIN return_requests rr USING (return_request_id)
            JOIN orders o USING (order_id)
            WHERE e.event_type = 'requested' AND e.event_at != rr.requested_at
               OR e.event_at < o.order_date
            """
        ).fetchone()[0]
        event_order_errors = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT return_request_id, event_at,
                       LAG(event_at) OVER (
                           PARTITION BY return_request_id ORDER BY event_id
                       ) AS previous_at
                FROM return_events
            ) WHERE previous_at IS NOT NULL AND event_at < previous_at
            """
        ).fetchone()[0]
        checks.append(
            _check(
                "event_history",
                event_errors == 0 and event_order_errors == 0,
                f"{event_errors} invalid event date(s); {event_order_errors} unordered event(s)",
            )
        )

        ground_truth_path = output_dir / "ground_truth.csv"
        with ground_truth_path.open(newline="", encoding="utf-8") as stream:
            truth = list(csv.DictReader(stream))
        type_counts = Counter(row["issue_type"] for row in truth)
        coverage = {issue_type: type_counts[issue_type] for issue_type in ANOMALY_TYPES}
        checks.append(
            _check(
                "ground_truth_coverage",
                len(truth) == len(ANOMALY_TYPES)
                and all(coverage[issue_type] == 1 for issue_type in ANOMALY_TYPES),
                f"{len(truth)} labels; one per required scenario: {all(v == 1 for v in coverage.values())}",
            )
        )
        missing_labels = []
        entity_tables = {
            "return_line": ("return_lines", "return_line_id"),
            "return_request": ("return_requests", "return_request_id"),
            "refund": ("refunds", "refund_id"),
            "inventory_disposition": ("inventory_dispositions", "disposition_id"),
        }
        for label in truth:
            table, key = entity_tables[label["entity_type"]]
            found = connection.execute(
                f"SELECT 1 FROM {table} WHERE {key} = ?", (label["entity_id"],)
            ).fetchone()
            if found is None:
                missing_labels.append(label["issue_id"])
        checks.append(
            _check("ground_truth_references", not missing_labels, f"missing labels: {missing_labels}")
        )
        semantics_errors = _ground_truth_semantics(connection, truth)
        checks.append(
            _check(
                "ground_truth_semantics",
                not semantics_errors,
                f"incorrect anomaly labels or outcomes: {semantics_errors}",
            )
        )
        metrics = _metrics(connection, counts, truth)
    finally:
        connection.close()

    failed = [check for check in checks if not check["passed"]]
    summary = {
        "status": "passed" if not failed else "failed",
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "checks": checks,
        "row_counts": counts,
        "metrics": metrics,
        "ground_truth_coverage": coverage,
    }
    if write_summary:
        (output_dir / "validation_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return summary


def _ground_truth_semantics(
    connection: sqlite3.Connection, truth: list[dict[str, str]]
) -> list[str]:
    errors = []
    labels = {row["issue_type"]: row for row in truth}
    for issue_type in ANOMALY_TYPES:
        if issue_type not in labels:
            errors.append(f"{issue_type}: missing label")
    if len(labels) != len(truth):
        errors.append("duplicate issue_type label")
    if errors:
        return errors

    quantity = labels["quantity_exceeds_purchase"]
    row = connection.execute(
        """
        SELECT rl.requested_quantity, ol.quantity, rl.approved_quantity
        FROM return_lines rl JOIN order_lines ol USING (order_line_id)
        WHERE rl.return_line_id = ?
        """,
        (quantity["entity_id"],),
    ).fetchone()
    if row is None or row["requested_quantity"] <= row["quantity"] or row["approved_quantity"] != 0:
        errors.append("quantity exception is not rejected")

    excess = labels["refund_exceeds_paid_amount"]
    row = connection.execute(
        """
        SELECT r.amount_cents, r.refund_status, ol.paid_total_cents
        FROM refunds r
        JOIN return_lines rl USING (return_line_id)
        JOIN order_lines ol USING (order_line_id)
        WHERE r.refund_id = ?
        """,
        (excess["entity_id"],),
    ).fetchone()
    if row is None or row["amount_cents"] <= row["paid_total_cents"] or row["refund_status"] != "blocked_policy":
        errors.append("excess refund is not blocked")

    early = labels["restock_before_inspection"]
    row = connection.execute(
        """
        SELECT d.decided_at, d.disposition, d.movement_status, i.inspected_at
        FROM inventory_dispositions d JOIN inspections i USING (inspection_id)
        WHERE d.disposition_id = ?
        """,
        (early["entity_id"],),
    ).fetchone()
    if (
        row is None
        or row["disposition"] != "restock"
        or row["decided_at"] >= row["inspected_at"]
        or row["movement_status"] != "blocked_sequence"
    ):
        errors.append("early restock is not blocked before inspection")

    late = labels["late_return_without_override"]
    row = connection.execute(
        """
        SELECT rr.manager_override, a.authorization_status
        FROM return_requests rr
        JOIN return_authorizations a USING (return_request_id)
        WHERE rr.return_request_id = ?
        """,
        (late["entity_id"],),
    ).fetchone()
    late_lines = connection.execute(
        """
        SELECT ol.category, o.order_date, rr.requested_at, rr.manager_override,
               rl.approved_quantity
        FROM return_requests rr
        JOIN orders o USING (order_id)
        JOIN return_lines rl USING (return_request_id)
        JOIN order_lines ol USING (order_line_id)
        WHERE rr.return_request_id = ?
        """,
        (late["entity_id"],),
    ).fetchall()
    if (
        row is None
        or row["manager_override"] != 0
        or row["authorization_status"] != "rejected"
        or not late_lines
        or any(
            line["approved_quantity"] != 0
            or is_eligible(
                line["category"],
                date.fromisoformat(line["order_date"]),
                date.fromisoformat(line["requested_at"]),
                bool(line["manager_override"]),
            )
            for line in late_lines
        )
    ):
        errors.append("late return without override is not rejected")

    duplicate = labels["duplicate_refund_attempt"]
    row = connection.execute(
        """
        SELECT r.return_line_id, r.refund_status,
               (SELECT COUNT(*) FROM refunds paid
                WHERE paid.return_line_id = r.return_line_id
                  AND paid.refund_status = 'paid') AS paid_count
        FROM refunds r WHERE r.refund_id = ?
        """,
        (duplicate["entity_id"],),
    ).fetchone()
    if row is None or row["refund_status"] != "blocked_duplicate" or row["paid_count"] != 1:
        errors.append("duplicate refund attempt is not blocked")
    return errors


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _metrics(
    connection: sqlite3.Connection, counts: dict[str, int], truth: list[dict[str, str]]
) -> dict[str, Any]:
    eligible = connection.execute(
        "SELECT COUNT(*) FROM return_lines WHERE approved_quantity > 0"
    ).fetchone()[0]
    partial = connection.execute(
        "SELECT COUNT(*) FROM return_authorizations WHERE authorization_status = 'partial'"
    ).fetchone()[0]
    shipment_counts = {
        row["shipment_status"]: row["count"]
        for row in connection.execute(
            "SELECT shipment_status, COUNT(*) AS count FROM return_shipments GROUP BY shipment_status"
        )
    }
    disposition_counts = {
        row["disposition"]: row["count"]
        for row in connection.execute(
            """
            SELECT disposition, COUNT(*) AS count FROM inventory_dispositions
            WHERE movement_status = 'completed' GROUP BY disposition
            """
        )
    }
    paid_refunds = connection.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE refund_status = 'paid'"
    ).fetchone()[0]
    request_reasons = {
        row["reason"]: row["count"]
        for row in connection.execute(
            "SELECT reason, COUNT(*) AS count FROM return_requests GROUP BY reason"
        )
    }
    inspection_conditions = {
        row["condition"]: row["count"]
        for row in connection.execute(
            "SELECT condition, COUNT(*) AS count FROM inspections GROUP BY condition"
        )
    }
    categories = {
        row["category"]: row["count"]
        for row in connection.execute(
            "SELECT category, COUNT(*) AS count FROM order_lines GROUP BY category"
        )
    }
    return {
        "approval_rate_by_line": round(eligible / counts["return_lines"], 4)
        if counts["return_lines"]
        else 0,
        "partial_authorization_count": partial,
        "shipment_outcomes": shipment_counts,
        "completed_disposition_counts": disposition_counts,
        "paid_refund_total_cents": paid_refunds,
        "blocked_refund_attempts": connection.execute(
            "SELECT COUNT(*) FROM refunds WHERE refund_status != 'paid'"
        ).fetchone()[0],
        "labelled_anomaly_count": len(truth),
        "request_reason_counts": request_reasons,
        "inspection_condition_counts": inspection_conditions,
        "order_line_category_counts": categories,
        "inspection_coverage": round(counts["inspections"] / counts["return_lines"], 4)
        if counts["return_lines"]
        else 0,
    }
