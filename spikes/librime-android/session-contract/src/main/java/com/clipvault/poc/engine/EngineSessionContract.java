package com.clipvault.poc.engine;

import java.util.List;
import java.util.regex.Pattern;

public final class EngineSessionContract {
    private static final Pattern OPAQUE_APP_SCOPE = Pattern.compile(
        "[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
    );

    private EngineSessionContract() {}

    public record HostEpoch(String value) {
        public HostEpoch {
            if (value == null || value.isBlank()) {
                throw new IllegalArgumentException("host epoch must not be blank");
            }
        }
    }

    public record SessionId(String value) {
        public SessionId {
            if (value == null || value.isBlank()) {
                throw new IllegalArgumentException("session id must not be blank");
            }
        }
    }

    public enum Platform {
        ANDROID,
        WINDOWS
    }

    public enum FieldKind {
        TEXT,
        MULTILINE,
        EMAIL,
        URL,
        NUMBER,
        PHONE,
        PASSWORD,
        OTP,
        UNKNOWN
    }

    public enum EditorAction {
        NONE,
        ENTER,
        DONE,
        GO,
        NEXT,
        SEARCH,
        SEND
    }

    public record InputContext(
        Platform platform,
        FieldKind fieldKind,
        EditorAction action,
        boolean incognito,
        boolean learningAllowed,
        boolean clipvaultAllowed,
        String appScope
    ) {
        public InputContext {
            if (platform == null || fieldKind == null || action == null) {
                throw new IllegalArgumentException("input context enums must not be null");
            }
            if (appScope != null && !OPAQUE_APP_SCOPE.matcher(appScope).matches()) {
                throw new IllegalArgumentException("app scope must be a bounded opaque token");
            }
            if (incognito || fieldKind == FieldKind.PASSWORD) {
                learningAllowed = false;
                clipvaultAllowed = false;
            }
        }

        public static InputContext androidText() {
            return new InputContext(
                Platform.ANDROID,
                FieldKind.TEXT,
                EditorAction.NONE,
                false,
                true,
                true,
                null
            );
        }
    }

    public enum SegmentKind {
        RAW,
        CONVERTED,
        SELECTED
    }

    public record CompositionSegment(int startUtf16, int endUtf16, SegmentKind kind) {
        public CompositionSegment {
            if (startUtf16 < 0 || endUtf16 <= startUtf16 || kind == null) {
                throw new IllegalArgumentException("invalid composition segment");
            }
        }
    }

    public enum CandidateSource {
        ENGINE
    }

    public record Candidate(String id, String text, String comment, CandidateSource source) {
        public Candidate {
            if (id == null || id.isBlank()) {
                throw new IllegalArgumentException("candidate id must not be blank");
            }
            if (text == null || source == null) {
                throw new IllegalArgumentException("candidate text/source must not be null");
            }
        }
    }

    public enum EngineMode {
        DIRECT,
        COMPOSING,
        SELECTING,
        DISABLED
    }

    public record State(
        HostEpoch hostEpoch,
        SessionId sessionId,
        long revision,
        boolean handled,
        String preedit,
        int caretUtf16,
        List<CompositionSegment> segments,
        List<Candidate> candidates,
        int pageIndex,
        boolean hasPreviousPage,
        boolean hasNextPage,
        EngineMode mode
    ) {
        public State {
            if (hostEpoch == null || sessionId == null || preedit == null || mode == null) {
                throw new IllegalArgumentException("state identity/text/mode must not be null");
            }
            if (
                revision < 0 ||
                caretUtf16 < 0 ||
                caretUtf16 > preedit.length() ||
                pageIndex < 0
            ) {
                throw new IllegalArgumentException("invalid revision or UTF-16 caret");
            }
            segments = List.copyOf(segments);
            candidates = List.copyOf(candidates);
            if (candidates.stream().map(Candidate::id).distinct().count() != candidates.size()) {
                throw new IllegalArgumentException("candidate IDs must be unique in one state");
            }
            validateSegments(preedit, caretUtf16, segments);
        }

        private static void validateSegments(
            String preedit,
            int caretUtf16,
            List<CompositionSegment> segments
        ) {
            if (preedit.isEmpty()) {
                if (caretUtf16 != 0 || !segments.isEmpty()) {
                    throw new IllegalArgumentException("empty preedit must have caret 0 and no segments");
                }
                return;
            }
            if (splitsSurrogatePair(preedit, caretUtf16)) {
                throw new IllegalArgumentException("caret must not split a surrogate pair");
            }
            if (segments.isEmpty() || segments.get(0).startUtf16() != 0) {
                throw new IllegalArgumentException("segments must start at UTF-16 offset 0");
            }
            int expectedStart = 0;
            for (CompositionSegment segment : segments) {
                if (segment.startUtf16() != expectedStart) {
                    throw new IllegalArgumentException("segments must be contiguous");
                }
                if (
                    splitsSurrogatePair(preedit, segment.startUtf16()) ||
                    splitsSurrogatePair(preedit, segment.endUtf16())
                ) {
                    throw new IllegalArgumentException(
                        "segment boundary must not split a surrogate pair"
                    );
                }
                expectedStart = segment.endUtf16();
            }
            if (expectedStart != preedit.length()) {
                throw new IllegalArgumentException("segments must cover the complete preedit");
            }
        }

        private static boolean splitsSurrogatePair(String text, int boundary) {
            return boundary > 0 &&
                boundary < text.length() &&
                Character.isHighSurrogate(text.charAt(boundary - 1)) &&
                Character.isLowSurrogate(text.charAt(boundary));
        }
    }

    public record Transition(long requestSeq, State state, String commitText) {
        public Transition {
            if (requestSeq <= 0 || state == null) {
                throw new IllegalArgumentException("transition requires request sequence and state");
            }
        }
    }

    public record SessionStart(
        long requestSeq,
        HostEpoch hostEpoch,
        SessionId sessionId,
        State state
    ) {
        public SessionStart {
            if (requestSeq != 1 || hostEpoch == null || sessionId == null || state == null) {
                throw new IllegalArgumentException("invalid session start response");
            }
            if (
                !hostEpoch.equals(state.hostEpoch()) ||
                !sessionId.equals(state.sessionId()) ||
                state.revision() != 0
            ) {
                throw new IllegalArgumentException("session start identity/revision mismatch");
            }
        }
    }

    public enum PageDirection {
        PREVIOUS,
        NEXT
    }

    /** Content-free whitelist shared by every client implementation. */
    public enum EngineOption {
        FULL_SHAPE
    }

    public enum ErrorCode {
        INVALID_KEY,
        INVALID_PAGE_SIZE,
        INVALID_SESSION,
        SESSION_ENDED,
        STALE_SESSION,
        STALE_REVISION,
        OUT_OF_ORDER_REQUEST,
        PAGE_OUT_OF_RANGE,
        INVALID_CANDIDATE,
        NO_COMPOSITION
    }

    public static final class ProtocolException extends IllegalStateException {
        private final ErrorCode code;

        public ProtocolException(ErrorCode code) {
            super(code.name());
            this.code = code;
        }

        public ErrorCode code() {
            return code;
        }
    }

    public interface Adapter {
        HostEpoch hostEpoch();

        default SessionStart startSession(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            InputContext context
        ) {
            return startSession(hostEpoch, sessionId, requestSeq, context, 2);
        }

        SessionStart startSession(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            InputContext context,
            int pageSize
        );

        Transition processKey(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision,
            String key
        );

        Transition pageCandidates(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision,
            PageDirection direction
        );

        Transition selectCandidate(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision,
            String candidateId
        );

        Transition commitComposition(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision
        );

        Transition cancelComposition(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision
        );

        Transition setOption(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq,
            long expectedRevision,
            EngineOption option,
            boolean enabled
        );

        State snapshot(HostEpoch hostEpoch, SessionId sessionId);

        /**
         * Trusted transport acknowledgement for the most recently delivered
         * response. Production transports must authenticate this call; the
         * in-process PoC uses the strongly typed session identity.
         */
        void acknowledgeResponse(
            HostEpoch hostEpoch,
            SessionId sessionId,
            long requestSeq
        );

        void endSession(HostEpoch hostEpoch, SessionId sessionId, long requestSeq);
    }
}
