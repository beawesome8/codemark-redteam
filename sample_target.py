"""
Business rule validation and routing module.

Runs on the output of Phase 3 (extract.py). Three layers, applied in order:

1. Type validation - dates parse, totals are numeric, required fields exist.
   This is already partially enforced by Pydantic in extract.py, but this
   module re-checks at the business-logic level (e.g. a date string that
   parses as a string but isn't a valid calendar date).

2. Business rules - line items must sum to the stated subtotal, subtotal
   plus tax must equal the total, due date must not precede invoice date.
   These catch logical inconsistency that type validation cannot.

3. Routing - decides auto-approve vs. human review, based on:
   - whether Phase 2 flagged this page for vision fallback (needs_vision_fallback)
   - whether Phase 3's extraction_notes is non-empty (model self-reported uncertainty)
   - whether any business rule failed

Note on confidence: this system does not have a numeric per-field confidence
score from the extraction model. Building a synthetic one (e.g. "1.0 if not
null") would be a fabricated signal, not a real one. Routing instead uses two
honest, already-computed signals: the OCR fallback flag and the model's own
uncertainty notes. This is a deliberate simplification, documented rather
than hidden.
"""

import json
import sys
from datetime import date
from dataclasses import dataclass, field


TOLERANCE = 0.01  # EUR; float rounding tolerance for sum comparisons


@dataclass
class ValidationIssue:
    rule: str
    severity: str  # "error" or "warning"
    detail: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)
    decision: str = "auto_approved"  # "auto_approved" | "needs_review"
    decision_reasons: list[str] = field(default_factory=list)

    def add_issue(self, rule: str, severity: str, detail: str):
        self.issues.append(ValidationIssue(rule=rule, severity=severity, detail=detail))


def validate_dates(extraction: dict, result: ValidationResult) -> None:
    """Confirm invoice_date and due_date parse as real calendar dates, and due_date is not before invoice_date."""
    try:
        inv_date = date.fromisoformat(extraction["invoice_date"])
    except (ValueError, TypeError):
        result.add_issue("date_format", "error", f"invoice_date '{extraction.get('invoice_date')}' is not a valid ISO date")
        return

    due_date_raw = extraction.get("due_date")
    if due_date_raw:
        try:
            due_date = date.fromisoformat(due_date_raw)
        except ValueError:
            result.add_issue("date_format", "error", f"due_date '{due_date_raw}' is not a valid ISO date")
            return

        if due_date < inv_date:
            result.add_issue("date_logic", "error", f"due_date {due_date} precedes invoice_date {inv_date}")


def validate_line_items_sum(extraction: dict, result: ValidationResult) -> None:
    """Confirm the sum of line item totals matches the stated subtotal, within float tolerance."""
    line_items = extraction.get("line_items", [])
    stated_subtotal = extraction.get("subtotal")

    if stated_subtotal is None:
        result.add_issue("subtotal_missing", "warning", "No subtotal stated; cannot cross-check against line items")
        return

    computed_sum = sum(
        item["line_total"] for item in line_items if item.get("line_total") is not None
    )
    missing_totals = [item["description"] for item in line_items if item.get("line_total") is None]

    if missing_totals:
        result.add_issue(
            "line_item_incomplete", "warning",
            f"{len(missing_totals)} line item(s) missing line_total, sum check is partial: {missing_totals}"
        )

    if abs(computed_sum - stated_subtotal) > TOLERANCE:
        result.add_issue(
            "subtotal_mismatch", "error",
            f"Sum of line totals ({computed_sum:.2f}) does not match stated subtotal ({stated_subtotal:.2f})"
        )


def validate_total(extraction: dict, result: ValidationResult) -> None:
    """Confirm subtotal + tax_amount equals total_amount, within float tolerance."""
    subtotal = extraction.get("subtotal")
    tax = extraction.get("tax_amount")
    total = extraction.get("total_amount")

    if subtotal is None or tax is None or total is None:
        result.add_issue("total_check_incomplete", "warning", "Cannot verify total: subtotal, tax, or total is missing")
        return

    expected_total = subtotal + tax
    if abs(expected_total - total) > TOLERANCE:
        result.add_issue(
            "total_mismatch", "error",
            f"subtotal + tax ({expected_total:.2f}) does not equal stated total ({total:.2f})"
        )


def route(extraction: dict, ocr_diagnostic: dict, result: ValidationResult) -> None:
    """
    Decide auto_approved vs needs_review based on: any validation error,
    Phase 2's vision-fallback flag, or non-empty extraction_notes.
    """
    has_error = any(issue.severity == "error" for issue in result.issues)
    needed_fallback = ocr_diagnostic.get("needs_vision_fallback", False)
    has_notes = bool(extraction.get("extraction_notes"))

    if has_error:
        result.decision = "needs_review"
        result.decision_reasons.append("One or more business rule validation errors")

    if needed_fallback:
        result.decision = "needs_review"
        result.decision_reasons.append("Page required vision fallback during OCR (Phase 2)")

    if has_notes:
        result.decision = "needs_review"
        result.decision_reasons.append(f"Model reported extraction uncertainty: {extraction['extraction_notes']}")

    if result.decision == "auto_approved":
        result.decision_reasons.append("No validation errors, no OCR fallback, no extraction uncertainty")


def validate_extraction(extraction_record: dict) -> ValidationResult:
    """Run all validation layers and produce a final routing decision."""
    extraction = extraction_record["extraction"]
    ocr_diagnostic = extraction_record["ocr_diagnostic"]

    result = ValidationResult()
    validate_dates(extraction, result)
    validate_line_items_sum(extraction, result)
    validate_total(extraction, result)
    route(extraction, ocr_diagnostic, result)

    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python src/validate.py <path_to_extraction_json>")
        print("(Pipe extract.py output to a file first, or see full_pipeline.py)")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        extraction_record = json.load(f)

    result = validate_extraction(extraction_record)

    output = {
        "decision": result.decision,
        "decision_reasons": result.decision_reasons,
        "issues": [
            {"rule": i.rule, "severity": i.severity, "detail": i.detail}
            for i in result.issues
        ],
    }
    print(json.dumps(output, indent=2))
