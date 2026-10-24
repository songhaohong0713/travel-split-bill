BEGIN;
CREATE INDEX ix_expenses_trip_date_id ON public.expenses(trip_id,occurred_at,id);
CREATE INDEX ix_expenses_trip_id_id ON public.expenses(trip_id,id);
CREATE TABLE public.payment_records (
 id varchar(36) PRIMARY KEY,
 trip_id varchar(36) NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
 currency varchar(3) NOT NULL CHECK(currency IN ('CNY','JPY','KRW','USD','EUR')),
 amount numeric(20,2) NOT NULL CHECK(amount>=0),
 from_id varchar(36) NOT NULL REFERENCES users(id),
 to_id varchar(36) NOT NULL REFERENCES users(id),
 created_by varchar(36) NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL,
 revoked_at timestamptz,
 payload_json jsonb NOT NULL,
 CONSTRAINT ck_payment_distinct_people CHECK(from_id<>to_id)
);
CREATE INDEX ix_payment_records_trip_created ON public.payment_records(trip_id,created_at,id);
CREATE TABLE public.payment_allocations (
 payment_id varchar(36) NOT NULL REFERENCES payment_records(id) ON DELETE CASCADE,
 occurred_at date NOT NULL,
 amount numeric(20,2) NOT NULL,
 PRIMARY KEY(payment_id,occurred_at)
);
ALTER TABLE public.payment_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_allocations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.payment_records,public.payment_allocations FROM PUBLIC,anon,authenticated;
GRANT ALL ON public.payment_records,public.payment_allocations TO service_role;

CREATE FUNCTION public.tsb_project_ledger_payments() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE p jsonb; a jsonb; total numeric;
BEGIN
 IF EXISTS(SELECT 1 FROM payment_records r WHERE r.trip_id=NEW.trip_id AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.state_json->'payments') x WHERE x->>'id'=r.id)) THEN RAISE EXCEPTION 'TSB_PAYMENT_HISTORY_REMOVAL'; END IF;
 FOR p IN SELECT value FROM jsonb_array_elements(NEW.state_json->'payments') LOOP
   IF EXISTS(SELECT 1 FROM payment_records WHERE id=p->>'id' AND (payload_json-'revoked_at'-'revoked_by') IS DISTINCT FROM (p-'revoked_at'-'revoked_by')) THEN RAISE EXCEPTION 'TSB_IMMUTABLE_PAYMENT'; END IF;
   SELECT coalesce(sum((value->>'amount')::numeric),0) INTO total FROM jsonb_array_elements(p->'allocations');
   IF abs(total)<>(p->>'amount')::numeric THEN RAISE EXCEPTION 'TSB_ALLOCATION_MISMATCH'; END IF;
   IF EXISTS(SELECT 1 FROM payment_records WHERE id=p->>'id' AND trip_id<>NEW.trip_id) THEN RAISE EXCEPTION 'TSB_PAYMENT_CONFLICT'; END IF;
   INSERT INTO payment_records(id,trip_id,currency,amount,from_id,to_id,created_by,created_at,revoked_at,payload_json)
   VALUES(p->>'id',NEW.trip_id,p->>'currency',(p->>'amount')::numeric,p->>'from_participant_id',p->>'to_participant_id',p->>'created_by',(p->>'created_at')::timestamptz,(p->>'revoked_at')::timestamptz,p)
   ON CONFLICT(id) DO UPDATE SET revoked_at=EXCLUDED.revoked_at,payload_json=EXCLUDED.payload_json;
   FOR a IN SELECT value FROM jsonb_array_elements(p->'allocations') LOOP
     INSERT INTO payment_allocations(payment_id,occurred_at,amount) VALUES(p->>'id',(a->>'date')::date,(a->>'amount')::numeric)
     ON CONFLICT(payment_id,occurred_at) DO NOTHING;
   END LOOP;
 END LOOP;
 RETURN NEW;
END; $$;
CREATE TRIGGER tsb_ledger_payment_projection AFTER INSERT OR UPDATE OF state_json ON public.trip_ledgers
FOR EACH ROW EXECUTE FUNCTION public.tsb_project_ledger_payments();
REVOKE ALL ON FUNCTION public.tsb_project_ledger_payments() FROM PUBLIC;
-- Backfill query projections without changing any payment or ledger version.
UPDATE trip_ledgers SET state_json=state_json;

INSERT INTO trip_ledgers(trip_id,revision,state_json)
SELECT DISTINCT e.trip_id,1,'{"closed_dates":[],"payments":[],"history_confirmation":{"status":"pending","source":"migration"}}'::jsonb
FROM expenses e WHERE NOT EXISTS(SELECT 1 FROM trip_ledgers l WHERE l.trip_id=e.trip_id);
UPDATE trip_ledgers SET revision=revision+1,state_json=jsonb_set(state_json,'{history_confirmation}','{"status":"pending","source":"migration"}')
WHERE NOT(state_json ? 'history_confirmation') AND jsonb_array_length(state_json->'payments')=0 AND jsonb_array_length(state_json->'closed_dates')=0
AND EXISTS(SELECT 1 FROM expenses e WHERE e.trip_id=trip_ledgers.trip_id);

CREATE FUNCTION public.tsb_member_ledger_snapshots(p_user_id text) RETURNS SETOF jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
 IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','')<>'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
 RETURN QUERY SELECT jsonb_build_object('trip_id',m.trip_id)||tsb_ledger_snapshot(m.trip_id,p_user_id)
 FROM trip_members m WHERE m.user_id=p_user_id ORDER BY m.trip_id;
END; $$;
REVOKE ALL ON FUNCTION public.tsb_member_ledger_snapshots(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_member_ledger_snapshots(text) TO service_role;
COMMIT;
