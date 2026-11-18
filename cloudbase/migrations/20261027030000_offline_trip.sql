BEGIN;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS nickname varchar(40);
-- Offline-created UUIDs replay only for the same owner and exact creation data.
CREATE OR REPLACE FUNCTION public.tsb_create_trip(p_trip_id text, p_owner_id text, p_name text, p_default_currency text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_owner_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trips(id, owner_id, name, default_currency) VALUES (p_trip_id, p_owner_id, p_name, p_default_currency) ON CONFLICT (id) DO NOTHING;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id FOR UPDATE;
  IF v_trip.owner_id <> p_owner_id OR v_trip.name <> p_name OR v_trip.default_currency <> p_default_currency THEN
    RETURN jsonb_build_object('conflict', true);
  END IF;
  INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:' || p_trip_id, p_trip_id, p_owner_id) ON CONFLICT (trip_id,user_id) DO NOTHING;
  RETURN jsonb_build_object('id', v_trip.id, 'name', v_trip.name, 'default_currency', v_trip.default_currency, 'member_count', (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id));
END; $$;
REVOKE ALL ON FUNCTION public.tsb_create_trip(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip(text, text, text, text) TO service_role;
COMMIT;
