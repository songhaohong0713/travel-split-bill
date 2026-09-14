BEGIN;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;

CREATE OR REPLACE FUNCTION public.tsb_upsert_wechat_user_and_issue_refresh_token(p_openid_hash text, p_user_id text, p_refresh_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_user_id text;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_user_id, p_openid_hash)
  ON CONFLICT (wechat_openid_hash) DO UPDATE SET wechat_openid_hash = EXCLUDED.wechat_openid_hash
  RETURNING id INTO v_user_id;
  INSERT INTO refresh_tokens(id, token_hash, user_id, expires_at) VALUES (p_refresh_id, p_token_hash, v_user_id, p_expires_at);
  RETURN jsonb_build_object('user_id', v_user_id);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_rotate_refresh_token(p_old_token_hash text, p_refresh_id text, p_new_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_user_id text;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = p_old_token_hash AND revoked_at IS NULL AND expires_at > now() RETURNING user_id INTO v_user_id;
  IF v_user_id IS NULL THEN RETURN jsonb_build_object('valid', false); END IF;
  INSERT INTO refresh_tokens(id, token_hash, user_id, expires_at) VALUES (p_refresh_id, p_new_token_hash, v_user_id, p_expires_at);
  RETURN jsonb_build_object('valid', true, 'user_id', v_user_id);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_create_trip(p_trip_id text, p_owner_id text, p_name text, p_default_currency text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_owner_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trips(id, owner_id, name, default_currency) VALUES (p_trip_id, p_owner_id, p_name, p_default_currency);
  RETURN jsonb_build_object('id', p_trip_id, 'name', p_name, 'default_currency', p_default_currency);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_create_expense_with_idempotency(p_expense_id text, p_trip_id text, p_owner_id text, p_occurred_at date, p_payload jsonb, p_key text, p_response jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_response jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id = p_trip_id AND owner_id = p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  SELECT response_json::jsonb INTO v_response FROM idempotency_keys WHERE owner_id = p_owner_id AND key = p_key;
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
  UPDATE expenses SET occurred_at = p_occurred_at, payload_json = p_payload, revision = revision + 1
  WHERE id = p_expense_id AND trip_id = p_trip_id AND owner_id = p_owner_id AND revision = p_revision
  RETURNING revision INTO v_next_revision;
  IF v_next_revision IS NULL THEN RAISE EXCEPTION 'TSB_REVISION_CONFLICT'; END IF;
  RETURN jsonb_build_object('id', p_expense_id, 'revision', v_next_revision, 'occurred_at', p_occurred_at, 'payload', p_payload);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_publish_settlement_version(p_version_id text, p_trip_id text, p_owner_id text, p_result jsonb, p_public jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id = p_trip_id AND owner_id = p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO settlement_versions(id, trip_id, result_json, public_json) VALUES (p_version_id, p_trip_id, p_result, p_public);
  UPDATE trips SET latest_version_id = p_version_id WHERE id = p_trip_id;
  RETURN jsonb_build_object('id', p_version_id, 'trip_id', p_trip_id, 'result', p_result);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_create_share_link(p_link_id text, p_trip_id text, p_owner_id text, p_token_hash text, p_expires_at timestamp)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id = p_trip_id AND owner_id = p_owner_id AND latest_version_id IS NOT NULL) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO share_links(id, token_hash, trip_id, expires_at) VALUES (p_link_id, p_token_hash, p_trip_id, p_expires_at);
  RETURN jsonb_build_object('id', p_link_id, 'expires_at', p_expires_at);
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_create_receipt_image(p_image_id text, p_owner_id text, p_trip_id text, p_object_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trips WHERE id = p_trip_id AND owner_id = p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO receipt_images(id, owner_id, trip_id, object_key, status) VALUES (p_image_id, p_owner_id, p_trip_id, p_object_key, 'pending');
  RETURN jsonb_build_object('id', p_image_id, 'object_key', p_object_key, 'status', 'pending');
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_mark_receipt_uploaded(p_image_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  UPDATE receipt_images SET status = 'uploaded' WHERE id = p_image_id AND owner_id = p_owner_id;
  IF NOT FOUND THEN RETURN jsonb_build_object('not_found', true); END IF;
  RETURN jsonb_build_object('id', p_image_id, 'status', 'uploaded');
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_create_receipt_job(p_job_id text, p_image_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM receipt_images WHERE id = p_image_id AND owner_id = p_owner_id AND status = 'uploaded') THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO receipt_jobs(id, image_id, status, attempts) VALUES (p_job_id, p_image_id, 'queued', 0);
  RETURN jsonb_build_object('id', p_job_id, 'status', 'queued');
END; $$;

CREATE OR REPLACE FUNCTION public.tsb_claim_receipt_job()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_job receipt_jobs%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  SELECT * INTO v_job FROM receipt_jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE receipt_jobs SET status = 'processing', attempts = attempts + 1 WHERE id = v_job.id;
  RETURN jsonb_build_object('id', v_job.id, 'image_id', v_job.image_id, 'attempts', v_job.attempts + 1);
END; $$;

REVOKE ALL ON FUNCTION public.tsb_upsert_wechat_user_and_issue_refresh_token(text, text, text, text, timestamp) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_rotate_refresh_token(text, text, text, timestamp) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_create_trip(text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_create_expense_with_idempotency(text, text, text, date, jsonb, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_update_expense_revision(text, text, text, integer, date, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_publish_settlement_version(text, text, text, jsonb, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_create_share_link(text, text, text, text, timestamp) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_create_receipt_image(text, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_mark_receipt_uploaded(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_create_receipt_job(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.tsb_claim_receipt_job() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_upsert_wechat_user_and_issue_refresh_token(text, text, text, text, timestamp) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_rotate_refresh_token(text, text, text, timestamp) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip(text, text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_create_expense_with_idempotency(text, text, text, date, jsonb, text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_update_expense_revision(text, text, text, integer, date, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_publish_settlement_version(text, text, text, jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_create_share_link(text, text, text, text, timestamp) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_create_receipt_image(text, text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_mark_receipt_uploaded(text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_create_receipt_job(text, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_claim_receipt_job() TO service_role;
COMMIT;