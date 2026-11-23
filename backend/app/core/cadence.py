"""When a trip's past days may be settled.

`daily`: every day opens the day after it. `trip`: nothing opens until the
day after the trip's end date. The ledger reads the choice from two snapshot
keys that the SQL tsb_ledger_snapshot and both Python snapshot builders emit
with the same names and values.
"""

CADENCE_DAILY, CADENCE_TRIP = "daily", "trip"
SNAPSHOT_CADENCE, SNAPSHOT_END_DATE = "settlement_cadence", "settlement_end_date"
