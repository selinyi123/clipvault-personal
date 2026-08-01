package com.clipvault.poc.engine;

import static com.clipvault.poc.engine.AppliedResponseLedger.Reservation;
import static com.clipvault.poc.engine.AppliedResponseLedger.SessionKey;
import static com.clipvault.poc.engine.EngineSessionContract.Adapter;
import static com.clipvault.poc.engine.EngineSessionContract.Candidate;
import static com.clipvault.poc.engine.EngineSessionContract.EngineOption;
import static com.clipvault.poc.engine.EngineSessionContract.ErrorCode;
import static com.clipvault.poc.engine.EngineSessionContract.HostEpoch;
import static com.clipvault.poc.engine.EngineSessionContract.InputContext;
import static com.clipvault.poc.engine.EngineSessionContract.PageDirection;
import static com.clipvault.poc.engine.EngineSessionContract.ProtocolException;
import static com.clipvault.poc.engine.EngineSessionContract.SessionId;
import static com.clipvault.poc.engine.EngineSessionContract.SessionStart;
import static com.clipvault.poc.engine.EngineSessionContract.State;
import static com.clipvault.poc.engine.EngineSessionContract.Transition;

import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.function.Supplier;

/**
 * JVM-testable Android IME client seam. A production adapter can delegate
 * these three editor operations to android.view.inputmethod.InputConnection;
 * this isolated PoC deliberately has no Android SDK, JNI, persistence, log, or
 * network dependency.
 */
public final class AndroidImeSessionClient {
    public interface EditorConnection {
        EditorEffectResult setComposingText(String text, int caretUtf16);

        EditorEffectResult clearComposingText();

        EditorEffectResult commitText(String text);
    }

    public enum EditorEffectResult {
        APPLIED,
        REJECTED,
        AMBIGUOUS
    }

    public enum OutcomeStatus {
        APPLIED,
        NO_EDITOR_EFFECT,
        DUPLICATE_RESPONSE_IGNORED,
        STALE_RESPONSE_IGNORED,
        HOST_RESTART_RECOVERED_NO_REPLAY,
        NO_ACTIVE_SESSION,
        SESSION_RETIRED_EDITOR_REJECTED,
        SESSION_RETIRED_EDITOR_AMBIGUOUS,
        SESSION_RETIRED_RESPONSE_GAP,
        SESSION_RETIRED_PROTOCOL_MISMATCH,
        SESSION_RETIRED_RESPONSE_EXPIRED,
        SESSION_RETIRED_ACK_AMBIGUOUS
    }

    public record Outcome(OutcomeStatus status, Transition transition) {
        public Outcome {
            Objects.requireNonNull(status, "status");
        }
    }

    public record ClipVaultCandidate(String id, String text) {
        public ClipVaultCandidate {
            if (id == null || id.isBlank() || text == null) {
                throw new IllegalArgumentException("invalid ClipVault candidate");
            }
        }
    }

    /** Engine and ClipVault IDs remain in separate surface namespaces. */
    public record CandidateSurfaces(
        List<Candidate> engine,
        List<ClipVaultCandidate> clipVault
    ) {
        public CandidateSurfaces {
            engine = List.copyOf(engine);
            clipVault = List.copyOf(clipVault);
            if (
                clipVault.stream().map(ClipVaultCandidate::id).distinct().count() !=
                    clipVault.size()
            ) {
                throw new IllegalArgumentException(
                    "ClipVault candidate IDs must be unique within their surface"
                );
            }
        }
    }

    @FunctionalInterface
    private interface Request {
        Transition send(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision
        );
    }

    private final Adapter host;
    private final EditorConnection editor;
    private final AppliedResponseLedger ledger;
    private final int pageSize;

    private SessionKey activeKey;
    private SessionKey lastRetiredKey;
    private SessionKey pendingStartKey;
    private InputContext pendingStartContext;
    private InputContext inputContext;
    private State state;
    private long nextRequestSeq;

    public AndroidImeSessionClient(Adapter host, EditorConnection editor) {
        this(host, editor, new AppliedResponseLedger(), 2);
    }

    public AndroidImeSessionClient(
        Adapter host,
        EditorConnection editor,
        AppliedResponseLedger ledger,
        int pageSize
    ) {
        this.host = Objects.requireNonNull(host, "host");
        this.editor = Objects.requireNonNull(editor, "editor");
        this.ledger = Objects.requireNonNull(ledger, "ledger");
        if (pageSize < 1 || pageSize > 20) {
            throw new IllegalArgumentException("page size must be between 1 and 20");
        }
        this.pageSize = pageSize;
    }

    public synchronized Outcome startInput(InputContext context) {
        Objects.requireNonNull(context, "context");
        if (activeKey != null) {
            Outcome ended = endInput();
            if (
                ended.status() == OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS ||
                ended.status() == OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED
            ) {
                return ended;
            }
        }
        startFreshSession(context);
        return outcome(OutcomeStatus.NO_EDITOR_EFFECT, null);
    }

    public synchronized Outcome processKey(String key) {
        return dispatch((epoch, sessionId, requestSeq, revision) ->
            host.processKey(epoch, sessionId, requestSeq, revision, key)
        );
    }

    public synchronized Outcome pageCandidates(PageDirection direction) {
        Objects.requireNonNull(direction, "direction");
        return dispatch((epoch, sessionId, requestSeq, revision) ->
            host.pageCandidates(epoch, sessionId, requestSeq, revision, direction)
        );
    }

    public synchronized Outcome selectCandidate(String candidateId) {
        Objects.requireNonNull(candidateId, "candidateId");
        return dispatch((epoch, sessionId, requestSeq, revision) ->
            host.selectCandidate(epoch, sessionId, requestSeq, revision, candidateId)
        );
    }

    public synchronized Outcome commitComposition() {
        return dispatch(host::commitComposition);
    }

    public synchronized Outcome cancelComposition() {
        return dispatch(host::cancelComposition);
    }

    public synchronized Outcome setOption(EngineOption option, boolean enabled) {
        Objects.requireNonNull(option, "option");
        return dispatch((epoch, sessionId, requestSeq, revision) ->
            host.setOption(epoch, sessionId, requestSeq, revision, option, enabled)
        );
    }

    /**
     * Accepts a delivered response. This is public because a Binder/JNI
     * transport can deliver a cached response independently of request code.
     */
    public synchronized Outcome acceptTransition(Transition transition) {
        Objects.requireNonNull(transition, "transition");
        if (activeKey == null) {
            return outcome(OutcomeStatus.STALE_RESPONSE_IGNORED, transition);
        }
        SessionKey responseKey = keyOf(transition.state());
        if (!activeKey.equals(responseKey)) {
            return outcome(OutcomeStatus.STALE_RESPONSE_IGNORED, transition);
        }
        Reservation reservation = ledger.reserve(responseKey, transition.requestSeq());
        if (reservation == Reservation.DUPLICATE_OR_OLDER) {
            acknowledgeDuplicateBestEffort(transition);
            return outcome(OutcomeStatus.DUPLICATE_RESPONSE_IGNORED, transition);
        }
        if (reservation == Reservation.UNKNOWN_SESSION) {
            return outcome(OutcomeStatus.STALE_RESPONSE_IGNORED, transition);
        }
        if (reservation == Reservation.OUT_OF_ORDER_GAP) {
            return retireProtocolFailure(
                OutcomeStatus.SESSION_RETIRED_RESPONSE_GAP,
                transition
            );
        }
        if (transition.requestSeq() >= nextRequestSeq) {
            return retireProtocolFailure(
                OutcomeStatus.SESSION_RETIRED_PROTOCOL_MISMATCH,
                transition
            );
        }
        if (!isValidSuccessor(state, transition)) {
            return retireProtocolFailure(
                OutcomeStatus.SESSION_RETIRED_PROTOCOL_MISMATCH,
                transition
            );
        }

        EditorEffectResult editorResult = applyEditorEffect(state, transition);
        if (editorResult == null || editorResult == EditorEffectResult.APPLIED) {
            state = transition.state();
            OutcomeStatus acknowledgementStatus = acknowledgeApplied(transition);
            if (acknowledgementStatus != null) {
                return outcome(acknowledgementStatus, transition);
            }
            return outcome(
                editorResult == null
                    ? OutcomeStatus.NO_EDITOR_EFFECT
                    : OutcomeStatus.APPLIED,
                transition
            );
        }

        retireCurrentSessionBestEffort();
        return outcome(
            editorResult == EditorEffectResult.AMBIGUOUS
                ? OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS
                : OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED,
            transition
        );
    }

    /**
     * Clears stale composing state and opens a fresh client-created session.
     * The interrupted key/request is intentionally not replayed.
     */
    public synchronized Outcome recoverAfterHostRestart() {
        if (activeKey == null) {
            return outcome(OutcomeStatus.NO_ACTIVE_SESSION, null);
        }
        HostEpoch currentHostEpoch = host.hostEpoch();
        if (activeKey.hostEpoch().equals(currentHostEpoch)) {
            return outcome(OutcomeStatus.NO_EDITOR_EFFECT, null);
        }

        return recoverAfterSessionLoss();
    }

    public synchronized Outcome endInput() {
        if (activeKey == null) {
            return outcome(OutcomeStatus.NO_ACTIVE_SESSION, null);
        }
        SessionKey endingKey = activeKey;
        long endingSequence = nextRequestSeq;
        boolean hadComposingText = !state.preedit().isEmpty();
        RuntimeException failure = null;
        EditorEffectResult result = hadComposingText
            ? requireEditorResult(editor.clearComposingText())
            : null;
        try {
            host.endSession(
                endingKey.hostEpoch(),
                endingKey.sessionId(),
                endingSequence
            );
        } catch (RuntimeException error) {
            if (
                !(error instanceof ProtocolException protocolError) ||
                protocolError.code() != ErrorCode.STALE_SESSION
            ) {
                failure = error;
            }
        } finally {
            retireLocal();
        }
        if (result == EditorEffectResult.REJECTED) {
            return outcome(OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED, null);
        }
        if (result == EditorEffectResult.AMBIGUOUS) {
            return outcome(OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS, null);
        }
        if (failure != null) {
            throw failure;
        }
        if (result == null) {
            return outcome(OutcomeStatus.NO_EDITOR_EFFECT, null);
        }
        return outcome(
            switch (result) {
                case APPLIED -> OutcomeStatus.APPLIED;
                case REJECTED -> OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED;
                case AMBIGUOUS -> OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS;
            },
            null
        );
    }

    /**
     * Projects two strongly separated surfaces. In password/incognito mode
     * the ClipVault source is not invoked at all.
     */
    public synchronized CandidateSurfaces candidateSurfaces(
        Supplier<List<ClipVaultCandidate>> localClipVaultSource
    ) {
        if (activeKey == null || state == null) {
            return new CandidateSurfaces(List.of(), List.of());
        }
        List<Candidate> engine = state.candidates();
        if (!isClipVaultSurfaceAllowed()) {
            return new CandidateSurfaces(engine, List.of());
        }

        Objects.requireNonNull(localClipVaultSource, "localClipVaultSource");
        List<ClipVaultCandidate> localCandidates = List.copyOf(
            Objects.requireNonNull(localClipVaultSource.get(), "local candidates")
        );
        return new CandidateSurfaces(engine, localCandidates);
    }

    public synchronized boolean hasActiveSession() {
        return activeKey != null;
    }

    public synchronized SessionKey activeSessionKey() {
        return activeKey;
    }

    public synchronized SessionKey lastRetiredSessionKey() {
        return lastRetiredKey;
    }

    synchronized SessionKey pendingStartSessionKey() {
        return pendingStartKey;
    }

    public synchronized State state() {
        return state;
    }

    public synchronized int liveLedgerSessions() {
        return ledger.liveSessionCount();
    }

    public synchronized long nextRequestSequence() {
        return nextRequestSeq;
    }

    public synchronized boolean clipVaultSurfaceAllowed() {
        return activeKey != null && isClipVaultSurfaceAllowed();
    }

    private Outcome dispatch(Request request) {
        if (activeKey == null) {
            return outcome(OutcomeStatus.NO_ACTIVE_SESSION, null);
        }
        if (!activeKey.hostEpoch().equals(host.hostEpoch())) {
            return recoverAfterHostRestart();
        }

        long requestSeq = nextRequestSeq;
        Transition transition;
        try {
            transition = request.send(
                activeKey.hostEpoch(),
                activeKey.sessionId(),
                requestSeq,
                state.revision()
            );
        } catch (ProtocolException error) {
            if (isRecoverableSessionLoss(error)) {
                return recoverAfterSessionLoss();
            }
            throw error;
        }
        nextRequestSeq = requestSeq + 1;
        if (transition.requestSeq() != requestSeq) {
            return retireProtocolFailure(
                OutcomeStatus.SESSION_RETIRED_PROTOCOL_MISMATCH,
                transition
            );
        }
        return acceptTransition(transition);
    }

    private void startFreshSession(InputContext context) {
        for (int attempt = 0; attempt < 2; attempt++) {
            HostEpoch epoch = host.hostEpoch();
            if (pendingStartKey != null && !pendingStartKey.hostEpoch().equals(epoch)) {
                clearPendingStart();
            }
            if (pendingStartKey != null && !Objects.equals(pendingStartContext, context)) {
                retirePendingStartBestEffort();
            }
            if (pendingStartKey == null) {
                pendingStartKey = new SessionKey(
                    epoch,
                    new SessionId("session-" + UUID.randomUUID())
                );
                pendingStartContext = context;
            }
            SessionId sessionId = pendingStartKey.sessionId();
            SessionStart started;
            try {
                started = host.startSession(epoch, sessionId, 1, context, pageSize);
            } catch (ProtocolException error) {
                if (!isRecoverableSessionLoss(error)) {
                    throw error;
                }
                clearPendingStart();
                if (attempt == 0) {
                    continue;
                }
                throw error;
            }
            if (
                started.requestSeq() != 1 ||
                !epoch.equals(started.hostEpoch()) ||
                !sessionId.equals(started.sessionId())
            ) {
                throw new IllegalStateException("host returned a mismatched session start");
            }
            activeKey = new SessionKey(epoch, sessionId);
            inputContext = context;
            state = started.state();
            nextRequestSeq = 2;
            ledger.open(activeKey, started.requestSeq());
            clearPendingStart();
            host.acknowledgeResponse(epoch, sessionId, started.requestSeq());
            return;
        }
        throw new IllegalStateException("bounded session start exhausted without a result");
    }

    private Outcome recoverAfterSessionLoss() {
        InputContext retainedContext = inputContext;
        boolean hadComposingText = state != null && !state.preedit().isEmpty();
        retireLocal();
        if (hadComposingText) {
            EditorEffectResult result = requireEditorResult(editor.clearComposingText());
            if (result != EditorEffectResult.APPLIED) {
                return outcome(
                    result == EditorEffectResult.AMBIGUOUS
                        ? OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS
                        : OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED,
                    null
                );
            }
        }
        startFreshSession(retainedContext);
        return outcome(OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY, null);
    }

    private boolean isValidSuccessor(State previous, Transition transition) {
        State next = transition.state();
        if (previous == null || next.revision() != previous.revision() + 1) {
            return false;
        }
        if (transition.commitText() == null) {
            return true;
        }
        return !transition.commitText().isEmpty() &&
            next.preedit().isEmpty() &&
            next.candidates().isEmpty() &&
            next.segments().isEmpty() &&
            next.caretUtf16() == 0 &&
            next.pageIndex() == 0 &&
            !next.hasPreviousPage() &&
            !next.hasNextPage() &&
            next.mode() == EngineSessionContract.EngineMode.DIRECT;
    }

    private OutcomeStatus acknowledgeApplied(Transition transition) {
        try {
            host.acknowledgeResponse(
                activeKey.hostEpoch(),
                activeKey.sessionId(),
                transition.requestSeq()
            );
            return null;
        } catch (RuntimeException error) {
            OutcomeStatus retiredStatus =
                error instanceof ProtocolException protocolError &&
                    protocolError.code() == ErrorCode.STALE_SESSION
                    ? OutcomeStatus.SESSION_RETIRED_RESPONSE_EXPIRED
                    : OutcomeStatus.SESSION_RETIRED_ACK_AMBIGUOUS;
            boolean hasComposingProjection = transition.commitText() == null &&
                state != null &&
                !state.preedit().isEmpty();
            retireCurrentSessionBestEffort();
            if (!hasComposingProjection) {
                return retiredStatus;
            }
            EditorEffectResult result;
            try {
                result = requireEditorResult(editor.clearComposingText());
            } catch (RuntimeException clearFailure) {
                return OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS;
            }
            return switch (result) {
                case APPLIED -> retiredStatus;
                case REJECTED -> OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED;
                case AMBIGUOUS -> OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS;
            };
        }
    }

    private boolean isRecoverableSessionLoss(ProtocolException error) {
        return error.code() == ErrorCode.STALE_SESSION ||
            error.code() == ErrorCode.INVALID_SESSION;
    }

    private Outcome retireProtocolFailure(
        OutcomeStatus protocolStatus,
        Transition transition
    ) {
        boolean hadComposingText = state != null && !state.preedit().isEmpty();
        if (!hadComposingText) {
            retireCurrentSessionBestEffort();
            return outcome(protocolStatus, transition);
        }
        EditorEffectResult result = requireEditorResult(editor.clearComposingText());
        retireCurrentSessionBestEffort();
        return switch (result) {
            case APPLIED -> outcome(protocolStatus, transition);
            case REJECTED -> outcome(
                OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED,
                transition
            );
            case AMBIGUOUS -> outcome(
                OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS,
                transition
            );
        };
    }

    private void acknowledgeDuplicateBestEffort(Transition transition) {
        try {
            host.acknowledgeResponse(
                activeKey.hostEpoch(),
                activeKey.sessionId(),
                transition.requestSeq()
            );
        } catch (RuntimeException ignored) {
            // An older response may already have been acknowledged and evicted.
        }
    }

    private void retirePendingStartBestEffort() {
        if (pendingStartKey == null) {
            return;
        }
        try {
            host.endSession(
                pendingStartKey.hostEpoch(),
                pendingStartKey.sessionId(),
                2
            );
        } catch (RuntimeException ignored) {
            // The request may never have reached the Host.
        } finally {
            clearPendingStart();
        }
    }

    private void clearPendingStart() {
        pendingStartKey = null;
        pendingStartContext = null;
    }

    private EditorEffectResult applyEditorEffect(State previous, Transition transition) {
        State next = transition.state();
        if (transition.commitText() != null) {
            return requireEditorResult(editor.commitText(transition.commitText()));
        }
        if (
            previous == null ||
            !previous.preedit().equals(next.preedit()) ||
            previous.caretUtf16() != next.caretUtf16()
        ) {
            if (next.preedit().isEmpty()) {
                return requireEditorResult(editor.clearComposingText());
            }
            return requireEditorResult(
                editor.setComposingText(next.preedit(), next.caretUtf16())
            );
        }
        return null;
    }

    private void retireCurrentSessionBestEffort() {
        if (activeKey == null) {
            return;
        }
        SessionKey retiringKey = activeKey;
        long terminalSequence = nextRequestSeq;
        retireLocal();
        try {
            host.endSession(
                retiringKey.hostEpoch(),
                retiringKey.sessionId(),
                terminalSequence
            );
        } catch (RuntimeException ignored) {
            // Local retirement is authoritative. A production host still needs
            // authenticated acknowledgement or a bounded response deadline.
        }
    }

    private void retireLocal() {
        if (activeKey != null) {
            lastRetiredKey = activeKey;
            ledger.close(activeKey);
        }
        activeKey = null;
        inputContext = null;
        state = null;
        nextRequestSeq = 0;
    }

    private boolean isClipVaultSurfaceAllowed() {
        return inputContext != null &&
            inputContext.clipvaultAllowed() &&
            !inputContext.incognito() &&
            inputContext.fieldKind() != EngineSessionContract.FieldKind.PASSWORD;
    }

    private static SessionKey keyOf(State state) {
        return new SessionKey(state.hostEpoch(), state.sessionId());
    }

    private static EditorEffectResult requireEditorResult(EditorEffectResult result) {
        return Objects.requireNonNull(result, "editor result");
    }

    private static Outcome outcome(OutcomeStatus status, Transition transition) {
        return new Outcome(status, transition);
    }
}
