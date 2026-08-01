package com.clipvault.poc.engine;

import static com.clipvault.poc.engine.EngineSessionContract.HostEpoch;
import static com.clipvault.poc.engine.EngineSessionContract.SessionId;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.OptionalLong;

/**
 * Memory-only client ledger keyed by the strongly typed host/session identity.
 * A sequence is reserved before any editor side effect is attempted.
 */
public final class AppliedResponseLedger {
    public record SessionKey(HostEpoch hostEpoch, SessionId sessionId) {
        public SessionKey {
            if (hostEpoch == null || sessionId == null) {
                throw new IllegalArgumentException("session key must be complete");
            }
        }
    }

    public enum Reservation {
        RESERVED,
        DUPLICATE_OR_OLDER,
        OUT_OF_ORDER_GAP,
        UNKNOWN_SESSION
    }

    private final Map<SessionKey, Long> highestApplied = new LinkedHashMap<>();

    public synchronized void open(SessionKey key, long appliedStartSequence) {
        requirePositive(appliedStartSequence);
        if (highestApplied.putIfAbsent(key, appliedStartSequence) != null) {
            throw new IllegalStateException("session ledger key is already live");
        }
    }

    public synchronized Reservation reserve(SessionKey key, long responseSequence) {
        requirePositive(responseSequence);
        Long previous = highestApplied.get(key);
        if (previous == null) {
            return Reservation.UNKNOWN_SESSION;
        }
        if (responseSequence <= previous) {
            return Reservation.DUPLICATE_OR_OLDER;
        }
        if (previous < Long.MAX_VALUE && responseSequence == previous + 1) {
            highestApplied.put(key, responseSequence);
            return Reservation.RESERVED;
        }
        return Reservation.OUT_OF_ORDER_GAP;
    }

    public synchronized void close(SessionKey key) {
        highestApplied.remove(key);
    }

    public synchronized void closeHost(HostEpoch hostEpoch) {
        highestApplied.keySet().removeIf(key -> key.hostEpoch().equals(hostEpoch));
    }

    public synchronized int liveSessionCount() {
        return highestApplied.size();
    }

    public synchronized OptionalLong highestApplied(SessionKey key) {
        Long value = highestApplied.get(key);
        return value == null ? OptionalLong.empty() : OptionalLong.of(value);
    }

    private static void requirePositive(long sequence) {
        if (sequence <= 0) {
            throw new IllegalArgumentException("response sequence must be positive");
        }
    }
}
