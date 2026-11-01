BEGIN;
ALTER TABLE public.trips ADD COLUMN IF NOT EXISTS timezone_offset_minutes integer;
ALTER TABLE public.trips ADD CONSTRAINT trip_timezone_bounds CHECK (timezone_offset_minutes BETWEEN -840 AND 720);
-- Unknown ages start a fresh 30-day retention window; do not guess historical upload dates.
ALTER TABLE public.receipt_images ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE OR REPLACE FUNCTION public.tsb_create_trip_with_timezone(p_trip_id text, p_owner_id text, p_name text, p_default_currency text, p_timezone_offset integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_trip trips%ROWTYPE;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  INSERT INTO users(id, wechat_openid_hash) VALUES (p_owner_id, NULL) ON CONFLICT (id) DO NOTHING;
  INSERT INTO trips(id, owner_id, name, default_currency, timezone_offset_minutes) VALUES (p_trip_id, p_owner_id, p_name, p_default_currency, p_timezone_offset) ON CONFLICT (id) DO NOTHING;
  SELECT * INTO v_trip FROM trips WHERE id=p_trip_id FOR UPDATE;
  IF v_trip.owner_id <> p_owner_id OR v_trip.name <> p_name OR v_trip.default_currency <> p_default_currency THEN
    RETURN jsonb_build_object('conflict', true);
  END IF;
  INSERT INTO trip_members(id, trip_id, user_id) VALUES ('owner:' || p_trip_id, p_trip_id, p_owner_id) ON CONFLICT (trip_id,user_id) DO NOTHING;
  RETURN jsonb_build_object('id', v_trip.id, 'name', v_trip.name, 'default_currency', v_trip.default_currency, 'member_count', (SELECT count(*) FROM trip_members WHERE trip_id=p_trip_id));
END; $$;
REVOKE ALL ON FUNCTION public.tsb_create_trip_with_timezone(text,text,text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.tsb_create_trip_with_timezone(text,text,text,text,integer) TO service_role;
CREATE OR REPLACE FUNCTION public.tsb_ledger_snapshot(p_trip_id text, p_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE result jsonb;
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_user_id) THEN RETURN jsonb_build_object('not_found',true); END IF;
  SELECT jsonb_build_object(
    'currency', t.default_currency,
    'timezone_offset_minutes', t.timezone_offset_minutes,
    'members', (SELECT jsonb_agg(user_id ORDER BY user_id) FROM trip_members WHERE trip_id=t.id),
    'revision', coalesce(l.revision,0),
    'state', coalesce(l.state_json, '{"closed_dates":[],"payments":[]}'::jsonb),
    'expenses', coalesce((SELECT jsonb_agg(jsonb_build_object('id',e.id,'revision',e.revision,'occurred_at',e.occurred_at,'payload',e.payload_json) ORDER BY e.id) FROM expenses e WHERE e.trip_id=t.id),'[]'::jsonb)
  ) INTO result FROM trips t LEFT JOIN trip_ledgers l ON l.trip_id=t.id WHERE t.id=p_trip_id;
  RETURN result;
END; $$;


CREATE FUNCTION public.tsb_set_trip_timezone(p_trip_id text,p_user_id text,p_timezone_offset integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE t trips%ROWTYPE;
BEGIN
 IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
 SELECT * INTO t FROM trips WHERE id=p_trip_id AND owner_id=p_user_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('not_found',true); END IF;
 IF t.timezone_offset_minutes IS NOT NULL THEN RETURN jsonb_build_object('conflict',true); END IF;
 UPDATE trips SET timezone_offset_minutes=p_timezone_offset WHERE id=p_trip_id;
 RETURN jsonb_build_object('ok',true);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_set_trip_timezone(text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.tsb_set_trip_timezone(text,text,integer) TO service_role;

CREATE FUNCTION public.tsb_receipt_orphan_preview(p_cutoff timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE result jsonb;
BEGIN
 IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
 IF p_cutoff > now()-interval '30 days' THEN RAISE EXCEPTION 'TSB_RETENTION_REQUIRED'; END IF;
 SELECT coalesce(jsonb_agg(to_jsonb(candidate)),'[]'::jsonb) INTO result FROM (
  SELECT i.id,i.trip_id,i.object_key,i.created_at FROM receipt_images i
  WHERE i.created_at < p_cutoff
   AND NOT EXISTS(SELECT 1 FROM receipt_jobs j WHERE j.image_id=i.id AND j.status IN ('queued','processing'))
   AND NOT EXISTS(SELECT 1 FROM receipt_jobs j JOIN expenses e ON e.payload_json->>'receipt_job_id'=j.id WHERE j.image_id=i.id)
  ORDER BY i.created_at,i.id LIMIT 1000
 ) candidate;
 RETURN result;
END; $$;
CREATE FUNCTION public.tsb_queue_receipt_orphan(p_image_id text,p_cutoff timestamptz) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE image receipt_images%ROWTYPE;
BEGIN
 IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
 IF p_cutoff > now()-interval '30 days' THEN RAISE EXCEPTION 'TSB_RETENTION_REQUIRED'; END IF;
 SELECT * INTO image FROM receipt_images WHERE id=p_image_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('queued',false); END IF;
 PERFORM id FROM trips WHERE id=image.trip_id FOR UPDATE;
 SELECT * INTO image FROM receipt_images WHERE id=p_image_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('queued',false); END IF;
 -- Serialize against OCR retry/claim updates before deciding a job is inactive.
 PERFORM id FROM receipt_jobs WHERE image_id=image.id FOR UPDATE;
 IF image.created_at >= p_cutoff
  OR EXISTS(SELECT 1 FROM receipt_jobs j WHERE j.image_id=image.id AND j.status IN ('queued','processing'))
  OR EXISTS(SELECT 1 FROM receipt_jobs j JOIN expenses e ON e.payload_json->>'receipt_job_id'=j.id WHERE j.image_id=image.id)
 THEN RETURN jsonb_build_object('queued',false); END IF;
 INSERT INTO receipt_cleanup(object_key) VALUES(image.object_key) ON CONFLICT DO NOTHING;
 DELETE FROM receipt_jobs WHERE image_id=image.id;
 DELETE FROM receipt_images WHERE id=image.id;
 RETURN jsonb_build_object('queued',true);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_receipt_orphan_preview(timestamptz) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.tsb_queue_receipt_orphan(text,timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.tsb_receipt_orphan_preview(timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.tsb_queue_receipt_orphan(text,timestamptz) TO service_role;
COMMIT;
