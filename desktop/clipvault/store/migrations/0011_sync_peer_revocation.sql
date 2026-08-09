-- A sync bearer must become durably unusable before optional OTP credential
-- and Broker cleanup.  Keep the peer row as a retryable tombstone while the
-- otp_pair_routes foreign key still references it.

ALTER TABLE sync_peers
  ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0
  CHECK (revoked IN (0, 1));

CREATE INDEX idx_sync_peers_active_token
  ON sync_peers(token_hash) WHERE revoked = 0;
