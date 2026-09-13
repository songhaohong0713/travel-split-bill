BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260912_01

CREATE TABLE users (
    id VARCHAR(36) NOT NULL, 
    wechat_openid_hash VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (wechat_openid_hash)
);

CREATE TABLE refresh_tokens (
    id VARCHAR(36) NOT NULL, 
    token_hash VARCHAR(64) NOT NULL, 
    user_id VARCHAR(36) NOT NULL, 
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens (token_hash);

CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE trips (
    id VARCHAR(36) NOT NULL, 
    owner_id VARCHAR(36) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    default_currency VARCHAR(3) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE INDEX ix_trips_owner_id ON trips (owner_id);

CREATE TABLE expenses (
    id VARCHAR(36) NOT NULL, 
    trip_id VARCHAR(36) NOT NULL, 
    owner_id VARCHAR(36) NOT NULL, 
    revision INTEGER NOT NULL, 
    occurred_at DATE NOT NULL, 
    payload_json JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(trip_id) REFERENCES trips (id), 
    FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE INDEX ix_expenses_trip_id ON expenses (trip_id);

CREATE INDEX ix_expenses_owner_id ON expenses (owner_id);

CREATE TABLE idempotency_keys (
    id VARCHAR(36) NOT NULL, 
    owner_id VARCHAR(36) NOT NULL, 
    key VARCHAR(36) NOT NULL, 
    status_code INTEGER NOT NULL, 
    response_json TEXT NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_owner_idempotency_key UNIQUE (owner_id, key), 
    FOREIGN KEY(owner_id) REFERENCES users (id)
);

CREATE INDEX ix_idempotency_keys_owner_id ON idempotency_keys (owner_id);

INSERT INTO alembic_version (version_num) VALUES ('20260912_01') RETURNING alembic_version.version_num;

-- Running upgrade 20260912_01 -> 20260912_02

ALTER TABLE trips ADD COLUMN latest_version_id VARCHAR(36);

CREATE TABLE settlement_versions (
    id VARCHAR(36) NOT NULL, 
    trip_id VARCHAR(36) NOT NULL, 
    result_json JSON NOT NULL, 
    public_json JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(trip_id) REFERENCES trips (id)
);

CREATE INDEX ix_settlement_versions_trip_id ON settlement_versions (trip_id);

UPDATE alembic_version SET version_num='20260912_02' WHERE alembic_version.version_num = '20260912_01';

-- Running upgrade 20260912_02 -> 20260912_03

CREATE TABLE share_links (
    id VARCHAR(36) NOT NULL, 
    token_hash VARCHAR(64) NOT NULL, 
    trip_id VARCHAR(36) NOT NULL, 
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(trip_id) REFERENCES trips (id)
);

CREATE UNIQUE INDEX ix_share_links_token_hash ON share_links (token_hash);

CREATE INDEX ix_share_links_trip_id ON share_links (trip_id);

UPDATE alembic_version SET version_num='20260912_03' WHERE alembic_version.version_num = '20260912_02';

-- Running upgrade 20260912_03 -> 20260912_04

CREATE TABLE receipt_images (
    id VARCHAR(36) NOT NULL, 
    owner_id VARCHAR(36) NOT NULL, 
    trip_id VARCHAR(36) NOT NULL, 
    object_key VARCHAR(255) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(owner_id) REFERENCES users (id), 
    FOREIGN KEY(trip_id) REFERENCES trips (id), 
    UNIQUE (object_key)
);

CREATE TABLE receipt_jobs (
    id VARCHAR(36) NOT NULL, 
    image_id VARCHAR(36) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (image_id), 
    FOREIGN KEY(image_id) REFERENCES receipt_images (id)
);

UPDATE alembic_version SET version_num='20260912_04' WHERE alembic_version.version_num = '20260912_03';

-- Running upgrade 20260912_04 -> 20260913_05

ALTER TABLE receipt_jobs ADD COLUMN attempts INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE receipt_jobs ADD COLUMN candidates_json JSON;

ALTER TABLE receipt_jobs ADD COLUMN error_code VARCHAR(80);

UPDATE alembic_version SET version_num='20260913_05' WHERE alembic_version.version_num = '20260912_04';

COMMIT;

