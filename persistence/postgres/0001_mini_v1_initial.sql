CREATE TABLE channels (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL CHECK(category IN ('OFFLINE','DELIVERY','LOCAL_LIFE','OTHER')),
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            );

CREATE TABLE industry_benchmarks (
                id BIGSERIAL PRIMARY KEY,
                category_code TEXT NOT NULL,
                region_level TEXT NOT NULL CHECK(region_level IN ('CITY','PROVINCE','REGION','COUNTRY')),
                region_code TEXT NOT NULL DEFAULT '',
                metric_code TEXT NOT NULL,
                low_value TEXT NOT NULL,
                mid_value TEXT,
                high_value TEXT NOT NULL,
                unit TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_date TEXT NOT NULL,
                confidence_level TEXT NOT NULL CHECK(confidence_level IN ('LOW','MEDIUM','HIGH')),
                review_status TEXT NOT NULL CHECK(review_status IN ('DRAFT','APPROVED','RETIRED')),
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE(category_code,region_level,region_code,metric_code,valid_from)
            );

CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at BIGINT NOT NULL);

CREATE TABLE stores (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, store_type TEXT, timezone TEXT, currency TEXT, created_at BIGINT, updated_at BIGINT);

CREATE TABLE users (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL);

CREATE TABLE ai_conversations (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

CREATE TABLE anomaly_events (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                metric_code TEXT NOT NULL,
                observed_value TEXT,
                baseline_low TEXT,
                baseline_high TEXT,
                baseline_source TEXT,
                estimated_impact_low TEXT,
                estimated_impact_high TEXT,
                severity TEXT NOT NULL CHECK(severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
                confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                actionability INTEGER NOT NULL CHECK(actionability BETWEEN 1 AND 3),
                persistence INTEGER NOT NULL CHECK(persistence BETWEEN 1 AND 3),
                root_cause_group TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED')),
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE cost_entries (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                period_start TEXT,
                period_end TEXT,
                business_date TEXT,
                cost_type TEXT NOT NULL CHECK(cost_type IN ('FOOD','LABOR','RENT','UTILITY','MARKETING','PACKAGING','OTHER')),
                amount TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_document_id BIGINT,
                basis TEXT,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE daily_data_status (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                expected_source_count INTEGER NOT NULL DEFAULT 0,
                received_source_count INTEGER NOT NULL DEFAULT 0,
                user_declared_complete INTEGER NOT NULL DEFAULT 0 CHECK(user_declared_complete IN (0,1)),
                completeness_level TEXT NOT NULL CHECK(completeness_level IN ('NO_DATA','PARTIAL','COMPLETE','DECLARED_COMPLETE')),
                calculation_version TEXT NOT NULL DEFAULT 'COMPLETENESS_V1',
                as_of_time BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE(store_id,business_date),
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE import_files (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                file_name TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                created_at BIGINT NOT NULL,
                UNIQUE(store_id,file_hash),
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE mini_users (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL UNIQUE,
                wechat_openid TEXT NOT NULL UNIQUE,
                wechat_unionid TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED')),
                created_at BIGINT NOT NULL,
                last_login_at BIGINT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

CREATE TABLE profit_snapshots (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                as_of_time BIGINT NOT NULL,
                revenue_amount TEXT,
                profit_status TEXT NOT NULL,
                profit_value TEXT,
                profit_low TEXT,
                profit_high TEXT,
                data_completeness TEXT,
                confidence_level TEXT,
                calculation_version TEXT NOT NULL,
                input_signature TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE store_cost_profiles (
                store_id BIGINT PRIMARY KEY,
                food_cost_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                food_cost_value TEXT,
                labor_cost_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                monthly_labor_actual TEXT,
                full_time_count INTEGER,
                part_time_hours_month TEXT,
                rent_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                monthly_rent TEXT,
                utilities_mode TEXT NOT NULL DEFAULT 'UNKNOWN',
                utilities_value TEXT,
                owner_work_mode TEXT,
                operating_days_per_month INTEGER,
                allocation_basis TEXT NOT NULL DEFAULT 'CALENDAR_DAY',
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE store_data_sources (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                channel_code TEXT,
                data_type TEXT NOT NULL,
                report_scope TEXT NOT NULL DEFAULT '',
                first_seen_at BIGINT NOT NULL,
                last_seen_at BIGINT NOT NULL,
                appearance_days INTEGER NOT NULL DEFAULT 0,
                recent_30d_days INTEGER NOT NULL DEFAULT 0,
                is_expected INTEGER NOT NULL DEFAULT 0 CHECK(is_expected IN (0,1)),
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE store_profiles (
                store_id BIGINT PRIMARY KEY,
                category_code TEXT NOT NULL CHECK(category_code IN ('MILK_TEA','COFFEE','FAST_FOOD_SNACK','CHINESE_DINING','HOTPOT','BBQ','BAKERY_DESSERT','BAR_LEISURE','OTHER_FOOD')),
                city_code TEXT,
                owner_work_mode TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id)
            );

CREATE TABLE upload_documents (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                document_type TEXT NOT NULL CHECK(document_type IN ('IMAGE','FILE','MANUAL')),
                original_filename TEXT,
                storage_key TEXT,
                mime_type TEXT,
                business_date TEXT,
                report_scope TEXT,
                status TEXT NOT NULL CHECK(status IN ('UPLOADED','PROCESSING','NEEDS_CONFIRMATION','CONFIRMED','IMPORTED','FAILED','REJECTED')),
                error_code TEXT,
                error_message TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

CREATE TABLE action_items (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                anomaly_id BIGINT,
                action_type TEXT NOT NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL,
                expected_metric TEXT NOT NULL,
                expected_direction TEXT NOT NULL CHECK(expected_direction IN ('UP','DOWN','STABLE')),
                priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','EXECUTED','SKIPPED','REMIND','WAITING_VERIFICATION','VERIFIED')),
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(anomaly_id) REFERENCES anomaly_events(id)
            );

CREATE TABLE ai_messages (
                id BIGSERIAL PRIMARY KEY,
                conversation_id BIGINT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('USER','ASSISTANT','SYSTEM')),
                content TEXT NOT NULL,
                context_json TEXT,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES ai_conversations(id)
            );

CREATE TABLE extraction_jobs (
                id BIGSERIAL PRIMARY KEY,
                document_id BIGINT NOT NULL,
                detected_platform TEXT,
                detected_page_type TEXT,
                template_code TEXT,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','NEEDS_CONFIRMATION','COMPLETED','FAILED')),
                provider TEXT NOT NULL,
                model_version TEXT,
                started_at BIGINT,
                completed_at BIGINT,
                error_code TEXT,
                error_message TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id)
            );

CREATE TABLE import_batches (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                source_file_id BIGINT,
                file_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                channel_id BIGINT,
                imported_at BIGINT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                accepted_rows INTEGER NOT NULL DEFAULT 0,
                rejected_rows INTEGER NOT NULL DEFAULT 0,
                duplicate_rows INTEGER NOT NULL DEFAULT 0,
                updated_rows INTEGER NOT NULL DEFAULT 0,
                parser_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','COMPLETED','PARTIAL','FAILED')),
                error_message TEXT,
                file_hash TEXT NOT NULL,
                unknown_fields_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id)
            );

CREATE TABLE action_executions (
                id BIGSERIAL PRIMARY KEY,
                action_id BIGINT NOT NULL UNIQUE,
                executed_at BIGINT NOT NULL,
                execution_note TEXT,
                baseline_metric_code TEXT NOT NULL,
                baseline_metric_value TEXT,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );

CREATE TABLE action_verifications (
                id BIGSERIAL PRIMARY KEY,
                action_id BIGINT NOT NULL,
                verification_date TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                change_rate TEXT,
                result TEXT NOT NULL CHECK(result IN ('IMPROVED','NO_CLEAR_CHANGE','WORSENED','INSUFFICIENT_DATA')),
                confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                explanation TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                UNIQUE(action_id,verification_date),
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );

CREATE TABLE daily_store_sales_totals (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                gross_sales TEXT NOT NULL,
                order_count INTEGER,
                source_document_id BIGINT,
                import_batch_id BIGINT,
                source_scope TEXT NOT NULL DEFAULT 'STORE_TOTAL' CHECK(source_scope='STORE_TOTAL'),
                source_kind TEXT NOT NULL,
                source_ref TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE(store_id,business_date),
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id)
            );

CREATE TABLE extracted_fields (
                id BIGSERIAL PRIMARY KEY,
                extraction_job_id BIGINT NOT NULL,
                field_code TEXT NOT NULL,
                raw_text TEXT,
                normalized_value TEXT,
                confidence TEXT NOT NULL,
                user_corrected INTEGER NOT NULL DEFAULT 0 CHECK(user_corrected IN (0,1)),
                confirmed_value TEXT,
                excluded_by_user INTEGER NOT NULL DEFAULT 0 CHECK(excluded_by_user IN (0,1)),
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(extraction_job_id) REFERENCES extraction_jobs(id),
                UNIQUE(extraction_job_id,field_code)
            );

CREATE TABLE raw_import_rows (
                id BIGSERIAL PRIMARY KEY,
                import_batch_id BIGINT NOT NULL,
                row_number INTEGER NOT NULL,
                raw_payload_json TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                error_message TEXT,
                created_at BIGINT NOT NULL,
                UNIQUE(import_batch_id,row_number),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id)
            );

CREATE TABLE reminders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                store_id BIGINT NOT NULL,
                action_id BIGINT NOT NULL,
                reminder_type TEXT NOT NULL DEFAULT 'ACTION_FOLLOWUP',
                scheduled_at BIGINT NOT NULL,
                wechat_template_id TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','SENT','FAILED','CANCELLED')),
                sent_at BIGINT,
                error_message TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(action_id) REFERENCES action_items(id)
            );

CREATE TABLE daily_channel_sales (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                business_date TEXT NOT NULL,
                channel_id BIGINT NOT NULL,
                gross_sales TEXT NOT NULL DEFAULT '0.00',
                customer_paid TEXT,
                order_count INTEGER,
                refund_amount TEXT,
                refund_count INTEGER,
                merchant_discount TEXT,
                platform_subsidy TEXT,
                source TEXT,
                source_file_id BIGINT,
                import_batch_id BIGINT,
                raw_import_row_id BIGINT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            );

CREATE TABLE document_import_links (
                id BIGSERIAL PRIMARY KEY,
                document_id BIGINT NOT NULL,
                import_file_id BIGINT,
                import_batch_id BIGINT,
                whole_store_record_id BIGINT,
                confirmed_by_user_id BIGINT NOT NULL,
                confirmed_at BIGINT NOT NULL,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES upload_documents(id),
                FOREIGN KEY(import_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(whole_store_record_id) REFERENCES daily_store_sales_totals(id),
                FOREIGN KEY(confirmed_by_user_id) REFERENCES users(id),
                CHECK(import_batch_id IS NOT NULL OR whole_store_record_id IS NOT NULL)
            );

CREATE TABLE payments (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                payment_date TEXT NOT NULL,
                amount TEXT NOT NULL,
                payment_method TEXT,
                bank_reference TEXT,
                settlement_reference TEXT,
                payment_fingerprint TEXT NOT NULL,
                source TEXT,
                source_file_id BIGINT,
                import_batch_id BIGINT,
                raw_import_row_id BIGINT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            );

CREATE TABLE settlements (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                settlement_period_start TEXT,
                settlement_period_end TEXT,
                settlement_date TEXT,
                gross_sales TEXT,
                customer_paid TEXT,
                commission_fee TEXT,
                delivery_fee TEXT,
                technical_service_fee TEXT,
                promotion_fee TEXT,
                merchant_discount TEXT,
                refund_amount TEXT,
                other_fee TEXT,
                platform_subsidy TEXT,
                reported_expected_settlement TEXT,
                calculated_expected_settlement TEXT,
                calculation_profile TEXT,
                calculation_version TEXT,
                settlement_reference TEXT,
                settlement_fingerprint TEXT NOT NULL,
                source_file_id BIGINT,
                import_batch_id BIGINT,
                raw_import_row_id BIGINT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            );

CREATE TABLE platform_fees (
                id BIGSERIAL PRIMARY KEY,
                store_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                business_date TEXT,
                settlement_id BIGINT,
                fee_type TEXT NOT NULL CHECK(fee_type IN ('COMMISSION','DELIVERY','TECH_SERVICE','MERCHANT_DISCOUNT','PROMOTION','REFUND','OTHER')),
                amount TEXT NOT NULL,
                description TEXT,
                source_file_id BIGINT,
                import_batch_id BIGINT,
                raw_import_row_id BIGINT,
                created_at BIGINT NOT NULL,
                FOREIGN KEY(store_id) REFERENCES stores(id),
                FOREIGN KEY(channel_id) REFERENCES channels(id),
                FOREIGN KEY(settlement_id) REFERENCES settlements(id),
                FOREIGN KEY(source_file_id) REFERENCES import_files(id),
                FOREIGN KEY(import_batch_id) REFERENCES import_batches(id),
                FOREIGN KEY(raw_import_row_id) REFERENCES raw_import_rows(id)
            );

CREATE TABLE reconciliation_matches (
                id BIGSERIAL PRIMARY KEY,
                settlement_id BIGINT NOT NULL,
                payment_id BIGINT NOT NULL,
                matched_amount TEXT NOT NULL,
                match_type TEXT NOT NULL,
                confidence TEXT,
                created_at BIGINT NOT NULL,
                UNIQUE(settlement_id,payment_id),
                FOREIGN KEY(settlement_id) REFERENCES settlements(id),
                FOREIGN KEY(payment_id) REFERENCES payments(id)
            );

CREATE INDEX ix_action_store_status
                ON action_items(store_id,status,business_date);

CREATE INDEX ix_ai_messages_conversation
                ON ai_messages(conversation_id,created_at);

CREATE INDEX ix_anomaly_events_store_date
               ON anomaly_events(store_id,business_date,status);

CREATE INDEX ix_cost_entries_store_date_type
               ON cost_entries(store_id,business_date,cost_type);

CREATE INDEX ix_daily_channel_sales_store_channel_date ON daily_channel_sales(store_id,channel_id,business_date);

CREATE INDEX ix_daily_channel_sales_store_date ON daily_channel_sales(store_id,business_date);

CREATE INDEX ix_document_import_links_document
               ON document_import_links(document_id,created_at);

CREATE INDEX ix_extraction_jobs_document
               ON extraction_jobs(document_id,created_at);

CREATE INDEX ix_import_batches_store_imported ON import_batches(store_id,imported_at);

CREATE INDEX ix_industry_benchmarks_lookup
               ON industry_benchmarks(category_code,metric_code,review_status,region_level,region_code,valid_from);

CREATE INDEX ix_payments_store_channel_date ON payments(store_id,channel_id,payment_date);

CREATE INDEX ix_reminders_due ON reminders(status,scheduled_at);

CREATE INDEX ix_settlements_store_channel_date ON settlements(store_id,channel_id,settlement_date);

CREATE INDEX ix_upload_documents_store_created
               ON upload_documents(store_id,created_at);

CREATE UNIQUE INDEX uq_action_identity
                ON action_items(store_id,business_date,COALESCE(anomaly_id,-1),action_type);

CREATE UNIQUE INDEX uq_anomaly_identity
                ON anomaly_events(store_id,business_date,anomaly_type,metric_code,COALESCE(root_cause_group,''));

CREATE UNIQUE INDEX uq_daily_channel_sales_store_date_channel ON daily_channel_sales(store_id,business_date,channel_id);

CREATE UNIQUE INDEX uq_payments_fingerprint ON payments(payment_fingerprint);

CREATE UNIQUE INDEX uq_profit_snapshots_input
               ON profit_snapshots(store_id,business_date,calculation_version,input_signature);

CREATE UNIQUE INDEX uq_settlements_fingerprint ON settlements(settlement_fingerprint);

CREATE UNIQUE INDEX uq_store_data_sources_identity
               ON store_data_sources(store_id,COALESCE(channel_code,''),data_type,report_scope);

INSERT INTO schema_meta(key,value,updated_at) VALUES ('schema_version','3',0) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at;

INSERT INTO schema_meta(key,value,updated_at) VALUES ('cloud_schema_version','1',0) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at;
