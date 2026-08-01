package org.clipvault.rime.poc;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

/** Isolated JNI contract for the ClipVault librime PoC. */
public final class NativeRimeBridge implements AutoCloseable {
    private static final String DEFAULT_SCHEMA = "clipvault_poc";

    static {
        System.loadLibrary("clipvault_rime_jni");
    }

    private long handle;

    public NativeRimeBridge(String sharedDataDir, String userDataDir) {
        this(sharedDataDir, userDataDir, DEFAULT_SCHEMA);
    }

    public NativeRimeBridge(String sharedDataDir, String userDataDir, String schemaId) {
        handle = nativeCreate(
                Objects.requireNonNull(sharedDataDir, "sharedDataDir"),
                Objects.requireNonNull(userDataDir, "userDataDir"),
                Objects.requireNonNull(schemaId, "schemaId"));
        if (handle == 0L) {
            throw new IllegalStateException("nativeCreate returned an invalid handle");
        }
    }

    public synchronized Snapshot processKey(int keycode, int mask) {
        return decode(nativeProcessKey(requireHandle(), keycode, mask));
    }

    public synchronized Snapshot selectCandidate(int index) {
        if (index < 0) {
            throw new IllegalArgumentException("candidate index must be non-negative");
        }
        return decode(nativeSelectCandidate(requireHandle(), index));
    }

    public synchronized Snapshot reset() {
        return decode(nativeReset(requireHandle()));
    }

    public synchronized boolean isClosed() {
        return handle == 0L;
    }

    @Override
    public synchronized void close() {
        if (handle == 0L) {
            return;
        }
        nativeDestroy(handle);
        handle = 0L;
    }

    private long requireHandle() {
        if (handle == 0L) {
            throw new IllegalStateException("native Rime bridge is closed");
        }
        return handle;
    }

    private static Snapshot decode(String[] raw) {
        if (raw == null || raw.length < 3 || ((raw.length - 3) & 1) != 0) {
            throw new IllegalStateException("invalid native snapshot shape");
        }
        final boolean handled;
        if ("1".equals(raw[0])) {
            handled = true;
        } else if ("0".equals(raw[0])) {
            handled = false;
        } else {
            throw new IllegalStateException("invalid native handled flag");
        }

        String composition = Objects.requireNonNull(raw[1], "composition");
        String commit = Objects.requireNonNull(raw[2], "commit");
        List<Candidate> candidates = new ArrayList<>((raw.length - 3) / 2);
        for (int index = 3; index < raw.length; index += 2) {
            candidates.add(new Candidate(
                    Objects.requireNonNull(raw[index], "candidate text"),
                    Objects.requireNonNull(raw[index + 1], "candidate comment")));
        }
        return new Snapshot(handled, composition, commit, candidates);
    }

    private static native long nativeCreate(
            String sharedDataDir, String userDataDir, String schemaId);

    private static native String[] nativeProcessKey(long handle, int keycode, int mask);

    private static native String[] nativeSelectCandidate(long handle, int index);

    private static native String[] nativeReset(long handle);

    private static native void nativeDestroy(long handle);

    public static final class Candidate {
        private final String text;
        private final String comment;

        private Candidate(String text, String comment) {
            this.text = text;
            this.comment = comment;
        }

        public String text() {
            return text;
        }

        public String comment() {
            return comment;
        }
    }

    public static final class Snapshot {
        private final boolean handled;
        private final String composition;
        private final String commit;
        private final List<Candidate> candidates;

        private Snapshot(
                boolean handled,
                String composition,
                String commit,
                List<Candidate> candidates) {
            this.handled = handled;
            this.composition = composition;
            this.commit = commit;
            this.candidates = Collections.unmodifiableList(new ArrayList<>(candidates));
        }

        public boolean handled() {
            return handled;
        }

        public String composition() {
            return composition;
        }

        public String commit() {
            return commit;
        }

        public List<Candidate> candidates() {
            return candidates;
        }
    }
}
