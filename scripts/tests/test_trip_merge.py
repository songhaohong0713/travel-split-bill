import importlib.util
import unittest
import json
import gzip
import subprocess
import sys
import tempfile
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "trip-merge.py"

class MergeTests(unittest.TestCase):
    def module(self):
        self.assertTrue(PATH.exists(), "offline merge tool is missing")
        spec = importlib.util.spec_from_file_location("trip_merge", PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def source(self):
        return {"tables": {"trips": [{"id": str(i), "name": n, "owner_id": "a", "default_currency": "JPY"} for i,n in enumerate(["20261001札幌", "1002札幌", "1003札幌"])], "trip_members": [{"id": f"{i}-{u}", "trip_id": str(i), "user_id": u} for i in range(3) for u in ["a", "b"]], "expenses": [{"id": f"e{i}", "trip_id": str(i), "occurred_at": f"2026-10-0{i+1}", "payload_json": {"splits": [1,2]}} for i in range(3)], "settlement_versions": [{"id":"v", "trip_id":"1", "result_json":{"paid":True}}], "payment_records": [{"id":"p", "trip_id":"1", "revoked_at":"2026-10-04"}], "payment_allocations":[{"payment_id":"p", "occurred_at":"2026-10-02", "amount":"1"}], "trip_ledgers": []}}

    def test_merge_preserves_payloads_and_audit(self):
        m = self.module(); source = self.source(); plan = m.preview(source)
        self.assertEqual(len(plan["groups"]), 1)
        result = m.apply(source, plan, plan["digest"])
        self.assertEqual(len(result["tables"]["trips"]), 1)
        self.assertEqual(len(result["tables"]["trip_members"]), 2)
        self.assertEqual(result["original"], source)
        self.assertEqual(result["tables"]["expenses"][1]["payload_json"], {"splits":[1,2]})
        self.assertEqual(result["tables"]["payment_records"][0]["revoked_at"], "2026-10-04")
        self.assertEqual(result["tables"]["payment_allocations"], source["tables"]["payment_allocations"])
        self.assertEqual(result["tables"]["trip_ledgers"][0]["state_json"]["history_confirmation"]["status"], "pending")

    def test_gaps_currency_members_unknown_dates(self):
        m = self.module()
        for change in [lambda s: s["tables"]["expenses"][1].update(occurred_at="2026-10-09"), lambda s: s["tables"]["trips"][1].update(default_currency="USD"), lambda s: s["tables"]["trip_members"][3].update(user_id="c"), lambda s: s["tables"]["expenses"][1].update(occurred_at=None)]:
            source=self.source(); change(source)
            self.assertEqual(m.preview(source)["groups"], [])

    def test_changed_source_or_plan_rejected(self):
        m=self.module(); source=self.source(); plan=m.preview(source)
        with self.assertRaises(ValueError): m.apply(source,plan,"wrong")
        source["tables"]["expenses"][0]["payload_json"]={}
        with self.assertRaises(ValueError): m.apply(source,plan,plan["digest"])

    def test_conflicting_ledger_is_blocked(self):
        m=self.module(); source=self.source()
        source["tables"]["trip_ledgers"]=[{"trip_id":str(i),"state_json":{"frozen_days":{"same":i},"payments":[],"closed_dates":[]}} for i in range(2)]
        self.assertEqual(m.preview(source)["groups"], [])
        self.assertTrue(m.preview(source)["blocked"])

    def test_plan_tampering_rejected(self):
        m=self.module(); source=self.source(); plan=m.preview(source)
        token=plan["digest"]
        plan["groups"][0]["target"]="1"
        with self.assertRaises(ValueError): m.apply(source,plan,token)

    def test_cli_preview_confirmed_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"source.gz"; plan=Path(folder)/"plan.json"; output=Path(folder)/"result.json"
            raw=gzip.compress(json.dumps({"format":"travel-split-preupgrade-v1",**self.source()}).encode())
            source.write_bytes(raw)
            command=[sys.executable,str(PATH)]
            first=subprocess.run(command+["preview","--source",str(source),"--output",str(plan)],capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertNotIn("札幌",first.stdout)
            token=json.loads(plan.read_text(encoding="utf8"))["digest"]
            args=["apply","--source",str(source),"--plan",str(plan),"--confirm",token,"--output",str(output)]
            self.assertEqual(subprocess.run(command+args,capture_output=True).returncode,0)
            self.assertFalse(json.loads(output.read_text(encoding="utf8"))["import_ready"])
            self.assertNotEqual(subprocess.run(command+args,capture_output=True).returncode,0)
            self.assertEqual(source.read_bytes(),raw)

    def test_sql_requires_current_complete_snapshot_and_matching_payment_history(self):
        m=self.module(); source=self.source(); plan=m.preview(source)
        with self.assertRaises(ValueError): m.prepare_sql(source,plan,plan['digest'])
        source['tables']['payment_records']=[]
        source['tables']['payment_allocations']=[]
        for table in m.SQL_TABLES:
            source['tables'].setdefault(table,[])
        plan=m.preview(source)
        sql=m.prepare_sql(source,plan,plan['digest'])
        self.assertIn('BEGIN;',sql)
        self.assertIn('LOCK TABLE',sql)
        self.assertIn('SOURCE_CHANGED',sql)
        self.assertIn('trip_merge_archives',sql)
        self.assertIn('COMMIT;',sql)
        source['tables']['trip_invites']=[{'id':'invite','trip_id':'1'}]
        plan=m.preview(source)
        with self.assertRaises(ValueError): m.prepare_sql(source,plan,plan['digest'])

if __name__ == "__main__": unittest.main()
