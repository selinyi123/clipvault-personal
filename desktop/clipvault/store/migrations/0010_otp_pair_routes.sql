-- OTP Pair Identity metadata only. Pair verifiers and OTP plaintext are never
-- stored in SQLite; verifier material lives in Windows Credential Manager.

CREATE TABLE otp_pair_routes (
  sync_device_id    TEXT PRIMARY KEY,
  session_epoch     TEXT NOT NULL UNIQUE,
  sender_device_id  TEXT NOT NULL UNIQUE,
  target_device_id  TEXT NOT NULL,
  credential_target TEXT NOT NULL UNIQUE,
  revoked           INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
  FOREIGN KEY (sync_device_id) REFERENCES sync_peers(device_id)
    ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_otp_pair_routes_active
  ON otp_pair_routes(sync_device_id) WHERE revoked = 0;
