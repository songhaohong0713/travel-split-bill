BEGIN;

-- Backend-only private storage. The app's own JWT is deliberately not a Storage JWT.
INSERT INTO storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
VALUES ('receipts', 'receipts', false, 5000000, ARRAY['image/jpeg'])
ON CONFLICT (id) DO NOTHING;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM storage.buckets WHERE id='receipts' AND (public IS DISTINCT FROM false OR file_size_limit IS DISTINCT FROM 5000000 OR allowed_mime_types IS DISTINCT FROM ARRAY['image/jpeg']::text[])) THEN
    RAISE EXCEPTION 'TSB_RECEIPT_BUCKET_CONFIGURATION_CONFLICT';
  END IF;
END $$;
-- Restrictive policies keep receipts private even if another bucket later adds a broad allow policy.
CREATE POLICY receipts_backend_only_objects ON storage.objects AS RESTRICTIVE
FOR ALL TO anon, authenticated USING (bucket_id <> 'receipts') WITH CHECK (bucket_id <> 'receipts');
CREATE POLICY receipts_backend_only_bucket ON storage.buckets AS RESTRICTIVE
FOR ALL TO anon, authenticated USING (id <> 'receipts') WITH CHECK (id <> 'receipts');

CREATE TABLE public.receipt_cleanup (
  object_key varchar(255) PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.receipt_cleanup ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.receipt_cleanup FROM anon, authenticated, PUBLIC;
GRANT SELECT, INSERT, DELETE ON public.receipt_cleanup TO service_role;

CREATE FUNCTION public.tsb_queue_receipt_delete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF OLD.status IN ('stored', 'pending') THEN
    INSERT INTO receipt_cleanup(object_key) VALUES (OLD.object_key) ON CONFLICT DO NOTHING;
  END IF;
  RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION public.tsb_queue_receipt_delete() FROM PUBLIC;
CREATE TRIGGER receipt_image_cleanup AFTER DELETE ON public.receipt_images
FOR EACH ROW EXECUTE FUNCTION public.tsb_queue_receipt_delete();

CREATE FUNCTION public.tsb_check_receipt_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE v_job text; v_image receipt_images%ROWTYPE;
BEGIN
  v_job := NEW.payload_json->>'receipt_job_id';
  IF TG_OP='UPDATE' AND v_job IS NOT DISTINCT FROM (OLD.payload_json->>'receipt_job_id') THEN RETURN NEW; END IF;
  IF coalesce(v_job,'')='' THEN RETURN NEW; END IF;
  SELECT i.* INTO v_image FROM receipt_images i JOIN receipt_jobs j ON j.image_id=i.id
    WHERE j.id=v_job AND i.trip_id=NEW.trip_id FOR UPDATE OF i;
  IF NOT FOUND OR v_image.status NOT IN ('uploaded','stored') THEN RAISE EXCEPTION 'TSB_RECEIPT_REFERENCE_INVALID'; END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.tsb_check_receipt_reference() FROM PUBLIC;
CREATE TRIGGER expense_receipt_reference BEFORE INSERT OR UPDATE ON public.expenses
FOR EACH ROW EXECUTE FUNCTION public.tsb_check_receipt_reference();

CREATE FUNCTION public.tsb_cleanup_unused_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE v_job text; v_image text;
BEGIN
  v_job := OLD.payload_json->>'receipt_job_id';
  IF TG_OP='UPDATE' AND v_job IS NOT DISTINCT FROM (NEW.payload_json->>'receipt_job_id') THEN RETURN NEW; END IF;
  IF coalesce(v_job,'')<>'' THEN
    SELECT i.id INTO v_image FROM receipt_images i JOIN receipt_jobs j ON j.image_id=i.id
      WHERE j.id=v_job AND i.trip_id=OLD.trip_id FOR UPDATE OF i;
    IF FOUND AND NOT EXISTS (SELECT 1 FROM expenses WHERE trip_id=OLD.trip_id AND payload_json->>'receipt_job_id'=v_job) THEN
      DELETE FROM receipt_jobs WHERE image_id=v_image;
      DELETE FROM receipt_images WHERE id=v_image;
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.tsb_cleanup_unused_receipt() FROM PUBLIC;
CREATE TRIGGER expense_receipt_cleanup AFTER UPDATE OR DELETE ON public.expenses
FOR EACH ROW EXECUTE FUNCTION public.tsb_cleanup_unused_receipt();

COMMIT;
-- Rollback: stop new uploads first, drain receipt_cleanup, drop the three triggers,
-- then their functions and receipt_cleanup. Retain the bucket and original files.
-- Never DELETE storage.objects using SQL; remove object bytes through Storage API.
