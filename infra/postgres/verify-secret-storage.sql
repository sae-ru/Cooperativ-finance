\set ON_ERROR_STOP on
\pset tuples_only on
\pset format unaligned

BEGIN TRANSACTION READ ONLY;

DO $secret_storage$
DECLARE
    invalid_count bigint;
    unexpected_columns text;
    column_row record;
    marker text;
    private_key_markers text[] := ARRAY[
        '-----BEGIN ' || 'PRIVATE KEY-----',
        '-----BEGIN RSA ' || 'PRIVATE KEY-----',
        '-----BEGIN EC ' || 'PRIVATE KEY-----',
        '-----BEGIN OPENSSH ' || 'PRIVATE KEY-----',
        '-----BEGIN DSA ' || 'PRIVATE KEY-----',
        '-----BEGIN ENCRYPTED ' || 'PRIVATE KEY-----'
    ];
BEGIN
    IF to_regclass('identity.users') IS NULL
       OR to_regclass('identity.auth_sessions') IS NULL
       OR to_regclass('identity.authentication_factors') IS NULL
       OR to_regclass('identity.account_recovery_requests') IS NULL
       OR to_regclass('identity.service_client_credentials') IS NULL
       OR to_regclass('identity.service_client_access_tokens') IS NULL THEN
        RAISE EXCEPTION 'SECRET_STORAGE_SCHEMA_INCOMPLETE';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.users
    WHERE password_hash NOT LIKE chr(36) || 'argon2id' || chr(36) || '%';
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION 'SECRET_STORAGE_PASSWORD_HASH_INVALID identity.users';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.account_recovery_requests
    WHERE temporary_password_hash NOT LIKE chr(36) || 'argon2id' || chr(36) || '%';
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION
            'SECRET_STORAGE_PASSWORD_HASH_INVALID identity.account_recovery_requests';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.auth_sessions
    WHERE access_token_hash !~ '^[0-9a-f]{64}$'
       OR refresh_token_hash !~ '^[0-9a-f]{64}$'
       OR csrf_token_hash !~ '^[0-9a-f]{64}$'
       OR (
            client_ip_hash IS NOT NULL
            AND client_ip_hash !~ '^[0-9a-f]{64}$'
       )
       OR (
            user_agent_hash IS NOT NULL
            AND user_agent_hash !~ '^[0-9a-f]{64}$'
       );
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION 'SECRET_STORAGE_TOKEN_HASH_INVALID identity.auth_sessions';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.service_client_credentials
    WHERE secret_hash !~ '^[0-9a-f]{64}$'
       OR length(secret_prefix) NOT BETWEEN 1 AND 32;
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION
            'SECRET_STORAGE_CREDENTIAL_HASH_INVALID identity.service_client_credentials';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.service_client_access_tokens
    WHERE access_token_hash !~ '^[0-9a-f]{64}$';
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION
            'SECRET_STORAGE_TOKEN_HASH_INVALID identity.service_client_access_tokens';
    END IF;

    SELECT count(*) INTO invalid_count
    FROM identity.authentication_factors
    WHERE octet_length(secret_nonce) <> 12
       OR octet_length(secret_ciphertext) < 17
       OR encryption_key_version = '';
    IF invalid_count <> 0 THEN
        RAISE EXCEPTION
            'SECRET_STORAGE_MFA_CIPHERTEXT_INVALID identity.authentication_factors';
    END IF;

    SELECT string_agg(
        format('%I.%I.%I', table_schema, table_name, column_name),
        ', '
        ORDER BY table_schema, table_name, column_name
    )
    INTO unexpected_columns
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND column_name ~*
          '(^|_)(password|passwd|secret|token|private_key|signing_seed|encryption_key)($|_)'
      AND format('%s.%s.%s', table_schema, table_name, column_name) NOT IN (
          'identity.account_recovery_requests.temporary_password_hash',
          'identity.auth_sessions.access_token_hash',
          'identity.auth_sessions.csrf_token_hash',
          'identity.auth_sessions.refresh_token_hash',
          'identity.authentication_factors.encryption_key_version',
          'identity.authentication_factors.secret_ciphertext',
          'identity.authentication_factors.secret_nonce',
          'identity.service_client_access_tokens.access_token_hash',
          'identity.service_client_credentials.secret_hash',
          'identity.service_client_credentials.secret_prefix',
          'identity.users.must_change_password',
          'identity.users.password_changed_at',
          'identity.users.password_hash'
      );
    IF unexpected_columns IS NOT NULL THEN
        RAISE EXCEPTION
            'SECRET_STORAGE_UNREVIEWED_COLUMNS %',
            unexpected_columns;
    END IF;

    FOR column_row IN
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND data_type IN (
              'text',
              'character varying',
              'character',
              'json',
              'jsonb',
              'bytea'
          )
        ORDER BY table_schema, table_name, ordinal_position
    LOOP
        FOREACH marker IN ARRAY private_key_markers
        LOOP
            IF column_row.data_type = 'bytea' THEN
                EXECUTE format(
                    'SELECT count(*) FROM %I.%I WHERE position(convert_to(%L, ''UTF8'') in %I) > 0',
                    column_row.table_schema,
                    column_row.table_name,
                    marker,
                    column_row.column_name
                )
                INTO invalid_count;
            ELSE
                EXECUTE format(
                    'SELECT count(*) FROM %I.%I WHERE position(%L in %I::text) > 0',
                    column_row.table_schema,
                    column_row.table_name,
                    marker,
                    column_row.column_name
                )
                INTO invalid_count;
            END IF;
            IF invalid_count <> 0 THEN
                RAISE EXCEPTION
                    'SECRET_STORAGE_PRIVATE_KEY_MARKER %.%.%',
                    column_row.table_schema,
                    column_row.table_name,
                    column_row.column_name;
            END IF;
        END LOOP;

        IF column_row.data_type <> 'bytea' THEN
            EXECUTE format(
                $query$
                SELECT count(*)
                FROM %I.%I
                WHERE %I::text ~*
                    '["''](password|passwd|secret|private_key|signing_seed|encryption_key|api_token|access_token|refresh_token)["'']\s*:\s*["''][^"'']{8,}["'']'
                   OR %I::text ~*
                    '(postgres(ql)?|mysql|redis|amqp|https?)://[^/[:space:]:@]+:[^@[:space:]/]+@'
                $query$,
                column_row.table_schema,
                column_row.table_name,
                column_row.column_name,
                column_row.column_name
            )
            INTO invalid_count;
            IF invalid_count <> 0 THEN
                RAISE EXCEPTION
                    'SECRET_STORAGE_PLAINTEXT_PATTERN %.%.%',
                    column_row.table_schema,
                    column_row.table_name,
                    column_row.column_name;
            END IF;
        END IF;
    END LOOP;
END
$secret_storage$;

SELECT 'secret_storage=PASS';
COMMIT;
