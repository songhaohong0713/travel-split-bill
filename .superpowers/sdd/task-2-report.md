# Task 2 implementation report

## Status

DONE

Implemented the pure Python Decimal settlement core only. No API, database, OCR,
share, or frontend code was changed.

## Files

- `backend/app/core/__init__.py`
- `backend/app/core/money.py`
- `backend/app/core/allocation.py`
- `backend/app/core/settlement.py`
- `backend/tests/unit/test_settlement.py`

## TDD evidence

Tests were written before any production core file existed.

RED command:

```text
uv run pytest backend/tests/unit/test_settlement.py -q
```

RED result (exit 1):

```text
E   ModuleNotFoundError: No module named ''app.core''
ERROR backend/tests/unit/test_settlement.py
1 error in 0.31s
```

First GREEN command:

```text
uv run pytest backend/tests/unit/test_settlement.py -q
```

First GREEN result (exit 0):

```text
..........                                                               [100%]
10 passed in 0.48s
```

After the lint/type refactor, the focused suite remained green:

```text
10 passed in 0.46s
```

## Requirement coverage

- Decimal-string `Money` boundary type with cached `Decimal` internal value;
  float input is not accepted.
- Currency scales: JPY=0, KRW=0, CNY/USD/EUR=2 with `ROUND_HALF_UP`.
- Exact shares and weight-derived allocation helpers; allocation conserves the
  original Decimal amount by assigning the final exact remainder explicitly.
- One payer and one original payment per expense.
- Actual paid amount determines the source-to-settlement mapping and overrides
  any stored reference rate.
- Personal coupons, shared discounts, generic negative refunds, and later tax
  refunds are represented as auditable adjustments.
- Later tax refunds derive participant shares from allocated item tax, not item
  price, and account for the actual refund recipient.
- Per-expense quantization is reconciled by an explicit `rounding` audit line;
  the rounding owner defaults to the payer.
- Net balances are matched deterministically by descending creditor/debtor
  magnitude with participant ID tie-breaking.
- The explicit JPY purchase fixture maps the actual CNY payment to a
  `168.42 CNY` friend-to-owner transfer despite a conflicting reference rate.
- Hypothesis covers shares summing exactly to one, allocation conservation, and
  quantized settlement nets summing to zero.

Every audit line includes expense ID, participant ID, amount, reason, and rate
source. Output participant and transfer ordering is deterministic.

## Fresh completion verification

```text
uv run pytest backend/tests/unit/test_settlement.py -q
10 passed in 0.47s

uv run pytest backend/tests/unit -q
10 passed in 0.41s

uv run pytest backend/tests -q
11 passed in 0.44s

uv run ruff check backend
All checks passed!

uv run mypy backend
Success: no issues found in 11 source files
```

All commands exited 0.

## Concerns

None.
