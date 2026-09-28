BEGIN;

CREATE OR REPLACE FUNCTION public.tsb_list_member_trips(p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name, 'default_currency', t.default_currency, 'member_count', (SELECT count(*) FROM trip_members c WHERE c.trip_id=t.id), 'is_owner', t.owner_id=p_user_id) ORDER BY t.created_at, t.id) FROM trips t JOIN trip_members m ON m.trip_id=t.id WHERE m.user_id=p_user_id), '[]'::jsonb);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_list_member_trips(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_list_member_trips(text) TO service_role;

CREATE OR REPLACE FUNCTION public.tsb_delete_owner_expense(p_trip_id text, p_expense_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id=p_trip_id AND owner_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  DELETE FROM expenses WHERE id=p_expense_id AND trip_id=p_trip_id;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  RETURN jsonb_build_object('deleted', true);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_delete_owner_trip(p_trip_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id=p_trip_id AND owner_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  DELETE FROM receipt_jobs WHERE image_id IN (SELECT id FROM receipt_images WHERE trip_id=p_trip_id);
  DELETE FROM receipt_images WHERE trip_id=p_trip_id;
  DELETE FROM share_links WHERE trip_id=p_trip_id;
  DELETE FROM settlement_versions WHERE trip_id=p_trip_id;
  DELETE FROM trip_invites WHERE trip_id=p_trip_id;
  DELETE FROM expenses WHERE trip_id=p_trip_id;
  DELETE FROM trip_members WHERE trip_id=p_trip_id;
  DELETE FROM trips WHERE id=p_trip_id;
  RETURN jsonb_build_object('deleted', true);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_delete_owner_expense(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_delete_owner_trip(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_delete_owner_expense(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_delete_owner_trip(text, text) TO service_role;

COMMIT;
