BEGIN;
-- Retain the uploaded PNG byte-for-byte; JPEG conversion is only for the OCR copy.
UPDATE storage.buckets SET allowed_mime_types=ARRAY['image/jpeg','image/png']
WHERE id='receipts' AND public=false AND file_size_limit=5000000;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM storage.buckets WHERE id='receipts' AND public=false AND allowed_mime_types @> ARRAY['image/jpeg','image/png']::text[]) THEN
    RAISE EXCEPTION 'TSB_PRIVATE_RECEIPT_BUCKET_REQUIRED';
  END IF;
END $$;
COMMIT;
