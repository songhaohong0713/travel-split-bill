BEGIN;
-- An offset closes days that cancel out for one member: no money, no payer.
-- The projection trigger already copies a missing from_participant_id as NULL.
ALTER TABLE public.payment_records ALTER COLUMN from_id DROP NOT NULL;
COMMIT;
