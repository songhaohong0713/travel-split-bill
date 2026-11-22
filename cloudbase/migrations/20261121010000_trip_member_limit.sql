BEGIN;
-- A trip holds up to six members and one invite link admits all of them.
-- These two functions were last defined in infra/cloudbase
-- (20260919_trip_collaboration.sql, 20260929_collaboration_permissions.sql).

-- Lock the trip, then the invite. Accept takes the same locks in that order,
-- so replacing a link and joining it cannot pass each other.
CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite(p_invite_id text, p_trip_id text, p_creator_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id AND owner_id=p_creator_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id) >= 6 THEN RETURN jsonb_build_object('full', true); END IF;
  UPDATE trip_invites SET revoked_at=now() WHERE trip_id=p_trip_id AND used_at IS NULL AND revoked_at IS NULL;
  INSERT INTO trip_invites(id, trip_id, creator_id, token_hash, expires_at) VALUES (p_invite_id, p_trip_id, p_creator_id, p_token_hash, p_expires_at);
  RETURN jsonb_build_object('id', p_invite_id, 'expires_at', p_expires_at);
END; $$;

-- used_at is no longer written: it only marks links consumed under the former
-- single-use rule, which stay expired. The token lookup below is only to find
-- the trip. Validity is checked again after the trip lock, on a locked invite row.
CREATE OR REPLACE FUNCTION public.tsb_accept_trip_invite(p_token_hash text, p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_invite trip_invites%ROWTYPE; v_trip trips%ROWTYPE; v_count integer;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_invite FROM trip_invites WHERE token_hash=p_token_hash;
  IF NOT FOUND THEN RETURN jsonb_build_object('expired', true); END IF;
  SELECT * INTO v_trip FROM trips WHERE id=v_invite.trip_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('expired', true); END IF;
  SELECT * INTO v_invite FROM trip_invites WHERE id=v_invite.id FOR UPDATE;
  IF NOT FOUND OR v_invite.used_at IS NOT NULL OR v_invite.revoked_at IS NOT NULL OR v_invite.expires_at <= now() THEN RETURN jsonb_build_object('expired', true); END IF;
  IF EXISTS (SELECT 1 FROM trip_members WHERE trip_id=v_invite.trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('already_member', true); END IF;
  SELECT count(*) INTO v_count FROM trip_members WHERE trip_id=v_invite.trip_id;
  IF v_count >= 6 THEN RETURN jsonb_build_object('full', true); END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_user_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trip_members(id, trip_id, user_id) VALUES (v_invite.id || ':' || p_user_id, v_invite.trip_id, p_user_id);
  RETURN jsonb_build_object('id', v_trip.id, 'name', v_trip.name, 'default_currency', v_trip.default_currency, 'member_count', v_count + 1);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_create_trip_invite(text, text, text, text, timestamp) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_accept_trip_invite(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip_invite(text, text, text, text, timestamp) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_accept_trip_invite(text, text) TO service_role;
COMMIT;
