BEGIN;
CREATE FUNCTION public.tsb_export_business_backup() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE backup_table text; data jsonb='{}'; columns jsonb='{}'; rows jsonb; shape jsonb;
BEGIN
 IF coalesce(current_setting('request.jwt.claims',true)::jsonb->>'role','')<>'service_role' THEN RAISE EXCEPTION 'TSB_FORBIDDEN'; END IF;
 FOREACH backup_table IN ARRAY ARRAY['users','trips','trip_members','trip_invites','expenses','receipt_images','receipt_jobs','settlement_versions','share_links','idempotency_keys','refresh_tokens','trip_ledgers','payment_records','payment_allocations'] LOOP
   EXECUTE format('SELECT coalesce(jsonb_agg(to_jsonb(t)),''[]''::jsonb) FROM public.%I t',backup_table) INTO rows;
   SELECT jsonb_agg(jsonb_build_object('name',c.column_name,'type',c.udt_name) ORDER BY c.column_name) INTO shape FROM information_schema.columns c WHERE c.table_schema='public' AND c.table_name=backup_table;
   data=data||jsonb_build_object(backup_table,rows);
   columns=columns||jsonb_build_object(backup_table,shape);
 END LOOP;
 RETURN jsonb_build_object('schema_version','20261022_07','exported_at',now(),'tables',data,'schema',columns);
END; $$;
REVOKE ALL ON FUNCTION public.tsb_export_business_backup() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tsb_export_business_backup() TO service_role;
COMMIT;
