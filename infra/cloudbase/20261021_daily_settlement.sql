BEGIN;
ALTER TABLE public.trips ADD COLUMN start_date date;
ALTER TABLE public.trips ADD COLUMN end_date date;
ALTER TABLE public.trips ADD CONSTRAINT trip_date_order CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date);

CREATE TABLE public.trip_ledgers (
  trip_id varchar(36) PRIMARY KEY REFERENCES public.trips(id) ON DELETE CASCADE,
  revision integer NOT NULL,
  state_json jsonb NOT NULL
);
ALTER TABLE public.trip_ledgers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.trip_ledgers FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.trip_ledgers TO service_role;

-- All expense changes and ledger commits serialize on the same trip row.
CREATE FUNCTION public.tsb_lock_expense_trip() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM id FROM trips WHERE id = OLD.trip_id FOR UPDATE;
    RETURN OLD;
  END IF;
  PERFORM id FROM trips WHERE id = NEW.trip_id FOR UPDATE;
  RETURN NEW;
END; $$;
CREATE TRIGGER tsb_expense_trip_lock BEFORE INSERT OR UPDATE OR DELETE ON public.expenses
FOR EACH ROW EXECUTE FUNCTION public.tsb_lock_expense_trip();

CREATE FUNCTION public.tsb_ledger_snapshot(p_trip_id text, p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE result jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('not_found',true); END IF;
  SELECT jsonb_build_object(
    'currency', t.default_currency,
    'members', (SELECT jsonb_agg(user_id ORDER BY user_id) FROM trip_members WHERE trip_id=t.id),
    'revision', coalesce(l.revision,0),
    'state', coalesce(l.state_json, '{"closed_dates":[],"payments":[]}'::jsonb),
    'expenses', coalesce((SELECT jsonb_agg(jsonb_build_object('id',e.id,'revision',e.revision,'occurred_at',e.occurred_at,'payload',e.payload_json) ORDER BY e.id) FROM expenses e WHERE e.trip_id=t.id),'[]'::jsonb)
  ) INTO result FROM trips t LEFT JOIN trip_ledgers l ON l.trip_id=t.id WHERE t.id=p_trip_id;
  RETURN result;
END; $$;

CREATE FUNCTION public.tsb_commit_ledger(p_trip_id text, p_user_id text, p_expected jsonb, p_state jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE current_snapshot jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  PERFORM id FROM trips WHERE id=p_trip_id FOR UPDATE;
  current_snapshot := tsb_ledger_snapshot(p_trip_id,p_user_id);
  IF current_snapshot IS NULL OR current_snapshot ? 'not_found' OR current_snapshot <> p_expected THEN RETURN jsonb_build_object('conflict',true); END IF;
  INSERT INTO trip_ledgers(trip_id,revision,state_json) VALUES(p_trip_id,1,p_state)
  ON CONFLICT(trip_id) DO UPDATE SET revision=trip_ledgers.revision+1,state_json=p_state;
  RETURN jsonb_build_object('ok',true);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_lock_expense_trip() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_ledger_snapshot(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_commit_ledger(text,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_ledger_snapshot(text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_commit_ledger(text,text,jsonb,jsonb) TO service_role;
CREATE FUNCTION public.tsb_update_trip_dates(p_trip_id text, p_user_id text, p_start_date date, p_end_date date)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  UPDATE trips SET start_date=p_start_date,end_date=p_end_date WHERE id=p_trip_id AND owner_id=p_user_id;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found',true); END IF;
  RETURN jsonb_build_object('ok',true);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_update_trip_dates(text,text,date,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_update_trip_dates(text,text,date,date) TO service_role;
COMMIT;
