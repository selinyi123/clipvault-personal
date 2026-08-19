package com.clipvault.poc.engine;

import static com.clipvault.poc.engine.EngineSessionContract.Adapter;
import static com.clipvault.poc.engine.EngineSessionContract.Candidate;
import static com.clipvault.poc.engine.EngineSessionContract.CandidateSource;
import static com.clipvault.poc.engine.EngineSessionContract.CompositionSegment;
import static com.clipvault.poc.engine.EngineSessionContract.EngineMode;
import static com.clipvault.poc.engine.EngineSessionContract.EngineOption;
import static com.clipvault.poc.engine.EngineSessionContract.ErrorCode;
import static com.clipvault.poc.engine.EngineSessionContract.HostEpoch;
import static com.clipvault.poc.engine.EngineSessionContract.InputContext;
import static com.clipvault.poc.engine.EngineSessionContract.PageDirection;
import static com.clipvault.poc.engine.EngineSessionContract.ProtocolException;
import static com.clipvault.poc.engine.EngineSessionContract.SegmentKind;
import static com.clipvault.poc.engine.EngineSessionContract.SessionId;
import static com.clipvault.poc.engine.EngineSessionContract.SessionStart;
import static com.clipvault.poc.engine.EngineSessionContract.State;
import static com.clipvault.poc.engine.EngineSessionContract.Transition;

import java.util.Iterator;
import java.util.Arrays;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.function.LongSupplier;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * In-memory protocol test double. The fixed lexicon is synthetic and exists
 * only to exercise session behavior before any JNI or third-party binary is
 * introduced.
 */
public final class FakeEngineHost implements Adapter {
    private static final int MAX_TOMBSTONES = 64;
    private static final long DEFAULT_RETRY_DEADLINE_NANOS = 2_000_000_000L;

    private enum SessionStatus {
        ACTIVE,
        ENDED,
        INVALIDATED_BY_HOST
    }

    private record CandidateTemplate(String text, String comment) {}

    private record RequestFingerprint(
        String operation,
        long expectedRevision,
        String argumentMac
    ) {}

    static final class SimulatedStartResponseLoss extends RuntimeException {
        private SimulatedStartResponseLoss() {
            super("synthetic start response loss");
        }
    }

    static final class SimulatedTransitionResponseLoss extends RuntimeException {
        private SimulatedTransitionResponseLoss() {
            super("synthetic transition response loss");
        }
    }

    private static final class SessionRecord {
        private final HostEpoch hostEpoch;
        private final SessionId id;
        private final int pageSize;
        private SessionStatus status = SessionStatus.ACTIVE;
        private InputContext context;
        private long revision;
        private String composing = "";
        private int pageIndex;
        private List<Candidate> allCandidates = List.of();
        private long lastRequestSeq;
        private RequestFingerprint lastRequest;
        private Transition cachedTransition;
        private SessionStart cachedStart;
        private long cachedResponseStartedNanos;
        private boolean responseCachePending;
        private long lastAcknowledgedResponseSeq;
        private final Map<EngineOption, Boolean> options = new EnumMap<>(EngineOption.class);
        private int commitMutationCount;

        private SessionRecord(
            HostEpoch hostEpoch,
            SessionId id,
            InputContext context,
            int pageSize
        ) {
            this.hostEpoch = hostEpoch;
            this.id = id;
            this.context = context;
            this.pageSize = pageSize;
        }
    }

    @FunctionalInterface
    private interface Change {
        String apply(SessionRecord record);
    }

    private final Map<String, List<CandidateTemplate>> lexicon = Map.of(
        "nihao",
        List.of(
            new CandidateTemplate("你好", "synthetic-1"),
            new CandidateTemplate("拟好", "synthetic-2"),
            new CandidateTemplate("你号", "synthetic-3")
        ),
        "zhongguo",
        List.of(new CandidateTemplate("中国", "synthetic-1"))
    );

    private final Map<SessionId, SessionRecord> sessions = new LinkedHashMap<>();
    private final LongSupplier monotonicNanos;
    private final long retryDeadlineNanos;
    private HostEpoch hostEpoch = newHostEpoch();
    private byte[] fingerprintKey = newFingerprintKey();
    private boolean dropNextStartResponse;
    private boolean corruptNextTransitionRevision;
    private boolean failNextEnd;
    private boolean throwRuntimeOnNextEnd;
    private boolean dropNextTransitionResponse;
    private boolean failNextAck;
    private boolean throwRuntimeOnNextAck;
    private int failNextStartWithStaleCount;
    private int failNextStartWithInvalidCount;

    public FakeEngineHost() {
        this(System::nanoTime, DEFAULT_RETRY_DEADLINE_NANOS);
    }

    FakeEngineHost(LongSupplier monotonicNanos, long retryDeadlineNanos) {
        this.monotonicNanos = Objects.requireNonNull(monotonicNanos, "monotonicNanos");
        if (retryDeadlineNanos <= 0) {
            throw new IllegalArgumentException("retry deadline must be positive");
        }
        this.retryDeadlineNanos = retryDeadlineNanos;
    }

    @Override
    public synchronized HostEpoch hostEpoch() {
        return hostEpoch;
    }

    @Override
    public synchronized SessionStart startSession(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        InputContext context,
        int pageSize
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        requireCurrentEpoch(requestEpoch);
        if (sessionId == null) {
            throw new IllegalArgumentException("session ID must not be null");
        }
        if (context == null) {
            throw new IllegalArgumentException("input context must not be null");
        }
        if (pageSize < 1 || pageSize > 20) {
            throw failure(ErrorCode.INVALID_PAGE_SIZE);
        }
        if (failNextStartWithInvalidCount > 0) {
            failNextStartWithInvalidCount -= 1;
            throw failure(ErrorCode.INVALID_SESSION);
        }
        if (failNextStartWithStaleCount > 0) {
            failNextStartWithStaleCount -= 1;
            throw failure(ErrorCode.STALE_SESSION);
        }
        RequestFingerprint fingerprint = fingerprint(
            "START",
            -1,
            startArgument(context, pageSize)
        );
        SessionRecord existing = sessions.get(sessionId);
        if (existing != null) {
            if (!existing.hostEpoch.equals(requestEpoch)) {
                throw failure(ErrorCode.INVALID_SESSION);
            }
            if (existing.status == SessionStatus.INVALIDATED_BY_HOST) {
                throw failure(ErrorCode.STALE_SESSION);
            }
            if (existing.status != SessionStatus.ACTIVE) {
                throw failure(ErrorCode.INVALID_SESSION);
            }
            if (
                requestSeq == 1 &&
                existing.lastRequestSeq == 1 &&
                fingerprint.equals(existing.lastRequest) &&
                existing.cachedStart != null
            ) {
                return existing.cachedStart;
            }
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        if (requestSeq != 1) {
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        SessionRecord record = new SessionRecord(requestEpoch, sessionId, context, pageSize);
        record.lastRequestSeq = requestSeq;
        record.lastRequest = fingerprint;
        SessionStart started = new SessionStart(
            requestSeq,
            requestEpoch,
            sessionId,
            snapshot(record, false)
        );
        record.cachedStart = started;
        markResponseCached(record);
        sessions.put(sessionId, record);
        if (dropNextStartResponse) {
            dropNextStartResponse = false;
            throw new SimulatedStartResponseLoss();
        }
        return started;
    }

    @Override
    public synchronized Transition processKey(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision,
        String key
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        if (
            key == null ||
            key.codePointCount(0, key.length()) != 1 ||
            Character.isISOControl(key.codePointAt(0))
        ) {
            throw failure(ErrorCode.INVALID_KEY);
        }
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint("KEY", expectedRevision, key),
            record -> {
                record.composing += key;
                record.pageIndex = 0;
                record.allCandidates = candidatesFor(record.composing);
                return null;
            }
        );
    }

    @Override
    public synchronized Transition pageCandidates(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision,
        PageDirection direction
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        Objects.requireNonNull(direction, "direction");
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint("PAGE", expectedRevision, direction.name()),
            record -> {
                int target = record.pageIndex + (direction == PageDirection.NEXT ? 1 : -1);
                int pageCount = (record.allCandidates.size() + record.pageSize - 1) / record.pageSize;
                if (target < 0 || target >= pageCount) {
                    throw failure(ErrorCode.PAGE_OUT_OF_RANGE);
                }
                record.pageIndex = target;
                return null;
            }
        );
    }

    @Override
    public synchronized Transition selectCandidate(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision,
        String candidateId
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint("SELECT", expectedRevision, candidateId),
            record -> {
                Candidate selected = visibleCandidates(record).stream()
                    .filter(candidate -> candidate.id().equals(candidateId))
                    .findFirst()
                    .orElseThrow(() -> failure(ErrorCode.INVALID_CANDIDATE));
                clearComposition(record);
                record.commitMutationCount += 1;
                return selected.text();
            }
        );
    }

    @Override
    public synchronized Transition commitComposition(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint("COMMIT", expectedRevision, ""),
            record -> {
                if (record.composing.isEmpty()) {
                    throw failure(ErrorCode.NO_COMPOSITION);
                }
                String commit = record.allCandidates.isEmpty()
                    ? record.composing
                    : record.allCandidates.get(0).text();
                clearComposition(record);
                record.commitMutationCount += 1;
                return commit;
            }
        );
    }

    @Override
    public synchronized Transition cancelComposition(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint("CANCEL", expectedRevision, ""),
            record -> {
                if (record.composing.isEmpty()) {
                    throw failure(ErrorCode.NO_COMPOSITION);
                }
                clearComposition(record);
                return null;
            }
        );
    }

    @Override
    public synchronized Transition setOption(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        long expectedRevision,
        EngineOption option,
        boolean enabled
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        Objects.requireNonNull(option, "option");
        return mutate(
            requestEpoch,
            sessionId,
            requestSeq,
            fingerprint(
                "SET_OPTION",
                expectedRevision,
                option.name() + "=" + enabled
            ),
            record -> {
                record.options.put(option, enabled);
                return null;
            }
        );
    }

    @Override
    public synchronized State snapshot(HostEpoch requestEpoch, SessionId sessionId) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        return snapshot(active(requestEpoch, sessionId), false);
    }

    @Override
    public synchronized void acknowledgeResponse(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        SessionRecord record = active(requestEpoch, sessionId);
        if (failNextAck) {
            failNextAck = false;
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        if (throwRuntimeOnNextAck) {
            throwRuntimeOnNextAck = false;
            throw new IllegalStateException("synthetic transport acknowledgement failure");
        }
        if (requestSeq == record.lastAcknowledgedResponseSeq) {
            return;
        }
        boolean acknowledgesStart = record.cachedStart != null &&
            record.cachedStart.requestSeq() == requestSeq;
        boolean acknowledgesTransition = record.cachedTransition != null &&
            record.cachedTransition.requestSeq() == requestSeq;
        if (!acknowledgesStart && !acknowledgesTransition) {
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        record.cachedStart = null;
        record.cachedTransition = null;
        record.lastRequest = null;
        record.responseCachePending = false;
        record.lastAcknowledgedResponseSeq = requestSeq;
    }

    @Override
    public synchronized void endSession(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq
    ) {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
        requireCurrentEpoch(requestEpoch);
        SessionRecord record = sessions.get(sessionId);
        if (record == null) {
            throw failure(ErrorCode.INVALID_SESSION);
        }
        RequestFingerprint fingerprint = fingerprint("END", -1, "");
        if (record.status == SessionStatus.ENDED) {
            if (requestSeq == record.lastRequestSeq && fingerprint.equals(record.lastRequest)) {
                return;
            }
            throw failure(ErrorCode.SESSION_ENDED);
        }
        if (record.status == SessionStatus.INVALIDATED_BY_HOST) {
            throw failure(ErrorCode.STALE_SESSION);
        }
        if (failNextEnd) {
            failNextEnd = false;
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        if (throwRuntimeOnNextEnd) {
            throwRuntimeOnNextEnd = false;
            throw new IllegalStateException("synthetic transport end failure");
        }
        requireNextRequest(record, requestSeq, fingerprint);
        sanitize(record);
        record.status = SessionStatus.ENDED;
        record.lastRequestSeq = requestSeq;
        record.lastRequest = fingerprint;
        trimTombstones();
    }

    /** Simulates an external native host process restart in the same wrapper. */
    public synchronized void restartHost() {
        sessions.values().stream()
            .filter(record -> record.status == SessionStatus.ACTIVE)
            .forEach(record -> {
                sanitize(record);
                record.status = SessionStatus.INVALIDATED_BY_HOST;
            });
        Arrays.fill(fingerprintKey, (byte) 0);
        fingerprintKey = newFingerprintKey();
        hostEpoch = newHostEpoch();
        trimTombstones();
    }

    synchronized void debugDropNextStartResponse() {
        dropNextStartResponse = true;
    }

    synchronized void debugCorruptNextTransitionRevision() {
        corruptNextTransitionRevision = true;
    }

    synchronized void debugFailNextEnd() {
        failNextEnd = true;
    }

    synchronized void debugThrowRuntimeOnNextEnd() {
        throwRuntimeOnNextEnd = true;
    }

    synchronized void debugDropNextTransitionResponse() {
        dropNextTransitionResponse = true;
    }

    synchronized void debugFailNextAck() {
        failNextAck = true;
    }

    synchronized void debugThrowRuntimeOnNextAck() {
        throwRuntimeOnNextAck = true;
    }

    synchronized void debugFailNextStartsWithStale(int count) {
        if (count < 1) {
            throw new IllegalArgumentException("failure count must be positive");
        }
        failNextStartWithStaleCount = count;
    }

    synchronized void debugFailNextStartsWithInvalid(int count) {
        if (count < 1) {
            throw new IllegalArgumentException("failure count must be positive");
        }
        failNextStartWithInvalidCount = count;
    }

    synchronized int debugPendingStartStaleFailures() {
        return failNextStartWithStaleCount;
    }

    synchronized void expireResponseCaches() {
        expireResponseCachesLocked(monotonicNanos.getAsLong());
    }

    synchronized void debugInvalidateSessionWithoutEpochChange(SessionId sessionId) {
        SessionRecord record = sessions.get(sessionId);
        if (record == null || record.status != SessionStatus.ACTIVE) {
            throw failure(ErrorCode.INVALID_SESSION);
        }
        sanitize(record);
        record.status = SessionStatus.INVALIDATED_BY_HOST;
    }

    synchronized boolean debugResponseCacheCleared(SessionId sessionId) {
        SessionRecord record = sessions.get(sessionId);
        return record != null &&
            record.cachedStart == null &&
            record.cachedTransition == null &&
            record.lastRequest == null;
    }

    synchronized boolean debugRetainedFingerprintIsOpaque(SessionId sessionId) {
        SessionRecord record = sessions.get(sessionId);
        return record != null &&
            record.lastRequest != null &&
            record.lastRequest.argumentMac().matches("[0-9a-f]{64}");
    }

    synchronized boolean debugSessionSanitized(SessionId sessionId) {
        SessionRecord record = sessions.get(sessionId);
        boolean requestMetadataSanitized = record != null &&
            (record.lastRequest == null || (
                record.lastRequest.operation().equals("END")
            ));
        return record != null &&
            record.context == null &&
            record.composing.isEmpty() &&
            record.allCandidates.isEmpty() &&
            record.options.isEmpty() &&
            record.cachedTransition == null &&
            record.cachedStart == null &&
            requestMetadataSanitized;
    }

    synchronized int debugTombstoneCount() {
        return (int) sessions.values().stream()
            .filter(record -> record.status != SessionStatus.ACTIVE)
            .count();
    }

    static int debugTombstoneLimit() {
        return MAX_TOMBSTONES;
    }

    synchronized int debugCommitMutationCount(SessionId sessionId) {
        SessionRecord record = sessions.get(sessionId);
        if (record == null) {
            throw failure(ErrorCode.INVALID_SESSION);
        }
        return record.commitMutationCount;
    }

    synchronized InputContext debugContext(HostEpoch requestEpoch, SessionId sessionId) {
        return active(requestEpoch, sessionId).context;
    }

    synchronized boolean debugOption(
        HostEpoch requestEpoch,
        SessionId sessionId,
        EngineOption option
    ) {
        return active(requestEpoch, sessionId).options.getOrDefault(option, false);
    }

    private Transition mutate(
        HostEpoch requestEpoch,
        SessionId sessionId,
        long requestSeq,
        RequestFingerprint fingerprint,
        Change change
    ) {
        SessionRecord record = active(requestEpoch, sessionId);
        if (requestSeq == record.lastRequestSeq) {
            if (fingerprint.equals(record.lastRequest) && record.cachedTransition != null) {
                return record.cachedTransition;
            }
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        requireNextRequest(record, requestSeq, fingerprint);
        if (record.revision != fingerprint.expectedRevision()) {
            throw failure(ErrorCode.STALE_REVISION);
        }
        String commit = change.apply(record);
        record.revision += 1;
        State responseState = snapshot(record, true);
        if (corruptNextTransitionRevision) {
            corruptNextTransitionRevision = false;
            responseState = withRevision(responseState, responseState.revision() - 1);
        }
        Transition transition = new Transition(
            requestSeq,
            responseState,
            commit
        );
        record.lastRequestSeq = requestSeq;
        record.lastRequest = fingerprint;
        record.cachedTransition = transition;
        record.cachedStart = null;
        markResponseCached(record);
        if (dropNextTransitionResponse) {
            dropNextTransitionResponse = false;
            throw new SimulatedTransitionResponseLoss();
        }
        return transition;
    }

    private void requireNextRequest(
        SessionRecord record,
        long requestSeq,
        RequestFingerprint fingerprint
    ) {
        if (requestSeq <= 0 || requestSeq != record.lastRequestSeq + 1) {
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
        if (requestSeq == record.lastRequestSeq && !fingerprint.equals(record.lastRequest)) {
            throw failure(ErrorCode.OUT_OF_ORDER_REQUEST);
        }
    }

    private SessionRecord active(HostEpoch requestEpoch, SessionId sessionId) {
        requireCurrentEpoch(requestEpoch);
        SessionRecord record = sessions.get(sessionId);
        if (record == null) {
            throw failure(ErrorCode.INVALID_SESSION);
        }
        if (!record.hostEpoch.equals(requestEpoch)) {
            throw failure(ErrorCode.STALE_SESSION);
        }
        return switch (record.status) {
            case ACTIVE -> record;
            case ENDED -> throw failure(ErrorCode.SESSION_ENDED);
            case INVALIDATED_BY_HOST -> throw failure(ErrorCode.STALE_SESSION);
        };
    }

    private void requireCurrentEpoch(HostEpoch requestEpoch) {
        if (requestEpoch == null || !hostEpoch.equals(requestEpoch)) {
            throw failure(ErrorCode.STALE_SESSION);
        }
    }

    private List<Candidate> candidatesFor(String composing) {
        return lexicon.getOrDefault(composing, List.of()).stream()
            .map(template -> new Candidate(
                "candidate-" + UUID.randomUUID(),
                template.text(),
                template.comment(),
                CandidateSource.ENGINE
            ))
            .toList();
    }

    private static List<Candidate> visibleCandidates(SessionRecord record) {
        int start = record.pageIndex * record.pageSize;
        if (start >= record.allCandidates.size()) {
            return List.of();
        }
        return List.copyOf(
            record.allCandidates.subList(
                start,
                Math.min(start + record.pageSize, record.allCandidates.size())
            )
        );
    }

    private State snapshot(SessionRecord record, boolean handled) {
        List<CompositionSegment> segments = record.composing.isEmpty()
            ? List.of()
            : List.of(new CompositionSegment(
                0,
                record.composing.length(),
                SegmentKind.RAW
            ));
        EngineMode mode = record.composing.isEmpty()
            ? EngineMode.DIRECT
            : record.allCandidates.isEmpty()
                ? EngineMode.COMPOSING
                : EngineMode.SELECTING;
        return new State(
            hostEpoch,
            record.id,
            record.revision,
            handled,
            record.composing,
            record.composing.length(),
            segments,
            visibleCandidates(record),
            record.pageIndex,
            record.pageIndex > 0,
            (record.pageIndex + 1) * record.pageSize < record.allCandidates.size(),
            mode
        );
    }

    private static void clearComposition(SessionRecord record) {
        record.composing = "";
        record.pageIndex = 0;
        record.allCandidates = List.of();
    }

    private static State withRevision(State state, long revision) {
        return new State(
            state.hostEpoch(),
            state.sessionId(),
            revision,
            state.handled(),
            state.preedit(),
            state.caretUtf16(),
            state.segments(),
            state.candidates(),
            state.pageIndex(),
            state.hasPreviousPage(),
            state.hasNextPage(),
            state.mode()
        );
    }

    private static void sanitize(SessionRecord record) {
        clearComposition(record);
        record.context = null;
        record.options.clear();
        record.lastRequestSeq = 0;
        record.lastRequest = null;
        record.cachedTransition = null;
        record.cachedStart = null;
        record.responseCachePending = false;
        record.lastAcknowledgedResponseSeq = 0;
    }

    private void trimTombstones() {
        int tombstones = debugTombstoneCount();
        Iterator<Map.Entry<SessionId, SessionRecord>> iterator = sessions.entrySet().iterator();
        while (tombstones > MAX_TOMBSTONES && iterator.hasNext()) {
            Map.Entry<SessionId, SessionRecord> entry = iterator.next();
            if (entry.getValue().status != SessionStatus.ACTIVE) {
                iterator.remove();
                tombstones -= 1;
            }
        }
    }

    private static HostEpoch newHostEpoch() {
        return new HostEpoch("epoch-" + UUID.randomUUID());
    }

    private void markResponseCached(SessionRecord record) {
        record.cachedResponseStartedNanos = monotonicNanos.getAsLong();
        record.responseCachePending = true;
    }

    private void expireResponseCachesLocked(long now) {
        for (SessionRecord record : sessions.values()) {
            if (
                record.status == SessionStatus.ACTIVE &&
                record.responseCachePending &&
                now - record.cachedResponseStartedNanos >= retryDeadlineNanos
            ) {
                sanitize(record);
                record.status = SessionStatus.INVALIDATED_BY_HOST;
            }
        }
        trimTombstones();
    }

    private RequestFingerprint fingerprint(
        String operation,
        long expectedRevision,
        String argument
    ) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(fingerprintKey, "HmacSHA256"));
            mac.update(operation.getBytes(StandardCharsets.UTF_8));
            mac.update((byte) 0);
            mac.update(Long.toString(expectedRevision).getBytes(StandardCharsets.US_ASCII));
            mac.update((byte) 0);
            byte[] digest = mac.doFinal(argument.getBytes(StandardCharsets.UTF_8));
            return new RequestFingerprint(operation, expectedRevision, toHex(digest));
        } catch (GeneralSecurityException error) {
            throw new IllegalStateException("HMAC-SHA256 is unavailable", error);
        }
    }

    private static byte[] newFingerprintKey() {
        byte[] key = new byte[32];
        new SecureRandom().nextBytes(key);
        return key;
    }

    private static String toHex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(Character.forDigit((value >>> 4) & 0x0f, 16));
            result.append(Character.forDigit(value & 0x0f, 16));
        }
        Arrays.fill(bytes, (byte) 0);
        return result.toString();
    }

    private static String startArgument(InputContext context, int pageSize) {
        return pageSize + "|" +
            context.platform().name() + "|" +
            context.fieldKind().name() + "|" +
            context.action().name() + "|" +
            context.incognito() + "|" +
            context.learningAllowed() + "|" +
            context.clipvaultAllowed() + "|" +
            Objects.toString(context.appScope(), "");
    }

    private static ProtocolException failure(ErrorCode code) {
        return new ProtocolException(code);
    }
}
