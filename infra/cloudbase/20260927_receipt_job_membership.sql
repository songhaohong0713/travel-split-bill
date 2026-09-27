BEGIN;

CREATE OR REPLACE FUNCTION public.tsb_create_receipt_image(p_image_id text, p_owner_id text, p_trip_id text, p_object_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM trip_members WHERE trip_id=p_trip_id AND user_id=p_owner_id) THEN RETURN jsonb_build_object('not_found', true); END IF;
  INSERT INTO receipt_images(id, owner_id, trip_id, object_key, status) VALUES (p_image_id, p_owner_id, p_trip_id, p_object_key, 'pending');
  RETURN jsonb_build_object('id', p_image_id, 'object_key', p_object_key, 'status', 'pending');
END; $$;

REVOKE ALL ON FUNCTION public.tsb_create_receipt_image(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_receipt_image(text, text, text, text) TO service_role;

COMMIT;
