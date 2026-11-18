ALTER TABLE public.trip_members ADD COLUMN IF NOT EXISTS nickname varchar(40);
ALTER TABLE public.receipt_cleanup ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
