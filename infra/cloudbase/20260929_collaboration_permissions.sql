BEGIN;

CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite(p_invite_id text, p_trip_id text, p_creator_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id=p_trip_id AND owner_id=p_creator_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id) >= 2 THEN RETURN jsonb_build_object('full', true); END IF;
  UPDATE trip_invites SET revoked_at=now() WHERE trip_id=p_trip_id AND used_at IS NULL AND revoked_at IS NULL;
  INSERT INTO trip_invites(id, trip_id, creator_id, token_hash, expires_at) VALUES (p_invite_id, p_trip_id, p_creator_id, p_token_hash, p_expires_at);
  RETURN jsonb_build_object('id', p_invite_id, 'expires_at', p_expires_at);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_update_expense_revision(p_expense_id text, p_trip_id text, p_owner_id text, p_revision integer, p_occurred_at date, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_next_revision integer;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM expenses e JOIN trip_members m ON m.trip_id=e.trip_id WHERE e.id=p_expense_id AND e.trip_id=p_trip_id AND m.user_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  UPDATE expenses SET occurred_at=p_occurred_at, payload_json=p_payload, revision=revision+1 WHERE id=p_expense_id AND trip_id=p_trip_id AND revision=p_revision RETURNING revision INTO v_next_revision;
  IF v_next_revision IS NULL THEN RAISE EXCEPTION 'TSB_REVISION_CONFLICT'; END IF;
  RETURN jsonb_build_object('id', p_expense_id, 'revision', v_next_revision, 'occurred_at', p_occurred_at, 'payload', p_payload);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_delete_owner_expense(p_trip_id text, p_expense_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  DELETE FROM expenses e USING trips t WHERE e.id=p_expense_id AND e.trip_id=p_trip_id AND t.id=e.trip_id AND (e.owner_id=p_owner_id OR t.owner_id=p_owner_id);
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  RETURN jsonb_build_object('deleted', true);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_publish_settlement_version(p_version_id text, p_trip_id text, p_owner_id text, p_result jsonb, p_public jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id=p_trip_id AND owner_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO settlement_versions(id, trip_id, result_json, public_json) VALUES (p_version_id, p_trip_id, p_result, p_public);
  UPDATE trips SET latest_version_id=p_version_id WHERE id=p_trip_id;
  RETURN jsonb_build_object('id', p_version_id, 'trip_id', p_trip_id, 'result', p_result);
END; $$;

COMMIT;
