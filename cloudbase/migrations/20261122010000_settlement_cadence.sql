BEGIN;
-- A trip is settled every day ('daily') or once, after its end date ('trip').
-- Every existing trip becomes 'daily'. The deployed create functions do not
-- name this column and keep working through its default.
ALTER TABLE public.trips ADD COLUMN IF NOT EXISTS settlement_cadence text NOT NULL DEFAULT 'daily';
ALTER TABLE public.trips ADD CONSTRAINT trip_settlement_cadence_values CHECK (settlement_cadence IN ('daily','trip'));
-- Backstop behind the functions below, which report this case as a flag first.
ALTER TABLE public.trips ADD CONSTRAINT trip_cadence_needs_end_date CHECK (settlement_cadence <> 'trip' OR end_date IS NOT NULL);

-- The previous backend keeps calling tsb_create_trip_with_timezone, which is
-- left exactly as deployed. A replay of a known id returns the stored trip
-- and never overwrites its cadence or dates.
CREATE FUNCTION public.tsb_create_trip_with_cadence(p_trip_id text, p_owner_id text, p_name text, p_default_currency text, p_timezone_offset integer, p_settlement_cadence text, p_start_date date, p_end_date date)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF p_settlement_cadence IS NULL OR p_settlement_cadence NOT IN ('daily','trip') THEN RAISE EXCEPTION 'TSB_INVALID_CADENCE'; END IF;
  IF p_settlement_cadence = 'trip' AND p_end_date IS NULL THEN RETURN jsonb_build_object('end_date_required', true); END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_owner_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trips(id, owner_id, name, default_currency, timezone_offset_minutes, settlement_cadence, start_date, end_date) VALUES (p_trip_id, p_owner_id, p_name, p_default_currency, p_timezone_offset, p_settlement_cadence, p_start_date, p_end_date) ON CONFLICT (id) DO NOTHING;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id FOR UPDATE;
  IF v_trip.owner_id <> p_owner_id OR v_trip.name <> p_name OR v_trip.default_currency <> p_default_currency THEN
    RETURN jsonb_build_object('conflict', true);
  END IF;
  INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:' || p_trip_id, p_trip_id, p_owner_id) ON CONFLICT (trip_id,user_id) DO NOTHING;
  RETURN jsonb_build_object('id', v_trip.id, 'name', v_trip.name, 'default_currency', v_trip.default_currency, 'member_count', (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id));
END; $$;

CREATE FUNCTION public.tsb_set_trip_settlement_cadence(p_trip_id text, p_user_id text, p_settlement_cadence text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF p_settlement_cadence IS NULL OR p_settlement_cadence NOT IN ('daily','trip') THEN RAISE EXCEPTION 'TSB_INVALID_CADENCE'; END IF;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id AND owner_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF p_settlement_cadence = 'trip' AND v_trip.end_date IS NULL THEN RETURN jsonb_build_object('end_date_required', true); END IF;
  UPDATE trips SET settlement_cadence=p_settlement_cadence WHERE id=p_trip_id;
  RETURN jsonb_build_object('ok', true);
END; $$;

-- Same signature as before. It now locks the trip and refuses to remove the
-- end date of a trip that is settled as a whole.
CREATE OR REPLACE FUNCTION public.tsb_update_trip_dates(p_trip_id text, p_user_id text, p_start_date date, p_end_date date)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id AND owner_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found',true); END IF;
  IF v_trip.settlement_cadence = 'trip' AND p_end_date IS NULL THEN RETURN jsonb_build_object('end_date_required', true); END IF;
  UPDATE trips SET start_date=p_start_date,end_date=p_end_date WHERE id=p_trip_id;
  RETURN jsonb_build_object('ok',true);
END; $$;

-- Same signature and the same six keys as before, plus the two the ledger
-- needs to decide when days open. The end date is part of the snapshot only
-- under the trip cadence, so editing the dates of a daily trip leaves its
-- previews valid. tsb_member_ledger_snapshots and tsb_commit_ledger call this
-- function by name and follow it without being rebuilt.
CREATE OR REPLACE FUNCTION public.tsb_ledger_snapshot(p_trip_id text, p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE result jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('not_found',true); END IF;
  SELECT jsonb_build_object(
    'currency', t.default_currency,
    'timezone_offset_minutes', t.timezone_offset_minutes,
    'settlement_cadence', t.settlement_cadence,
    'settlement_end_date', CASE WHEN t.settlement_cadence = 'trip' THEN t.end_date END,
    'members', (SELECT jsonb_agg(user_id ORDER BY user_id) FROM trip_members WHERE trip_id=t.id),
    'revision', coalesce(l.revision,0),
    'state', coalesce(l.state_json, '{"closed_dates":[],"payments":[]}'::jsonb),
    'expenses', coalesce((SELECT jsonb_agg(jsonb_build_object('id',e.id,'revision',e.revision,'occurred_at',e.occurred_at,'payload',e.payload_json) ORDER BY e.id) FROM expenses e WHERE e.trip_id=t.id),'[]'::jsonb)
  ) INTO result FROM trips t LEFT JOIN trip_ledgers l ON l.trip_id=t.id WHERE t.id=p_trip_id;
  RETURN result;
END; $$;

REVOKE ALL ON FUNCTION public.tsb_create_trip_with_cadence(text,text,text,text,integer,text,date,date) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_set_trip_settlement_cadence(text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip_with_cadence(text,text,text,text,integer,text,date,date) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_set_trip_settlement_cadence(text,text,text) TO service_role;
COMMIT;
