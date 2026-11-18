import gzip
import json

import pytest
from app.ops.business_backup import TABLES, pack_backup, restore_sql, unpack_backup


def data():
    return {
        "schema_version": "20261022_07",
        "tables": {t: [] for t in TABLES},
        "schema": {t: [] for t in TABLES},
    }


def test_backup_roundtrip_and_empty_target_restore_guard():
    payload = data()
    assert unpack_backup(pack_backup(payload)) == payload
    sql = restore_sql(pack_backup(payload))
    assert "TARGET_NOT_EMPTY" in sql and "SCHEMA_MISMATCH" in sql
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;")


def test_backup_tampering_is_rejected():
    envelope = json.loads(gzip.decompress(pack_backup(data())))
    envelope["payload"]["tables"]["users"].append({"id": "tampered"})
    with pytest.raises(ValueError, match="校验失败"):
        unpack_backup(gzip.compress(json.dumps(envelope).encode()))
