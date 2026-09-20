BEGIN;

CREATE TABLE IF NOT EXISTS trip_members (
  id text PRIMARY KEY,
  trip_id text NOT NULL REFERENCES trips(id),
  user_id text NOT NULL REFERENCES users(id),
  joined_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_trip_members_trip_user UNIQUE(trip_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_trip_members_user_trip ON trip_members(user_id, trip_id);

CREATE TABLE IF NOT EXISTS trip_invites (
  id text PRIMARY KEY,
  trip_id text NOT NULL REFERENCES trips(id),
  creator_id text NOT NULL REFERENCES users(id),
  token_hash text NOT NULL UNIQUE,
  expires_at timestamp NOT NULL,
  used_at timestamp NULL,
  revoked_at timestamp NULL
);

CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite(p_invite_id text, p_trip_id text, p_creator_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_creator_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id) >= 2 THEN RETURN jsonb_build_object('full', true); END IF;
  UPDATE trip_invites SET revoked_at=now() WHERE trip_id=p_trip_id AND used_at IS NULL AND revoked_at IS NULL;
  INSERT INTO trip_invites(id, trip_id, creator_id, token_hash, expires_at) VALUES (p_invite_id, p_trip_id, p_creator_id, p_token_hash, p_expires_at);
  RETURN jsonb_build_object('id', p_invite_id, 'expires_at', p_expires_at);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_accept_trip_invite(p_token_hash text, p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_invite trip_invites%ROWTYPE; v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_invite FROM trip_invites WHERE token_hash=p_token_hash FOR UPDATE;
  IF NOT FOUND OR v_invite.used_at IS NOT NULL OR v_invite.revoked_at IS NOT NULL OR v_invite.expires_at <= now() THEN RETURN jsonb_build_object('expired', true); END IF;
  IF EXISTS (SELECT 1 FROM trip_members WHERE trip_id=v_invite.trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('already_member', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=v_invite.trip_id) >= 2 THEN RETURN jsonb_build_object('full', true); END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_user_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trip_members(id, trip_id, user_id) VALUES (v_invite.id || ':' || p_user_id, v_invite.trip_id, p_user_id);
  UPDATE trip_invites SET used_at=now() WHERE id=v_invite.id;
  SELECT * INTO v_trip FROM trips WHERE id=v_invite.trip_id;
  RETURN jsonb_build_object('id', v_trip.id, 'name', v_trip.name, 'default_currency', v_trip.default_currency, 'member_count', 2);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_create_trip_invite(text, text, text, text, timestamp) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_accept_trip_invite(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip_invite(text, text, text, text, timestamp) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_accept_trip_invite(text, text) TO service_role;

INSERT INTO trip_members(id, trip_id, user_id)
SELECT 'owner:' || id, id, owner_id FROM trips
ON CONFLICT (trip_id, user_id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.tsb_create_trip(p_trip_id text, p_owner_id text, p_name text, p_default_currency text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_owner_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trips(id, owner_id, name, default_currency) VALUES (p_trip_id, p_owner_id, p_name, p_default_currency);
  INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:' || p_trip_id, p_trip_id, p_owner_id);
  RETURN jsonb_build_object('id', p_trip_id, 'name', p_name, 'default_currency', p_default_currency, 'member_count', 1);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_create_trip(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip(text, text, text, text) TO service_role;

CREATE OR REPLACE FUNCTION public.tsb_list_member_trips(p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name, 'default_currency', t.default_currency, 'member_count', (SELECT count(*) FROM trip_members c WHERE c.trip_id=t.id)) ORDER BY t.created_at, t.id) FROM trips t JOIN trip_members m ON m.trip_id=t.id WHERE m.user_id=p_user_id), '[]'::jsonb);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_list_member_trips(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_list_member_trips(text) TO service_role;

CREATE OR REPLACE FUNCTION public.tsb_create_expense_with_idempotency(p_expense_id text, p_trip_id text, p_owner_id text, p_occurred_at date, p_payload jsonb, p_key text, p_response jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_response jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  SELECT response_json::jsonb INTO v_response FROM idempotency_keys WHERE owner_id=p_owner_id AND key=p_key;
  IF v_response IS NOT NULL THEN RETURN jsonb_build_object('replayed', true, 'response', v_response); END IF;
  INSERT INTO expenses(id, trip_id, owner_id, revision, occurred_at, payload_json) VALUES (p_expense_id, p_trip_id, p_owner_id, 1, p_occurred_at, p_payload);
  INSERT INTO idempotency_keys(id, owner_id, key, status_code, response_json) VALUES (p_key, p_owner_id, p_key, 201, p_response::text);
  RETURN jsonb_build_object('replayed', false, 'response', p_response);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_update_expense_revision(p_expense_id text, p_trip_id text, p_owner_id text, p_revision integer, p_occurred_at date, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_next_revision integer;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  UPDATE expenses SET occurred_at=p_occurred_at, payload_json=p_payload, revision=revision+1 WHERE id=p_expense_id AND trip_id=p_trip_id AND revision=p_revision RETURNING revision INTO v_next_revision;
  IF v_next_revision IS NULL THEN RAISE EXCEPTION 'TSB_REVISION_CONFLICT'; END IF;
  RETURN jsonb_build_object('id', p_expense_id, 'revision', v_next_revision, 'occurred_at', p_occurred_at, 'payload', p_payload);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_publish_settlement_version(p_version_id text, p_trip_id text, p_owner_id text, p_result jsonb, p_public jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO settlement_versions(id, trip_id, result_json, public_json) VALUES (p_version_id, p_trip_id, p_result, p_public);
  UPDATE trips SET latest_version_id=p_version_id WHERE id=p_trip_id;
  RETURN jsonb_build_object('id', p_version_id, 'trip_id', p_trip_id, 'result', p_result);
END; $$;
COMMIT;