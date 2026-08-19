package com.clipvault.poc.engine;

import static com.clipvault.poc.engine.AndroidImeSessionClient.EditorEffectResult;
import static com.clipvault.poc.engine.AndroidImeSessionClient.ClipVaultCandidate;
import static com.clipvault.poc.engine.AndroidImeSessionClient.Outcome;
import static com.clipvault.poc.engine.AndroidImeSessionClient.OutcomeStatus;
import static com.clipvault.poc.engine.AppliedResponseLedger.SessionKey;
import static com.clipvault.poc.engine.EngineSessionContract.Candidate;
import static com.clipvault.poc.engine.EngineSessionContract.EditorAction;
import static com.clipvault.poc.engine.EngineSessionContract.EngineOption;
import static com.clipvault.poc.engine.EngineSessionContract.ErrorCode;
import static com.clipvault.poc.engine.EngineSessionContract.FieldKind;
import static com.clipvault.poc.engine.EngineSessionContract.HostEpoch;
import static com.clipvault.poc.engine.EngineSessionContract.InputContext;
import static com.clipvault.poc.engine.EngineSessionContract.PageDirection;
import static com.clipvault.poc.engine.EngineSessionContract.Platform;
import static com.clipvault.poc.engine.EngineSessionContract.ProtocolException;
import static com.clipvault.poc.engine.EngineSessionContract.SessionId;
import static com.clipvault.poc.engine.EngineSessionContract.SessionStart;
import static com.clipvault.poc.engine.EngineSessionContract.Transition;

import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.OptionalLong;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public final class AndroidImeSliceRunner {
    private static final class FakeEditorConnection
        implements AndroidImeSessionClient.EditorConnection {
        private EditorEffectResult nextCommit = EditorEffectResult.APPLIED;
        private EditorEffectResult nextClear = EditorEffectResult.APPLIED;
        private String composing = "";
        private int caretUtf16;
        private final StringBuilder committed = new StringBuilder();
        private int setCalls;
        private int clearCalls;
        private int commitCalls;
        private Runnable nextAfterEffect;

        private void runAfterEffect() {
            Runnable callback = nextAfterEffect;
            nextAfterEffect = null;
            if (callback != null) {
                callback.run();
            }
        }

        @Override
        public EditorEffectResult setComposingText(String text, int caret) {
            setCalls += 1;
            composing = text;
            caretUtf16 = caret;
            runAfterEffect();
            return EditorEffectResult.APPLIED;
        }

        @Override
        public EditorEffectResult clearComposingText() {
            clearCalls += 1;
            EditorEffectResult result = nextClear;
            nextClear = EditorEffectResult.APPLIED;
            if (result == EditorEffectResult.APPLIED) {
                composing = "";
                caretUtf16 = 0;
            }
            runAfterEffect();
            return result;
        }

        @Override
        public EditorEffectResult commitText(String text) {
            commitCalls += 1;
            EditorEffectResult result = nextCommit;
            nextCommit = EditorEffectResult.APPLIED;
            if (result == EditorEffectResult.APPLIED) {
                committed.append(text);
                composing = "";
                caretUtf16 = 0;
            }
            runAfterEffect();
            return result;
        }
    }

    private static final class FakeNanoClock implements java.util.function.LongSupplier {
        private long now;

        private FakeNanoClock() {}

        private FakeNanoClock(long now) {
            this.now = now;
        }

        @Override
        public long getAsLong() {
            return now;
        }

        private void advance(long nanos) {
            now += nanos;
        }
    }

    private AndroidImeSliceRunner() {}

    public static void main(String[] args) throws IOException {
        Map<String, Runnable> scenarios = new LinkedHashMap<>();
        scenarios.put("selection_commit", AndroidImeSliceRunner::eng2V001SelectionCommit);
        scenarios.put("paging_stale_ids", AndroidImeSliceRunner::eng2V002PagingAndStaleRejection);
        scenarios.put("response_ledger", AndroidImeSliceRunner::eng2V003AppliedResponseLedger);
        scenarios.put("ambiguous_editor", AndroidImeSliceRunner::eng2V004AmbiguousEditorRetiresSession);
        scenarios.put("session_loss", AndroidImeSliceRunner::eng2V005HostRestartRecoveryWithoutReplay);
        scenarios.put("utf16_projection", AndroidImeSliceRunner::eng2V006Utf16Projection);
        scenarios.put("privacy_surfaces", AndroidImeSliceRunner::eng2V007PrivacyCandidateGate);
        scenarios.put("ack_cleanup_bounds", AndroidImeSliceRunner::eng2V008CleanupAndBounds);
        runCanonicalManifest(scenarios);
        startSequenceAndIdempotency();
        setOptionWhitelist();
        System.out.println(
            "ANDROID IME SLICE PASSED: 8 canonical semantics + START_SEQ + SET_OPTION"
        );
    }

    private static void runCanonicalManifest(Map<String, Runnable> scenarios)
        throws IOException {
        String configured = System.getProperty("clipvault.androidImeSliceVectors");
        String assertionsConfigured = System.getProperty(
            "clipvault.foundationEngineAssertions"
        );
        check(configured != null, "clipvault.androidImeSliceVectors is required");
        check(assertionsConfigured != null, "clipvault.foundationEngineAssertions is required");
        Map<String, List<String>> canonicalAssertions = loadCanonicalAssertions(
            Path.of(assertionsConfigured)
        );
        List<String> lines = Files.readAllLines(Path.of(configured), StandardCharsets.UTF_8);
        List<String> expectedIds = List.of(
            "ENG2-V001", "ENG2-V002", "ENG2-V003", "ENG2-V004",
            "ENG2-V005", "ENG2-V006", "ENG2-V007", "ENG2-V008"
        );
        List<String> observedIds = new java.util.ArrayList<>();
        for (String raw : lines) {
            String line = raw.strip();
            if (
                line.isEmpty() ||
                line.startsWith("#") ||
                line.equals("semantic_id\tscenario\tassertion_ids")
            ) {
                continue;
            }
            String[] columns = line.split("\t", -1);
            check(columns.length == 3, "invalid Android semantic manifest row");
            Runnable scenario = scenarios.get(columns[1]);
            check(scenario != null, "unknown Android semantic scenario: " + columns[1]);
            check(!observedIds.contains(columns[0]), "duplicate Android semantic ID");
            check(
                List.of(columns[2].split(",", -1)).equals(canonicalAssertions.get(columns[0])),
                "Android semantic assertion mapping drifted: " + columns[0]
            );
            observedIds.add(columns[0]);
            scenario.run();
            System.out.println(columns[0] + " PASSED (" + columns[1] + ")");
        }
        check(observedIds.equals(expectedIds), "canonical Android semantic IDs/order mismatch");
        check(observedIds.size() == scenarios.size(), "unmapped Android semantic scenario");
        check(observedIds.equals(List.copyOf(canonicalAssertions.keySet())),
            "foundation assertion semantic order mismatch");
    }

    private static Map<String, List<String>> loadCanonicalAssertions(Path path)
        throws IOException {
        Map<String, List<String>> result = new LinkedHashMap<>();
        boolean sawHeader = false;
        for (String raw : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String line = raw.strip();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            if (!sawHeader) {
                check(
                    line.equals("semantic_id\tassertion_id\tassertion"),
                    "invalid foundation assertion header"
                );
                sawHeader = true;
                continue;
            }
            String[] columns = line.split("\t", -1);
            check(columns.length == 3 && !columns[2].isBlank(),
                "invalid foundation assertion row");
            List<String> ids = result.computeIfAbsent(
                columns[0],
                ignored -> new java.util.ArrayList<>()
            );
            check(!ids.contains(columns[1]), "duplicate foundation assertion ID");
            ids.add(columns[1]);
        }
        check(sawHeader && !result.isEmpty(), "foundation assertions are empty");
        return result;
    }

    private static void eng2V001SelectionCommit() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);

        client.startInput(InputContext.androidText());
        check(client.state().revision() == 0, "ENG2-V001 start revision");
        for (int codePoint : "nihao".codePoints().toArray()) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        Candidate selected = client.state().candidates().get(0);
        Outcome outcome = client.selectCandidate(selected.id());
        check(outcome.status() == OutcomeStatus.APPLIED, "ENG2-V001 commit application");
        check(editor.commitCalls == 1, "ENG2-V001 exactly-once commit");
        check(client.state().preedit().isEmpty(), "ENG2-V001 preedit clear");
        check(client.state().candidates().isEmpty(), "ENG2-V001 candidate clear");

        FakeEngineHost lossyHost = new FakeEngineHost();
        AndroidImeSessionClient lossyClient = new AndroidImeSessionClient(
            lossyHost,
            new FakeEditorConnection()
        );
        lossyHost.debugDropNextStartResponse();
        try {
            lossyClient.startInput(InputContext.androidText());
            throw new IllegalStateException("ENG2-V001 synthetic Start loss did not occur");
        } catch (FakeEngineHost.SimulatedStartResponseLoss expected) {
            // Retry below must preserve the exact client-created identity.
        }
        SessionKey pending = lossyClient.pendingStartSessionKey();
        check(pending != null, "ENG2-V001 pending Start identity retained");
        lossyClient.startInput(InputContext.androidText());
        check(
            pending.equals(lossyClient.activeSessionKey()),
            "ENG2-V001 Start retry uses same identity"
        );
    }

    private static void eng2V002PagingAndStaleRejection() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        for (int codePoint : "nihao".codePoints().toArray()) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        List<String> firstPageIds = client.state().candidates().stream()
            .map(Candidate::id)
            .toList();
        long firstRevision = client.state().revision();
        client.pageCandidates(PageDirection.NEXT);
        check(client.state().revision() == firstRevision + 1, "ENG2-V002 paging revision");
        client.pageCandidates(PageDirection.PREVIOUS);
        check(
            client.state().candidates().stream().map(Candidate::id).toList().equals(firstPageIds),
            "ENG2-V002 stable candidate identity"
        );

        SessionKey key = client.activeSessionKey();
        int editorEffects = editor.setCalls + editor.clearCalls + editor.commitCalls;
        expectProtocol(
            ErrorCode.STALE_REVISION,
            () -> host.pageCandidates(
                key.hostEpoch(),
                key.sessionId(),
                client.nextRequestSequence(),
                client.state().revision() - 1,
                PageDirection.NEXT
            ),
            "ENG2-V002 stale revision"
        );
        expectProtocol(
            ErrorCode.INVALID_CANDIDATE,
            () -> client.selectCandidate("candidate-missing"),
            "ENG2-V002 invalid candidate"
        );
        check(
            editorEffects == editor.setCalls + editor.clearCalls + editor.commitCalls,
            "ENG2-V002 rejected requests have no editor effect"
        );
    }

    private static void eng2V003AppliedResponseLedger() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AppliedResponseLedger ledger = new AppliedResponseLedger();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor, ledger, 2);
        client.startInput(InputContext.androidText());
        check(client.nextRequestSequence() == 2, "ENG2-V003 start consumes sequence one");
        int[] codePoints = "zhongguo".codePoints().toArray();
        for (int codePoint : codePoints) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        check(
            ledger.highestApplied(client.activeSessionKey()).orElseThrow() ==
                codePoints.length + 1,
            "ENG2-V003 synchronous dispatch remains contiguous"
        );
        Outcome committed = client.commitComposition();
        Transition cached = committed.transition();
        check(editor.commitCalls == 1, "ENG2-V003 first response projected");
        check(
            client.acceptTransition(cached).status() ==
                OutcomeStatus.DUPLICATE_RESPONSE_IGNORED,
            "ENG2-V003 duplicate response ignored"
        );
        check(editor.commitCalls == 1, "ENG2-V003 duplicate has no editor effect");
        OptionalLong highest = ledger.highestApplied(client.activeSessionKey());
        check(
            highest.isPresent() && highest.getAsLong() == cached.requestSeq(),
            "ENG2-V003 monotonic high-water"
        );
        check(client.liveLedgerSessions() == 1, "ENG2-V003 one live ledger entry");
        client.endInput();
        check(client.liveLedgerSessions() == 0, "ENG2-V003 ledger removed on end");

        AppliedResponseLedger gapLedger = new AppliedResponseLedger();
        SessionKey gapKey = new SessionKey(
            new HostEpoch("epoch-gap"),
            new SessionId("session-gap")
        );
        gapLedger.open(gapKey, 1);
        check(
            gapLedger.reserve(gapKey, 3) ==
                AppliedResponseLedger.Reservation.OUT_OF_ORDER_GAP,
            "ENG2-V003 ledger rejects a response gap"
        );
        check(
            gapLedger.highestApplied(gapKey).orElseThrow() == 1,
            "ENG2-V003 gap does not advance high-water"
        );

        FakeEngineHost gapHost = new FakeEngineHost();
        FakeEditorConnection gapEditor = new FakeEditorConnection();
        AndroidImeSessionClient gapClient = new AndroidImeSessionClient(
            gapHost,
            gapEditor
        );
        gapClient.startInput(InputContext.androidText());
        gapClient.processKey("n");
        int gapClearsBefore = gapEditor.clearCalls;
        Transition outOfOrder = new Transition(4, gapClient.state(), null);
        check(
            gapClient.acceptTransition(outOfOrder).status() ==
                OutcomeStatus.SESSION_RETIRED_RESPONSE_GAP,
            "ENG2-V003 client retires on response gap"
        );
        check(!gapClient.hasActiveSession(), "ENG2-V003 gap retirement");
        check(
            gapEditor.clearCalls == gapClearsBefore + 1,
            "ENG2-V003 response gap clears editor composition"
        );

        FakeEngineHost malformedHost = new FakeEngineHost();
        FakeEditorConnection malformedEditor = new FakeEditorConnection();
        AndroidImeSessionClient malformedClient = new AndroidImeSessionClient(
            malformedHost,
            malformedEditor
        );
        malformedClient.startInput(InputContext.androidText());
        malformedClient.processKey("n");
        malformedHost.debugCorruptNextTransitionRevision();
        check(
            malformedClient.processKey("i").status() ==
                OutcomeStatus.SESSION_RETIRED_PROTOCOL_MISMATCH,
            "ENG2-V003 contiguous malformed revision retires"
        );
        check(!malformedClient.hasActiveSession(), "ENG2-V003 malformed retirement");
        check(malformedEditor.setCalls == 1, "ENG2-V003 malformed response not projected");
        check(malformedEditor.clearCalls == 1, "ENG2-V003 malformed response clears preedit");
    }

    private static void eng2V004AmbiguousEditorRetiresSession() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        for (int codePoint : "zhongguo".codePoints().toArray()) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        SessionKey oldKey = client.activeSessionKey();
        editor.nextCommit = EditorEffectResult.AMBIGUOUS;
        Outcome ambiguous = client.commitComposition();
        check(
            ambiguous.status() == OutcomeStatus.SESSION_RETIRED_EDITOR_AMBIGUOUS,
            "ENG2-V004 ambiguous result"
        );
        check(!client.hasActiveSession(), "ENG2-V004 client session retired");
        check(client.liveLedgerSessions() == 0, "ENG2-V004 ledger retired");
        check(editor.commitCalls == 1, "ENG2-V004 no blind retry");
        check(host.debugSessionSanitized(oldKey.sessionId()), "ENG2-V004 host sanitized");
        check(
            client.acceptTransition(ambiguous.transition()).status() ==
                OutcomeStatus.STALE_RESPONSE_IGNORED,
            "ENG2-V004 old response ignored"
        );
        check(editor.commitCalls == 1, "ENG2-V004 old response has no effect");

        FakeEditorConnection rejectingEditor = new FakeEditorConnection();
        AndroidImeSessionClient rejectingClient = new AndroidImeSessionClient(
            new FakeEngineHost(),
            rejectingEditor
        );
        rejectingClient.startInput(InputContext.androidText());
        rejectingClient.processKey("n");
        rejectingEditor.nextClear = EditorEffectResult.REJECTED;
        check(
            rejectingClient.startInput(InputContext.androidText()).status() ==
                OutcomeStatus.SESSION_RETIRED_EDITOR_REJECTED,
            "ENG2-V004 rejected cleanup blocks replacement session"
        );
        check(
            !rejectingClient.hasActiveSession(),
            "ENG2-V004 rejected cleanup leaves no active session"
        );
    }

    private static void eng2V005HostRestartRecoveryWithoutReplay() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        for (int codePoint : "nihao".codePoints().toArray()) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        String oldCandidateId = client.state().candidates().get(0).id();
        SessionKey oldKey = client.activeSessionKey();
        host.restartHost();

        Outcome recovered = client.processKey("i");
        check(
            recovered.status() == OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY,
            "ENG2-V005 restart recovery"
        );
        check(client.hasActiveSession(), "ENG2-V005 fresh session active");
        check(!oldKey.equals(client.activeSessionKey()), "ENG2-V005 fresh identity");
        check(client.state().preedit().isEmpty(), "ENG2-V005 interrupted key not replayed");
        check(editor.clearCalls == 1, "ENG2-V005 stale composition cleared");
        check(client.liveLedgerSessions() == 1, "ENG2-V005 old ledger removed");
        expectProtocol(
            ErrorCode.INVALID_CANDIDATE,
            () -> client.selectCandidate(oldCandidateId),
            "ENG2-V005 old candidate rejected"
        );
        client.processKey("i");
        check(client.state().preedit().equals("i"), "ENG2-V005 new input starts fresh");

        FakeEngineHost reconnectHost = new FakeEngineHost();
        FakeEditorConnection reconnectEditor = new FakeEditorConnection();
        AndroidImeSessionClient reconnectClient = new AndroidImeSessionClient(
            reconnectHost,
            reconnectEditor
        );
        reconnectClient.startInput(InputContext.androidText());
        reconnectClient.processKey("n");
        SessionKey lostKey = reconnectClient.activeSessionKey();
        reconnectHost.debugInvalidateSessionWithoutEpochChange(lostKey.sessionId());
        Outcome reconnected = reconnectClient.processKey("i");
        check(
            reconnected.status() == OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY,
            "ENG2-V005 same-epoch transport reconnect recovery"
        );
        check(
            !lostKey.equals(reconnectClient.activeSessionKey()),
            "ENG2-V005 reconnect gets fresh session"
        );
        check(reconnectClient.state().preedit().isEmpty(), "ENG2-V005 reconnect no replay");
    }

    private static void eng2V006Utf16Projection() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        client.processKey("😀");
        check(client.state().caretUtf16() == 2, "ENG2-V006 state UTF-16 caret");
        check(editor.caretUtf16 == 2, "ENG2-V006 editor UTF-16 caret");
        check(
            client.state().segments().size() == 1 &&
                client.state().segments().get(0).endUtf16() == 2,
            "ENG2-V006 UTF-16 segment"
        );
    }

    private static void eng2V007PrivacyCandidateGate() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        ClipVaultCandidate local = new ClipVaultCandidate(
            "clipvault-synthetic",
            "synthetic-local-candidate"
        );
        int[] queries = {0};

        InputContext password = new InputContext(
            Platform.ANDROID,
            FieldKind.PASSWORD,
            EditorAction.NONE,
            false,
            true,
            true,
            null
        );
        client.startInput(password);
        check(!client.clipVaultSurfaceAllowed(), "ENG2-V007 password hard gate");
        check(
            client.candidateSurfaces(() -> {
                queries[0] += 1;
                return List.of(local);
            }).clipVault().isEmpty(),
            "ENG2-V007 password surface"
        );
        check(queries[0] == 0, "ENG2-V007 password source not queried");
        client.endInput();

        InputContext incognito = new InputContext(
            Platform.ANDROID,
            FieldKind.TEXT,
            EditorAction.NONE,
            true,
            true,
            true,
            null
        );
        client.startInput(incognito);
        check(client.candidateSurfaces(() -> {
            queries[0] += 1;
            return List.of(local);
        }).clipVault().isEmpty(), "ENG2-V007 incognito surface");
        check(queries[0] == 0, "ENG2-V007 incognito source not queried");
        check(!client.clipVaultSurfaceAllowed(), "ENG2-V007 incognito hard gate");
        client.endInput();

        client.startInput(InputContext.androidText());
        check(
            client.candidateSurfaces(() -> {
                queries[0] += 1;
                return List.of(local);
            }).clipVault().equals(List.of(local)),
            "ENG2-V007 normal local surface"
        );
        check(queries[0] == 1, "ENG2-V007 normal source queried once");
    }

    private static void eng2V008CleanupAndBounds() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        client.processKey("n");
        SessionId first = client.activeSessionKey().sessionId();
        check(host.debugResponseCacheCleared(first), "ENG2-V008 key response acknowledged");
        for (int codePoint : "ihao".codePoints().toArray()) {
            client.processKey(new String(Character.toChars(codePoint)));
        }
        client.commitComposition();
        check(host.debugResponseCacheCleared(first), "ENG2-V008 commit response acknowledged");
        client.endInput();
        check(host.debugSessionSanitized(first), "ENG2-V008 content wiped");
        check(client.liveLedgerSessions() == 0, "ENG2-V008 ledger removed");

        for (int index = 0; index < 80; index++) {
            client.startInput(InputContext.androidText());
            check(client.liveLedgerSessions() == 1, "ENG2-V008 live-session bound");
            client.endInput();
        }
        check(
            host.debugTombstoneCount() <= FakeEngineHost.debugTombstoneLimit(),
            "ENG2-V008 tombstone bound"
        );

        FakeNanoClock clock = new FakeNanoClock(-100);
        FakeEngineHost deadlineHost = new FakeEngineHost(clock, 10);
        HostEpoch epoch = deadlineHost.hostEpoch();
        SessionId idle = new SessionId("session-retry-deadline");
        deadlineHost.startSession(epoch, idle, 1, InputContext.androidText());
        deadlineHost.processKey(epoch, idle, 2, 0, "n");
        check(!deadlineHost.debugResponseCacheCleared(idle), "ENG2-V008 retry cache retained");
        check(
            deadlineHost.debugRetainedFingerprintIsOpaque(idle),
            "ENG2-V008 retry fingerprint is keyed opaque metadata"
        );
        clock.advance(11);
        deadlineHost.expireResponseCaches();
        check(deadlineHost.debugResponseCacheCleared(idle), "ENG2-V008 deadline evicts cache");

        FakeNanoClock lostStartClock = new FakeNanoClock();
        FakeEngineHost lostStartHost = new FakeEngineHost(lostStartClock, 10);
        AndroidImeSessionClient lostStartClient = new AndroidImeSessionClient(
            lostStartHost,
            new FakeEditorConnection()
        );
        lostStartHost.debugDropNextStartResponse();
        try {
            lostStartClient.startInput(InputContext.androidText());
            throw new IllegalStateException("ENG2-V008 lost Start response missing");
        } catch (FakeEngineHost.SimulatedStartResponseLoss expected) {
            // Deadline below must retire the Host session and permit a fresh ID.
        }
        SessionKey expiredPending = lostStartClient.pendingStartSessionKey();
        lostStartClock.advance(11);
        lostStartHost.expireResponseCaches();
        lostStartClient.startInput(InputContext.androidText());
        check(
            !expiredPending.equals(lostStartClient.activeSessionKey()),
            "ENG2-V008 expired Start retry opens fresh identity"
        );

        FakeNanoClock churnedStartClock = new FakeNanoClock();
        FakeEngineHost churnedStartHost = new FakeEngineHost(churnedStartClock, 10);
        AndroidImeSessionClient churnedStartClient = new AndroidImeSessionClient(
            churnedStartHost,
            new FakeEditorConnection()
        );
        churnedStartHost.debugDropNextStartResponse();
        try {
            churnedStartClient.startInput(InputContext.androidText());
            throw new IllegalStateException("ENG2-V008 churned Start response loss missing");
        } catch (FakeEngineHost.SimulatedStartResponseLoss expected) {
            // Keep the exact pending Start identity until the retry deadline.
        }
        SessionKey evictedPending = churnedStartClient.pendingStartSessionKey();
        churnedStartClock.advance(11);
        churnedStartHost.expireResponseCaches();
        churnTombstones(churnedStartHost, "start-churn");
        churnedStartHost.debugFailNextStartsWithInvalid(1);
        churnedStartClient.startInput(InputContext.androidText());
        check(
            !evictedPending.equals(churnedStartClient.activeSessionKey()),
            "ENG2-V008 evicted pending Start INVALID_SESSION recovers once"
        );

        FakeEngineHost boundedStartHost = new FakeEngineHost();
        AndroidImeSessionClient boundedStartClient = new AndroidImeSessionClient(
            boundedStartHost,
            new FakeEditorConnection()
        );
        boundedStartHost.debugFailNextStartsWithStale(3);
        expectProtocol(
            ErrorCode.STALE_SESSION,
            () -> boundedStartClient.startInput(InputContext.androidText()),
            "ENG2-V008 sustained stale Start is bounded"
        );
        check(!boundedStartClient.hasActiveSession(), "ENG2-V008 bounded Start no active session");
        check(
            boundedStartHost.debugPendingStartStaleFailures() == 1,
            "ENG2-V008 bounded Start performs only one fresh retry"
        );

        FakeNanoClock lostKeyClock = new FakeNanoClock();
        FakeEngineHost lostKeyHost = new FakeEngineHost(lostKeyClock, 10);
        FakeEditorConnection lostKeyEditor = new FakeEditorConnection();
        AndroidImeSessionClient lostKeyClient = new AndroidImeSessionClient(
            lostKeyHost,
            lostKeyEditor
        );
        lostKeyClient.startInput(InputContext.androidText());
        lostKeyHost.debugDropNextTransitionResponse();
        try {
            lostKeyClient.processKey("n");
            throw new IllegalStateException("ENG2-V008 lost key response missing");
        } catch (FakeEngineHost.SimulatedTransitionResponseLoss expected) {
            // The unconfirmed key must not be replayed after expiry.
        }
        lostKeyClock.advance(11);
        lostKeyHost.expireResponseCaches();
        check(
            lostKeyClient.processKey("n").status() ==
                OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY,
            "ENG2-V008 expired key response recovers"
        );
        check(lostKeyClient.state().preedit().isEmpty(), "ENG2-V008 key not replayed");
        check(lostKeyEditor.setCalls == 0, "ENG2-V008 lost key not projected");

        FakeNanoClock churnedKeyClock = new FakeNanoClock();
        FakeEngineHost churnedKeyHost = new FakeEngineHost(churnedKeyClock, 10);
        FakeEditorConnection churnedKeyEditor = new FakeEditorConnection();
        AndroidImeSessionClient churnedKeyClient = new AndroidImeSessionClient(
            churnedKeyHost,
            churnedKeyEditor
        );
        churnedKeyClient.startInput(InputContext.androidText());
        churnedKeyClient.processKey("z");
        churnedKeyHost.debugDropNextTransitionResponse();
        try {
            churnedKeyClient.processKey("x");
            throw new IllegalStateException("ENG2-V008 churned key response loss missing");
        } catch (FakeEngineHost.SimulatedTransitionResponseLoss expected) {
            // The current active identity remains exact while its Host tombstone exists.
        }
        churnedKeyClock.advance(11);
        churnedKeyHost.expireResponseCaches();
        churnTombstones(churnedKeyHost, "key-churn");
        check(
            churnedKeyClient.processKey("x").status() ==
                OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY,
            "ENG2-V008 evicted active INVALID_SESSION recovers once"
        );
        check(churnedKeyClient.state().preedit().isEmpty(), "ENG2-V008 evicted key not replayed");
        check(churnedKeyEditor.setCalls == 1, "ENG2-V008 only confirmed key was projected");
        check(churnedKeyEditor.clearCalls == 1, "ENG2-V008 confirmed preedit cleared on recovery");

        FakeNanoClock lostCommitClock = new FakeNanoClock();
        FakeEngineHost lostCommitHost = new FakeEngineHost(lostCommitClock, 10);
        FakeEditorConnection lostCommitEditor = new FakeEditorConnection();
        AndroidImeSessionClient lostCommitClient = new AndroidImeSessionClient(
            lostCommitHost,
            lostCommitEditor
        );
        lostCommitClient.startInput(InputContext.androidText());
        lostCommitClient.processKey("z");
        lostCommitHost.debugDropNextTransitionResponse();
        try {
            lostCommitClient.commitComposition();
            throw new IllegalStateException("ENG2-V008 lost commit response missing");
        } catch (FakeEngineHost.SimulatedTransitionResponseLoss expected) {
            // No editor commit happened, so recovery must not replay it.
        }
        lostCommitClock.advance(11);
        lostCommitHost.expireResponseCaches();
        check(
            lostCommitClient.commitComposition().status() ==
                OutcomeStatus.HOST_RESTART_RECOVERED_NO_REPLAY,
            "ENG2-V008 expired commit response recovers"
        );
        check(lostCommitEditor.commitCalls == 0, "ENG2-V008 commit not replayed");
        check(lostCommitEditor.clearCalls == 1, "ENG2-V008 stale preedit cleared");

        FakeNanoClock lateAckClock = new FakeNanoClock();
        FakeEngineHost lateAckHost = new FakeEngineHost(lateAckClock, 10);
        FakeEditorConnection lateAckEditor = new FakeEditorConnection();
        AndroidImeSessionClient lateAckClient = new AndroidImeSessionClient(
            lateAckHost,
            lateAckEditor
        );
        lateAckClient.startInput(InputContext.androidText());
        lateAckEditor.nextAfterEffect = () -> lateAckClock.advance(11);
        check(
            lateAckClient.processKey("n").status() ==
                OutcomeStatus.SESSION_RETIRED_RESPONSE_EXPIRED,
            "ENG2-V008 late acknowledgement retires without false retry"
        );
        check(!lateAckClient.hasActiveSession(), "ENG2-V008 late ack retires client");
        check(lateAckEditor.clearCalls == 1, "ENG2-V008 late ack clears preedit");

        FakeEngineHost outOfOrderAckHost = new FakeEngineHost();
        FakeEditorConnection outOfOrderAckEditor = new FakeEditorConnection();
        AndroidImeSessionClient outOfOrderAckClient = new AndroidImeSessionClient(
            outOfOrderAckHost,
            outOfOrderAckEditor
        );
        outOfOrderAckClient.startInput(InputContext.androidText());
        outOfOrderAckHost.debugFailNextAck();
        check(
            outOfOrderAckClient.processKey("n").status() ==
                OutcomeStatus.SESSION_RETIRED_ACK_AMBIGUOUS,
            "ENG2-V008 OUT_OF_ORDER acknowledgement returns retired outcome"
        );
        check(!outOfOrderAckClient.hasActiveSession(), "ENG2-V008 ACK protocol failure retires");
        check(outOfOrderAckEditor.setCalls == 1, "ENG2-V008 ACK protocol effect applied once");
        check(outOfOrderAckEditor.clearCalls == 1, "ENG2-V008 ACK protocol preedit cleared");

        FakeEngineHost runtimeAckHost = new FakeEngineHost();
        FakeEditorConnection runtimeAckEditor = new FakeEditorConnection();
        AndroidImeSessionClient runtimeAckClient = new AndroidImeSessionClient(
            runtimeAckHost,
            runtimeAckEditor
        );
        runtimeAckClient.startInput(InputContext.androidText());
        runtimeAckClient.processKey("z");
        runtimeAckHost.debugThrowRuntimeOnNextAck();
        check(
            runtimeAckClient.commitComposition().status() ==
                OutcomeStatus.SESSION_RETIRED_ACK_AMBIGUOUS,
            "ENG2-V008 runtime acknowledgement returns retired outcome"
        );
        check(!runtimeAckClient.hasActiveSession(), "ENG2-V008 ACK runtime failure retires");
        check(runtimeAckEditor.commitCalls == 1, "ENG2-V008 ACK runtime commit applied once");
        check(runtimeAckEditor.committed.toString().equals("z"), "ENG2-V008 ACK runtime no replay");
        check(runtimeAckEditor.clearCalls == 0, "ENG2-V008 committed effect is not cleared");

        FakeEngineHost failingEndHost = new FakeEngineHost();
        FakeEditorConnection failingEndEditor = new FakeEditorConnection();
        AndroidImeSessionClient failingEndClient = new AndroidImeSessionClient(
            failingEndHost,
            failingEndEditor
        );
        failingEndClient.startInput(InputContext.androidText());
        failingEndClient.processKey("n");
        failingEndHost.debugFailNextEnd();
        expectProtocol(
            ErrorCode.OUT_OF_ORDER_REQUEST,
            failingEndClient::endInput,
            "ENG2-V008 terminal Host failure"
        );
        check(!failingEndClient.hasActiveSession(), "ENG2-V008 failed End retires local");
        check(failingEndEditor.clearCalls == 1, "ENG2-V008 failed End clears preedit");

        FakeEngineHost runtimeEndHost = new FakeEngineHost();
        FakeEditorConnection runtimeEndEditor = new FakeEditorConnection();
        AndroidImeSessionClient runtimeEndClient = new AndroidImeSessionClient(
            runtimeEndHost,
            runtimeEndEditor
        );
        runtimeEndClient.startInput(InputContext.androidText());
        runtimeEndClient.processKey("n");
        runtimeEndHost.debugThrowRuntimeOnNextEnd();
        try {
            runtimeEndClient.endInput();
            throw new IllegalStateException("ENG2-V008 runtime End failure missing");
        } catch (IllegalStateException expected) {
            check(
                expected.getMessage().equals("synthetic transport end failure"),
                "ENG2-V008 unexpected runtime End failure"
            );
        }
        check(!runtimeEndClient.hasActiveSession(), "ENG2-V008 runtime End retires local");
        check(runtimeEndEditor.clearCalls == 1, "ENG2-V008 runtime End clears preedit");
    }

    private static void churnTombstones(FakeEngineHost host, String prefix) {
        HostEpoch epoch = host.hostEpoch();
        for (int index = 0; index <= FakeEngineHost.debugTombstoneLimit(); index++) {
            SessionId sessionId = new SessionId("session-" + prefix + "-" + index);
            host.startSession(epoch, sessionId, 1, InputContext.androidText());
            host.acknowledgeResponse(epoch, sessionId, 1);
            host.endSession(epoch, sessionId, 2);
        }
        check(
            host.debugTombstoneCount() <= FakeEngineHost.debugTombstoneLimit(),
            "ENG2-V008 churn preserves tombstone bound"
        );
    }

    private static void startSequenceAndIdempotency() {
        FakeEngineHost host = new FakeEngineHost();
        HostEpoch epoch = host.hostEpoch();
        SessionId sessionId = new SessionId("session-start-sequence");
        SessionStart first = host.startSession(
            epoch,
            sessionId,
            1,
            InputContext.androidText()
        );
        SessionStart duplicate = host.startSession(
            epoch,
            sessionId,
            1,
            InputContext.androidText()
        );
        check(first.equals(duplicate), "START_SEQ idempotent first request");
        host.processKey(epoch, sessionId, 2, 0, "n");
        expectProtocol(
            ErrorCode.OUT_OF_ORDER_REQUEST,
            () -> host.startSession(epoch, sessionId, 1, InputContext.androidText()),
            "START_SEQ lower start after mutation"
        );

    }

    private static void setOptionWhitelist() {
        FakeEngineHost host = new FakeEngineHost();
        FakeEditorConnection editor = new FakeEditorConnection();
        AndroidImeSessionClient client = new AndroidImeSessionClient(host, editor);
        client.startInput(InputContext.androidText());
        Outcome outcome = client.setOption(EngineOption.FULL_SHAPE, true);
        check(outcome.status() == OutcomeStatus.NO_EDITOR_EFFECT, "SET_OPTION no editor effect");
        check(
            host.debugOption(
                client.activeSessionKey().hostEpoch(),
                client.activeSessionKey().sessionId(),
                EngineOption.FULL_SHAPE
            ),
            "SET_OPTION applied"
        );
        check(EngineOption.values().length == 1, "SET_OPTION whitelist is closed");
    }

    private static void expectProtocol(
        ErrorCode expected,
        Runnable operation,
        String label
    ) {
        try {
            operation.run();
            throw new IllegalStateException(label + " unexpectedly succeeded");
        } catch (ProtocolException error) {
            check(error.code() == expected, label + " error mismatch: " + error.code());
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new IllegalStateException(message);
        }
    }
}
