BEGIN;
-- Preserve old backend compatibility while accepting genuinely persisted originals.
CREATE OR REPLACE FUNCTION public.tsb_create_receipt_job(p_job_id text, p_image_id text, p_owner_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','') <> 'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
  IF NOT EXISTS (SELECT 1 FROM receipt_images i JOIN trip_members m ON m.trip_id=i.trip_id AND m.user_id=p_owner_id WHERE i.id=p_image_id AND i.owner_id=p_owner_id AND i.status IN ('uploaded','stored')) THEN
    RETURN jsonb_build_object('not_found',true);
  END IF;
  INSERT INTO receipt_jobs(id,image_id,status,attempts) VALUES (p_job_id,p_image_id,'queued',0);
  RETURN jsonb_build_object('id',p_job_id,'status','queued');
END $$;
REVOKE ALL ON FUNCTION public.tsb_create_receipt_job(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_create_receipt_job(text,text,text) TO service_role;
COMMIT;
