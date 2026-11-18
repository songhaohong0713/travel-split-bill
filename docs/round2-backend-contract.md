# Round two backend contract

Existing ledger and payment endpoints remain unchanged. Dates before the client's current calendar date (bounded X-Client-Timezone-Offset header, legacy clients fall back to UTC) automatically become eligible for settlement. Today and future dates remain `recording` until explicitly closed, including dates receiving an adjustment. A carry-only date can be synthetic and have `count: 0`.

Each ledger day adds:
- `original_totals`: original expense spending by currency (`amount`) and signed member net (`signed_amount`); excludes adjustments and payments.
- `carry_entries`: separately labelled `kind: adjustment`, `source_date`, `currency`, absolute `amount`, and signed `signed_amount`.
- `settled_transfers`: actual active payment records allocated to this day, including creator/time and immutable original amount.

`groups` continues to describe current original expenses. `pending` is the authoritative remaining obligation including carry adjustments. A fully paid source date stays settled after edits, additions, deletion, or currency changes. Adjustments go to the next un-settled date by currency, otherwise today; if today is already frozen, the next open calendar date is used. A recording target receiving a carry remains recording until explicitly closed; the close endpoint also accepts a synthetic date containing only adjustments.

Payment records add `freeze_basis` (raw signed expense balances per source date and currency) and `depends_on` (active prerequisite payment IDs). `basis_expenses` preserves the complete expense evidence at confirmation. Original payment amount/direction/allocations remain immutable; revoke only adds audit metadata. Revoke invalidates dependent freeze assumptions without deleting later actual payments. Legacy payments freeze only when immutable expense evidence proves the allocated original balance was fully covered. A calculation snapshot alone never establishes payment.

Trip summaries add `day_count` (distinct actual expense dates), `latest_expense_date` (nullable), and `has_recording`. Unknown legacy history remains visible in daily and trip pending amounts. Its `history_status: pending` no longer blocks preview or explicitly marking an actual payment; historical owner confirmation endpoints remain available.

Preview tokens still hash the full snapshot, rejecting concurrent edits. Currencies remain independent; dates within a trip offset, separate trips never offset.
