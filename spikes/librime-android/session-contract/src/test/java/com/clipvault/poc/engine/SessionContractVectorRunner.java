package com.clipvault.poc.engine;

import static com.clipvault.poc.engine.EngineSessionContract.Candidate;
import static com.clipvault.poc.engine.EngineSessionContract.CandidateSource;
import static com.clipvault.poc.engine.EngineSessionContract.CompositionSegment;
import static com.clipvault.poc.engine.EngineSessionContract.EditorAction;
import static com.clipvault.poc.engine.EngineSessionContract.EngineMode;
import static com.clipvault.poc.engine.EngineSessionContract.EngineOption;
import static com.clipvault.poc.engine.EngineSessionContract.FieldKind;
import static com.clipvault.poc.engine.EngineSessionContract.HostEpoch;
import static com.clipvault.poc.engine.EngineSessionContract.InputContext;
import static com.clipvault.poc.engine.EngineSessionContract.PageDirection;
import static com.clipvault.poc.engine.EngineSessionContract.Platform;
import static com.clipvault.poc.engine.EngineSessionContract.ProtocolException;
import static com.clipvault.poc.engine.EngineSessionContract.SegmentKind;
import static com.clipvault.poc.engine.EngineSessionContract.SessionId;
import static com.clipvault.poc.engine.EngineSessionContract.SessionStart;
import static com.clipvault.poc.engine.EngineSessionContract.State;
import static com.clipvault.poc.engine.EngineSessionContract.Transition;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

public final class SessionContractVectorRunner {
    private static final String EMPTY = "<empty>";
    private static final String NONE = "~";
    private static final String NOT_APPLICABLE = "-";
    private static final int COLUMN_COUNT = 17;
    private static final String HEADER =
        "case_id\tstep\taction\targument\trequest_seq\trequest_revision\texpected_revision\t" +
            "expected_preedit\texpected_caret_utf16\texpected_segments\t" +
            "expected_page_index\texpected_has_previous\texpected_has_next\t" +
            "expected_candidate_ids\texpected_commit\texpected_mode\texpected_error";

    private record VectorRow(
        String caseId,
        int step,
        String action,
        String argument,
        String requestSeq,
        String requestRevision,
        String expectedRevision,
        String expectedPreedit,
        String expectedCaretUtf16,
        String expectedSegments,
        String expectedPageIndex,
        String expectedHasPrevious,
        String expectedHasNext,
        String expectedCandidateIds,
        String expectedCommit,
        String expectedMode,
        String expectedError
    ) {}

    private record Observed(State state, String commitText, Transition transition) {}

    private record SessionResponseKey(HostEpoch hostEpoch, SessionId sessionId) {}

    private static final class CaseContext {
        private FakeEngineHost host = new FakeEngineHost();
        private HostEpoch requestEpoch;
        private SessionId activeSession;
        private HostEpoch retiredEpoch;
        private SessionId retiredSession;
        private InputContext activeContext;
        private SessionStart cachedSessionStart;
        private final Map<String, String> candidateAliases = new LinkedHashMap<>();
        private final Map<Long, Transition> successfulResponses = new LinkedHashMap<>();
        private final Map<SessionResponseKey, Long> highestAppliedRequestSeq =
            new LinkedHashMap<>();
        private final StringBuilder projectedEditorText = new StringBuilder();
    }

    private SessionContractVectorRunner() {}

    public static void main(String[] args) throws IOException {
        String configured = System.getProperty("clipvault.sessionVectors");
        if (configured == null) {
            throw new IllegalStateException("clipvault.sessionVectors is required");
        }
        List<VectorRow> rows = loadVectors(Path.of(configured));
        Map<String, List<VectorRow>> cases = new LinkedHashMap<>();
        for (VectorRow row : rows) {
            cases.computeIfAbsent(row.caseId(), ignored -> new ArrayList<>()).add(row);
        }
        cases.forEach(SessionContractVectorRunner::runCase);
        System.out.printf(
            "SESSION CONTRACT VECTORS PASSED: %d cases / %d steps%n",
            cases.size(),
            rows.size()
        );
    }

    private static void runCase(String caseId, List<VectorRow> rows) {
        CaseContext context = new CaseContext();
        int previousStep = 0;

        for (VectorRow row : rows) {
            check(row.step() == previousStep + 1, caseId + " has non-contiguous steps");
            previousStep = row.step();
            String expectedError = NONE.equals(row.expectedError())
                ? null
                : row.expectedError();
            try {
                Observed observed = execute(row, context);
                check(
                    expectedError == null,
                    row.caseId() + ":" + row.step() + " expected an error but succeeded"
                );
                assertObserved(row, observed, context);
                cacheAndCheckDuplicate(row, observed, context);
                applyCommitToClientProjection(observed, context);
            } catch (ProtocolException error) {
                check(
                    Objects.equals(expectedError, error.code().name()),
                    row.caseId() + ":" + row.step() + " error-code mismatch: " + error.code()
                );
            }
        }
    }

    private static Observed execute(VectorRow row, CaseContext context) {
        return switch (row.action()) {
            case "START" -> {
                InputContext inputContext = parseContext(row.argument());
                SessionId requestedSession = new SessionId("session-" + UUID.randomUUID());
                SessionStart started = context.host.startSession(
                    context.host.hostEpoch(),
                    requestedSession,
                    requestSeq(row),
                    inputContext
                );
                check(
                    started.requestSeq() == requestSeq(row),
                    row.caseId() + ":" + row.step() + " start sequence mismatch"
                );
                context.requestEpoch = started.hostEpoch();
                context.activeSession = started.sessionId();
                context.activeContext = inputContext;
                context.cachedSessionStart = started;
                context.candidateAliases.clear();
                context.successfulResponses.clear();
                context.highestAppliedRequestSeq.clear();
                context.projectedEditorText.setLength(0);
                yield new Observed(started.state(), null, null);
            }
            case "START_DUPLICATE" -> {
                SessionStart repeated = context.host.startSession(
                    requireEpoch(context, row),
                    requireSession(context, row),
                    requestSeq(row),
                    context.activeContext
                );
                check(
                    repeated.equals(context.cachedSessionStart),
                    row.caseId() + ":" + row.step() + " changed duplicate start response"
                );
                yield new Observed(repeated.state(), null, null);
            }
            case "KEY" -> observed(context.host.processKey(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row),
                requireArgument(row)
            ));
            case "KEYS" -> {
                HostEpoch epoch = requireEpoch(context, row);
                SessionId sessionId = requireSession(context, row);
                long sequence = requestSeq(row);
                long revision = expectedRevisionForRequest(row);
                Transition last = null;
                String argument = requireArgument(row);
                int[] codePoints = argument.codePoints().toArray();
                check(codePoints.length > 0, row.caseId() + ":" + row.step() + " has empty KEYS");
                for (int codePoint : codePoints) {
                    last = context.host.processKey(
                        epoch,
                        sessionId,
                        sequence,
                        revision,
                        new String(Character.toChars(codePoint))
                    );
                    sequence += 1;
                    revision = last.state().revision();
                }
                yield observed(last);
            }
            case "PAGE_NEXT" -> observed(context.host.pageCandidates(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row),
                PageDirection.NEXT
            ));
            case "PAGE_PREVIOUS" -> observed(context.host.pageCandidates(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row),
                PageDirection.PREVIOUS
            ));
            case "SELECT" -> observed(context.host.selectCandidate(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row),
                resolveCandidateArgument(row, context)
            ));
            case "COMMIT" -> observed(context.host.commitComposition(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row)
            ));
            case "CANCEL" -> observed(context.host.cancelComposition(
                requireEpoch(context, row),
                requireSession(context, row),
                requestSeq(row),
                expectedRevisionForRequest(row)
            ));
            case "SET_OPTION" -> {
                String[] parts = requireArgument(row).split("=", 2);
                check(parts.length == 2, row.caseId() + ":" + row.step() + " invalid option");
                yield observed(context.host.setOption(
                    requireEpoch(context, row),
                    requireSession(context, row),
                    requestSeq(row),
                    expectedRevisionForRequest(row),
                    EngineOption.valueOf(parts[0]),
                    strictBoolean(parts[1])
                ));
            }
            case "SNAPSHOT" -> new Observed(
                context.host.snapshot(
                    requireEpoch(context, row),
                    requireSession(context, row)
                ),
                null,
                null
            );
            case "SNAPSHOT_INVALID_SESSION" -> new Observed(
                context.host.snapshot(
                    requireEpoch(context, row),
                    new SessionId("missing-" + UUID.randomUUID())
                ),
                null,
                null
            );
            case "SNAPSHOT_RETIRED_SESSION_CURRENT_EPOCH" -> new Observed(
                context.host.snapshot(
                    requireEpoch(context, row),
                    requireRetiredSession(context, row)
                ),
                null,
                null
            );
            case "END_SESSION" -> {
                SessionResponseKey responseKey = new SessionResponseKey(
                    requireEpoch(context, row),
                    requireSession(context, row)
                );
                context.host.endSession(
                    responseKey.hostEpoch(),
                    responseKey.sessionId(),
                    requestSeq(row)
                );
                context.highestAppliedRequestSeq.remove(responseKey);
                yield null;
            }
            case "RESTART_HOST" -> {
                context.host.restartHost();
                context.highestAppliedRequestSeq.clear();
                yield null;
            }
            case "NEW_HOST" -> {
                HostEpoch previous = context.host.hostEpoch();
                context.retiredEpoch = context.requestEpoch;
                context.retiredSession = context.activeSession;
                context.host = new FakeEngineHost();
                context.highestAppliedRequestSeq.clear();
                check(
                    !previous.equals(context.host.hostEpoch()),
                    row.caseId() + ":" + row.step() + " reused a host epoch"
                );
                yield null;
            }
            case "ASSERT_SANITIZED" -> {
                check(
                    context.host.debugSessionSanitized(requireSession(context, row)),
                    row.caseId() + ":" + row.step() + " retained session content"
                );
                yield null;
            }
            case "ASSERT_COMMIT_MUTATIONS" -> {
                int expected = Integer.parseInt(requireArgument(row));
                check(
                    context.host.debugCommitMutationCount(requireSession(context, row)) == expected,
                    row.caseId() + ":" + row.step() + " repeated a commit mutation"
                );
                yield null;
            }
            case "ASSERT_PROJECTED_EDITOR" -> {
                check(
                    context.projectedEditorText.toString().equals(requireArgument(row)),
                    row.caseId() + ":" + row.step() + " repeated an editor commit"
                );
                yield null;
            }
            case "CHURN_ENDED" -> {
                int count = Integer.parseInt(requireArgument(row));
                for (int index = 0; index < count; index++) {
                    HostEpoch epoch = context.host.hostEpoch();
                    SessionId sessionId = new SessionId("session-" + UUID.randomUUID());
                    SessionStart started = context.host.startSession(
                        epoch,
                        sessionId,
                        1,
                        InputContext.androidText()
                    );
                    context.host.endSession(
                        started.hostEpoch(),
                        started.sessionId(),
                        2
                    );
                }
                yield null;
            }
            case "ASSERT_TOMBSTONES_BOUNDED" -> {
                int expectedMaximum = Integer.parseInt(requireArgument(row));
                check(
                    expectedMaximum == FakeEngineHost.debugTombstoneLimit() &&
                        context.host.debugTombstoneCount() <= expectedMaximum,
                    row.caseId() + ":" + row.step() + " exceeded the tombstone bound"
                );
                yield null;
            }
            case "ASSERT_PRIVACY_CONTEXT" -> {
                InputContext inputContext = context.host.debugContext(
                    requireEpoch(context, row),
                    requireSession(context, row)
                );
                check(
                    !inputContext.learningAllowed() && !inputContext.clipvaultAllowed(),
                    row.caseId() + ":" + row.step() + " did not normalize privacy flags"
                );
                yield null;
            }
            case "ASSERT_OPTION" -> {
                String[] parts = requireArgument(row).split("=", 2);
                check(parts.length == 2, row.caseId() + ":" + row.step() + " invalid option");
                check(
                    context.host.debugOption(
                        requireEpoch(context, row),
                        requireSession(context, row),
                        EngineOption.valueOf(parts[0])
                    ) == strictBoolean(parts[1]),
                    row.caseId() + ":" + row.step() + " option state mismatch"
                );
                yield null;
            }
            case "ASSERT_VALUE_GUARDS" -> {
                assertValueGuards(row);
                yield null;
            }
            default -> throw new IllegalStateException(
                row.caseId() + ":" + row.step() + " has unknown action"
            );
        };
    }

    private static void assertObserved(
        VectorRow row,
        Observed observed,
        CaseContext context
    ) {
        if (observed == null) {
            assertControlRow(row);
            return;
        }

        State state = observed.state();
        check(state.hostEpoch().equals(context.requestEpoch), mismatch(row, "host epoch"));
        check(state.sessionId().equals(context.activeSession), mismatch(row, "session ID"));
        check(state.revision() == Long.parseLong(row.expectedRevision()), mismatch(row, "revision"));
        check(state.preedit().equals(decodeText(row.expectedPreedit())), mismatch(row, "preedit"));
        check(
            state.caretUtf16() == Integer.parseInt(row.expectedCaretUtf16()),
            mismatch(row, "UTF-16 caret")
        );
        check(
            segmentDescriptors(state).equals(parseExpectedList(row.expectedSegments())),
            mismatch(row, "segments")
        );
        check(state.pageIndex() == Integer.parseInt(row.expectedPageIndex()), mismatch(row, "page"));
        check(
            state.hasPreviousPage() == strictBoolean(row.expectedHasPrevious()),
            mismatch(row, "previous-page flag")
        );
        check(
            state.hasNextPage() == strictBoolean(row.expectedHasNext()),
            mismatch(row, "next-page flag")
        );
        assertCandidateAliases(row, state, context);
        String expectedCommit = NONE.equals(row.expectedCommit())
            ? null
            : decodeText(row.expectedCommit());
        check(Objects.equals(observed.commitText(), expectedCommit), mismatch(row, "commit"));
        check(state.mode().name().equals(row.expectedMode()), mismatch(row, "mode"));
        check(
            state.handled() == (observed.transition() != null),
            mismatch(row, "handled flag")
        );
    }

    private static void cacheAndCheckDuplicate(
        VectorRow row,
        Observed observed,
        CaseContext context
    ) {
        if (observed == null || observed.transition() == null) {
            return;
        }
        Transition transition = observed.transition();
        Transition previous = context.successfulResponses.putIfAbsent(
            transition.requestSeq(),
            transition
        );
        if (previous != null) {
            check(previous.equals(transition), row.caseId() + ":" + row.step() + " changed duplicate response");
        }
    }

    private static void applyCommitToClientProjection(
        Observed observed,
        CaseContext context
    ) {
        if (
            observed == null ||
            observed.transition() == null ||
            observed.commitText() == null
        ) {
            return;
        }
        Transition transition = observed.transition();
        SessionResponseKey key = new SessionResponseKey(
            transition.state().hostEpoch(),
            transition.state().sessionId()
        );
        long requestSeq = transition.requestSeq();
        Long highestApplied = context.highestAppliedRequestSeq.get(key);
        if (highestApplied != null && requestSeq <= highestApplied) {
            return;
        }
        // Reserve before the editor effect. A real client must invalidate the
        // session rather than retry if the platform reports an ambiguous result.
        context.highestAppliedRequestSeq.put(key, requestSeq);
        context.projectedEditorText.append(observed.commitText());
    }

    private static void assertCandidateAliases(
        VectorRow row,
        State state,
        CaseContext context
    ) {
        List<String> expectedAliases = parseExpectedList(row.expectedCandidateIds());
        check(state.candidates().size() == expectedAliases.size(), mismatch(row, "candidate count"));
        for (int index = 0; index < expectedAliases.size(); index++) {
            String alias = expectedAliases.get(index);
            String actualId = state.candidates().get(index).id();
            check(alias.startsWith("$"), row.caseId() + ":" + row.step() + " uses a non-alias candidate ID");
            String previous = context.candidateAliases.putIfAbsent(alias, actualId);
            check(previous == null || previous.equals(actualId), mismatch(row, "stable candidate ID"));
            check(
                context.candidateAliases.entrySet().stream().noneMatch(entry ->
                    !entry.getKey().equals(alias) && entry.getValue().equals(actualId)
                ),
                mismatch(row, "candidate alias identity")
            );
            check(
                actualId.startsWith("candidate-") &&
                    !actualId.contains(state.candidates().get(index).text()),
                mismatch(row, "opaque candidate ID")
            );
        }
    }

    private static void assertValueGuards(VectorRow row) {
        expectIllegalArgument(
            () -> new InputContext(
                Platform.ANDROID,
                FieldKind.TEXT,
                EditorAction.NONE,
                false,
                false,
                false,
                "typed text is forbidden"
            ),
            row,
            "unbounded app scope"
        );

        Candidate duplicate = new Candidate(
            "candidate-duplicate",
            "synthetic",
            null,
            CandidateSource.ENGINE
        );
        expectIllegalArgument(
            () -> new State(
                new HostEpoch("epoch-synthetic"),
                new SessionId("session-synthetic"),
                1,
                true,
                "ab",
                2,
                List.of(new CompositionSegment(0, 2, SegmentKind.RAW)),
                List.of(duplicate, duplicate),
                0,
                false,
                false,
                EngineMode.SELECTING
            ),
            row,
            "duplicate candidate ID"
        );

        expectIllegalArgument(
            () -> new State(
                new HostEpoch("epoch-synthetic"),
                new SessionId("session-synthetic"),
                1,
                true,
                "😀",
                1,
                List.of(new CompositionSegment(0, 2, SegmentKind.RAW)),
                List.of(),
                0,
                false,
                false,
                EngineMode.COMPOSING
            ),
            row,
            "caret splitting surrogate pair"
        );

        expectIllegalArgument(
            () -> new State(
                new HostEpoch("epoch-synthetic"),
                new SessionId("session-synthetic"),
                1,
                true,
                "😀",
                2,
                List.of(
                    new CompositionSegment(0, 1, SegmentKind.RAW),
                    new CompositionSegment(1, 2, SegmentKind.RAW)
                ),
                List.of(),
                0,
                false,
                false,
                EngineMode.COMPOSING
            ),
            row,
            "segment splitting surrogate pair"
        );

        expectIllegalArgument(
            () -> new State(
                new HostEpoch("epoch-synthetic"),
                new SessionId("session-synthetic"),
                1,
                true,
                "a",
                1,
                List.of(new CompositionSegment(0, 1, SegmentKind.RAW)),
                List.of(),
                -1,
                false,
                false,
                EngineMode.COMPOSING
            ),
            row,
            "negative page index"
        );

    }

    private static void expectIllegalArgument(
        Runnable operation,
        VectorRow row,
        String label
    ) {
        try {
            operation.run();
            throw new IllegalStateException(
                row.caseId() + ":" + row.step() + " accepted " + label
            );
        } catch (IllegalArgumentException expected) {
            // Expected fail-closed guard.
        }
    }

    private static List<String> segmentDescriptors(State state) {
        return state.segments().stream()
            .map(segment ->
                segment.startUtf16() + ":" + segment.endUtf16() + ":" + segment.kind().name()
            )
            .toList();
    }

    private static void assertControlRow(VectorRow row) {
        check(
            List.of(
                row.expectedRevision(),
                row.expectedPreedit(),
                row.expectedCaretUtf16(),
                row.expectedSegments(),
                row.expectedPageIndex(),
                row.expectedHasPrevious(),
                row.expectedHasNext(),
                row.expectedCandidateIds(),
                row.expectedMode()
            ).stream().allMatch(NOT_APPLICABLE::equals) &&
                NONE.equals(row.expectedCommit()),
            row.caseId() + ":" + row.step() + " has state for a control action"
        );
    }

    private static List<VectorRow> loadVectors(Path path) throws IOException {
        List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        Map<String, String> metadata = new LinkedHashMap<>();
        List<VectorRow> rows = new ArrayList<>();
        boolean sawHeader = false;

        for (int index = 0; index < lines.size(); index++) {
            String line = lines.get(index).stripTrailing();
            if (line.isBlank()) {
                continue;
            }
            if (line.startsWith("#")) {
                String[] parts = line.substring(1).trim().split("=", 2);
                check(parts.length == 2, "invalid vector metadata at line " + (index + 1));
                metadata.put(parts[0], parts[1]);
                continue;
            }
            if (!sawHeader) {
                check(line.equals(HEADER), "unexpected vector header");
                sawHeader = true;
                continue;
            }
            String[] columns = line.split("\t", -1);
            check(columns.length == COLUMN_COUNT, "invalid vector row at line " + (index + 1));
            rows.add(new VectorRow(
                columns[0],
                Integer.parseInt(columns[1]),
                columns[2],
                columns[3],
                columns[4],
                columns[5],
                columns[6],
                columns[7],
                columns[8],
                columns[9],
                columns[10],
                columns[11],
                columns[12],
                columns[13],
                columns[14],
                columns[15],
                columns[16]
            ));
        }

        check("3".equals(metadata.get("format_version")), "unsupported vector format");
        check(
            "project-authored-synthetic-fixtures".equals(metadata.get("source")),
            "vectors must remain project-authored synthetic fixtures"
        );
        check(
            "false".equals(metadata.get("contains_personal_data")),
            "vectors must contain no personal data"
        );
        check(
            "false".equals(metadata.get("typed_text_persistence_allowed")),
            "typed-text persistence must remain disabled"
        );
        check(sawHeader && !rows.isEmpty(), "session vectors are empty");
        return rows;
    }

    private static InputContext parseContext(String value) {
        return switch (value) {
            case "ANDROID_TEXT" -> InputContext.androidText();
            case "ANDROID_PASSWORD_INCOGNITO" -> new InputContext(
                Platform.ANDROID,
                FieldKind.PASSWORD,
                EditorAction.NONE,
                true,
                true,
                true,
                null
            );
            default -> throw new IllegalStateException("unknown synthetic input context: " + value);
        };
    }

    private static long requestSeq(VectorRow row) {
        check(
            !NOT_APPLICABLE.equals(row.requestSeq()),
            row.caseId() + ":" + row.step() + " requires a request sequence"
        );
        return Long.parseLong(row.requestSeq());
    }

    private static long expectedRevisionForRequest(VectorRow row) {
        check(
            !NOT_APPLICABLE.equals(row.requestRevision()),
            row.caseId() + ":" + row.step() + " requires an expected revision"
        );
        return Long.parseLong(row.requestRevision());
    }

    private static String requireArgument(VectorRow row) {
        check(
            !NOT_APPLICABLE.equals(row.argument()),
            row.caseId() + ":" + row.step() + " requires an argument"
        );
        return decodeText(row.argument());
    }

    private static String resolveCandidateArgument(VectorRow row, CaseContext context) {
        String argument = requireArgument(row);
        if (!argument.startsWith("$")) {
            return argument;
        }
        String resolved = context.candidateAliases.get(argument);
        check(resolved != null, row.caseId() + ":" + row.step() + " uses an unknown candidate alias");
        return resolved;
    }

    private static HostEpoch requireEpoch(CaseContext context, VectorRow row) {
        check(
            context.requestEpoch != null,
            row.caseId() + ":" + row.step() + " has no request host epoch"
        );
        return context.requestEpoch;
    }

    private static SessionId requireSession(CaseContext context, VectorRow row) {
        check(
            context.activeSession != null,
            row.caseId() + ":" + row.step() + " has no active session"
        );
        return context.activeSession;
    }

    private static SessionId requireRetiredSession(CaseContext context, VectorRow row) {
        check(
            context.retiredEpoch != null && context.retiredSession != null,
            row.caseId() + ":" + row.step() + " has no retired host/session identity"
        );
        check(
            !context.retiredEpoch.equals(context.requestEpoch) &&
                !context.retiredSession.equals(context.activeSession),
            row.caseId() + ":" + row.step() + " reused a host/session identity"
        );
        return context.retiredSession;
    }

    private static List<String> parseExpectedList(String value) {
        return EMPTY.equals(value) ? List.of() : List.of(value.split(",", -1));
    }

    private static String decodeText(String value) {
        return EMPTY.equals(value) ? "" : value;
    }

    private static boolean strictBoolean(String value) {
        check("true".equals(value) || "false".equals(value), "invalid boolean vector value");
        return Boolean.parseBoolean(value);
    }

    private static Observed observed(Transition transition) {
        return new Observed(transition.state(), transition.commitText(), transition);
    }

    private static String mismatch(VectorRow row, String field) {
        return row.caseId() + ":" + row.step() + " " + field + " mismatch";
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new IllegalStateException(message);
        }
    }
}
