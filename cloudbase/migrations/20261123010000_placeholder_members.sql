BEGIN;
-- A placeholder is a member who does not use the app. Only this flag tells
-- one apart: real users can also have no WeChat identity. Every existing
-- member is a real one, and inserts that do not name the columns stay so.
ALTER TABLE public.trip_members ADD COLUMN IF NOT EXISTS is_placeholder boolean NOT NULL DEFAULT false;
ALTER TABLE public.trip_members ADD COLUMN IF NOT EXISTS added_by text REFERENCES public.users(id);

-- Any member adds a person by name. Placeholders count toward the limit.
CREATE FUNCTION public.tsb_add_placeholder_member(p_member_id text, p_placeholder_id text, p_trip_id text, p_user_id text, p_nickname text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  PERFORM id FROM trips WHERE id=p_trip_id FOR UPDATE;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id) >= 6 THEN RETURN jsonb_build_object('full', true); END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_placeholder_id, NULL);
  INSERT INTO trip_members(id, trip_id, user_id, nickname, is_placeholder, added_by) VALUES (p_member_id, p_trip_id, p_placeholder_id, p_nickname, true, p_user_id);
  RETURN jsonb_build_object('id', p_placeholder_id, 'nickname', p_nickname);
END; $$;

-- The backend decides from a snapshot that the placeholder never paid, owed
-- or settled, and which expenses merely list it. This function applies that
-- decision only if the trip is still exactly as the backend read it.
CREATE FUNCTION public.tsb_delete_placeholder_member(p_trip_id text, p_user_id text, p_placeholder_id text, p_expected jsonb, p_expenses jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_change jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  PERFORM id FROM trips WHERE id=p_trip_id FOR UPDATE;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_placeholder_id AND is_placeholder) THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF tsb_ledger_snapshot(p_trip_id, p_user_id) IS DISTINCT FROM p_expected THEN RETURN jsonb_build_object('conflict', true); END IF;
  FOR v_change IN SELECT value FROM jsonb_array_elements(coalesce(p_expenses, '[]'::jsonb)) LOOP
    UPDATE expenses SET payload_json=(v_change->'payload')::json, revision=revision+1 WHERE id=v_change->>'id' AND trip_id=p_trip_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TSB_PLACEHOLDER_EXPENSE_MISSING'; END IF;
  END LOOP;
  DELETE FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_placeholder_id AND is_placeholder;
  -- Foreign keys abort everything if the placeholder is referenced after all.
  DELETE FROM users WHERE id=p_placeholder_id;
  RETURN jsonb_build_object('ok', true);
END; $$;

-- Shared by the two claim functions: the invite must be live, the caller not
-- yet a member, and the placeholder still unclaimed. Locks the trip, then the
-- invite, in the order joining does.
CREATE FUNCTION public.tsb_claim_check(p_token_hash text, p_user_id text, p_placeholder_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_invite trip_invites%ROWTYPE; v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_invite FROM trip_invites WHERE token_hash=p_token_hash;
  IF NOT FOUND THEN RETURN jsonb_build_object('expired', true); END IF;
  SELECT * INTO v_trip FROM trips WHERE id=v_invite.trip_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('expired', true); END IF;
  SELECT * INTO v_invite FROM trip_invites WHERE id=v_invite.id FOR UPDATE;
  IF NOT FOUND OR v_invite.used_at IS NOT NULL OR v_invite.revoked_at IS NOT NULL OR v_invite.expires_at <= now() THEN RETURN jsonb_build_object('expired', true); END IF;
  IF EXISTS (SELECT 1 FROM trip_members WHERE trip_id=v_trip.id AND user_id=p_user_id) THEN RETURN jsonb_build_object('already_member', true); END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=v_trip.id AND user_id=p_placeholder_id AND is_placeholder) THEN RETURN jsonb_build_object('unavailable', true); END IF;
  RETURN jsonb_build_object('trip_id', v_trip.id, 'owner_id', v_trip.owner_id, 'name', v_trip.name, 'default_currency', v_trip.default_currency);
END; $$;

-- What the backend rewrites: the trip's snapshot, read through its creator
-- because the caller is not a member yet.
CREATE FUNCTION public.tsb_claim_snapshot(p_token_hash text, p_user_id text, p_placeholder_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_check jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  v_check := tsb_claim_check(p_token_hash, p_user_id, p_placeholder_id);
  IF NOT (v_check ? 'trip_id') THEN RETURN v_check; END IF;
  RETURN jsonb_build_object('snapshot', tsb_ledger_snapshot(v_check->>'trip_id', v_check->>'owner_id'));
END; $$;

-- The claim itself, all or nothing. The placeholder's id becomes the caller's
-- id in the member row, the expenses, the settlement rows and the ledger
-- state; then the placeholder's user row is deleted. The settlement rows are
-- written before the ledger state, with the very documents the state holds,
-- so the projection trigger that guards settlement history sees no difference
-- and stays exactly as strict as it is for every other write.
CREATE FUNCTION public.tsb_claim_placeholder_member(p_token_hash text, p_user_id text, p_placeholder_id text, p_expected jsonb, p_expenses jsonb, p_state jsonb, p_payments jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_check jsonb; v_trip_id text; v_change jsonb; v_row jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  v_check := tsb_claim_check(p_token_hash, p_user_id, p_placeholder_id);
  IF NOT (v_check ? 'trip_id') THEN RETURN v_check; END IF;
  v_trip_id := v_check->>'trip_id';
  IF tsb_ledger_snapshot(v_trip_id, v_check->>'owner_id') IS DISTINCT FROM p_expected THEN RETURN jsonb_build_object('conflict', true); END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_user_id, NULL) ON CONFLICT (id) DO NOTHING;
  -- The row is kept, so the member keeps its place, name and join time.
  UPDATE trip_members SET user_id=p_user_id, is_placeholder=false WHERE trip_id=v_trip_id AND user_id=p_placeholder_id AND is_placeholder;
  FOR v_change IN SELECT value FROM jsonb_array_elements(coalesce(p_expenses, '[]'::jsonb)) LOOP
    UPDATE expenses SET payload_json=(v_change->'payload')::json, revision=revision+1 WHERE id=v_change->>'id' AND trip_id=v_trip_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TSB_CLAIM_EXPENSE_MISSING'; END IF;
  END LOOP;
  FOR v_change IN SELECT value FROM jsonb_array_elements(coalesce(p_payments, '[]'::jsonb)) LOOP
    UPDATE payment_records SET from_id=v_change->>'from_id', to_id=v_change->>'to_id', payload_json=v_change->'payload' WHERE id=v_change->>'id' AND trip_id=v_trip_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TSB_CLAIM_PAYMENT_MISSING'; END IF;
    FOR v_row IN SELECT value FROM jsonb_array_elements(v_change->'allocations') LOOP
      UPDATE payment_allocations SET amount=(v_row->>'amount')::numeric WHERE payment_id=v_change->>'id' AND occurred_at=(v_row->>'date')::date;
    END LOOP;
  END LOOP;
  -- A snapshot read before the claim must not be written back afterwards.
  UPDATE trip_ledgers SET state_json=p_state, revision=revision+1 WHERE trip_id=v_trip_id;
  -- Foreign keys abort everything if any row still names the placeholder.
  DELETE FROM users WHERE id=p_placeholder_id;
  RETURN jsonb_build_object('id', v_trip_id, 'name', v_check->>'name', 'default_currency', v_check->>'default_currency', 'member_count', (SELECT count(*) FROM trip_members WHERE trip_id=v_trip_id));
END; $$;

-- Same signature as before. A full trip may still issue a link while it has a
-- placeholder: the link is then only good for claiming one, since joining as
-- a new member answers "full" in tsb_accept_trip_invite, which is unchanged.
CREATE OR REPLACE FUNCTION public.tsb_create_trip_invite(p_invite_id text, p_trip_id text, p_creator_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id AND owner_id=p_creator_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  IF (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id) >= 6 AND NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND is_placeholder) THEN RETURN jsonb_build_object('full', true); END IF;
  UPDATE trip_invites SET revoked_at=now() WHERE trip_id=p_trip_id AND used_at IS NULL AND revoked_at IS NULL;
  INSERT INTO trip_invites(id, trip_id, creator_id, token_hash, expires_at) VALUES (p_invite_id, p_trip_id, p_creator_id, p_token_hash, p_expires_at);
  RETURN jsonb_build_object('id', p_invite_id, 'expires_at', p_expires_at);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_add_placeholder_member(text,text,text,text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_delete_placeholder_member(text,text,text,jsonb,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_claim_check(text,text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_claim_snapshot(text,text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_claim_placeholder_member(text,text,text,jsonb,jsonb,jsonb,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.tsb_add_placeholder_member(text,text,text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_delete_placeholder_member(text,text,text,jsonb,jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_claim_check(text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_claim_snapshot(text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_claim_placeholder_member(text,text,text,jsonb,jsonb,jsonb,jsonb) TO service_role;
COMMIT;
