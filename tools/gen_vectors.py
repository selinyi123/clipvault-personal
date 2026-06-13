"""Generate contracts/vectors/*.json (VEC-1).

Expectations are hand-authored here; hashes are computed with hashlib only
(never with the implementation under test). A validation pass against the
desktop implementation catches authoring mistakes before files are written.

Run from repo root:  python tools/gen_vectors.py
"""

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "desktop"))

# ---------------------------------------------------------------- NORM-1

NORM_CASES: list[tuple[str, str]] = [
    ("hello world", "hello world"),
    ("line1\r\nline2", "line1\nline2"),
    ("line1\rline2", "line1\nline2"),
    ("mixed\r\nline\rend", "mixed\nline\nend"),
    ("trailing spaces   ", "trailing spaces"),
    ("trailing newline\n", "trailing newline"),
    ("trailing tabs\t\t", "trailing tabs"),
    ("multi trailing \n\t \n", "multi trailing"),
    ("  leading kept", "  leading kept"),
    ("inner  spaces  kept", "inner  spaces  kept"),
    ("inner line trailing  \nnext", "inner line trailing  \nnext"),
    ("Café", "Café"),                       # NFC: e + combining acute
    ("が", "が"),                          # NFC: ka + dakuten -> ga
    ("你好，世界", "你好，世界"),
    ("\U0001f680 launch", "\U0001f680 launch"),
    ("\U0001f468‍\U0001f469‍\U0001f467", "\U0001f468‍\U0001f469‍\U0001f467"),
    ("código\r\n", "código"),
    ("def f():\n\tindent kept", "def f():\n\tindent kept"),
    ("keep\n\n\ninner blank lines", "keep\n\n\ninner blank lines"),
    ("CRLF at end\r\n\r\n", "CRLF at end"),
    ("ＡＢＣ１２３", "ＡＢＣ１２３"),                     # NFC keeps full-width (NFKC would not)
    ("ﬁle ligature", "ﬁle ligature"),                    # NFC keeps ligature
]

# ---------------------------------------------------------------- CLS-1

CLS_CASES: list[tuple[str, str]] = [
    # url
    ("https://example.com", "url"),
    ("http://example.com/path?q=1", "url"),
    ("https://a.com\nhttps://b.com", "url"),
    ("https://github.com/user/repo/issues/42", "url"),
    ("  https://example.com", "url"),
    # path
    ("C:\\Users\\Admin\\file.txt", "path"),
    ("D:\\AI\\CLAUDE CODE", "path"),
    ("\\\\server\\share\\doc.md", "path"),
    ("/usr/local/bin/python", "path"),
    ("~/projects/clipvault", "path"),
    # command
    ("git commit -m \"fix\"", "command"),
    ("docker compose up -d", "command"),
    ("$ ls -la", "command"),
    ("pip install fastapi", "command"),
    ("adb shell dumpsys battery", "command"),
    ("> npm run dev", "command"),
    # error_log
    (
        "Traceback (most recent call last):\n"
        "  File \"app.py\", line 10, in <module>\n"
        "    main()\nValueError: boom",
        "error_log",
    ),
    ("ERROR foo\nERROR bar", "error_log"),
    ("Exception in thread main\nFATAL: out of memory", "error_log"),
    (
        "java.lang.NullPointerException\n"
        "    at com.app.Main.run(Main.java:42)\n"
        "    at com.app.Main.main(Main.java:10)",
        "error_log",
    ),
    ("[12:01] ERROR db timeout\n[12:02] ERROR retry failed", "error_log"),
    # code
    ("def main():\n    print('hi')\n    return 0", "code"),
    ("function add(a, b) {\n  return a + b;\n}", "code"),
    ("#include <stdio.h>\nint main(void) {\n  return 0;\n}", "code"),
    ("import os\nimport sys\nprint(os.name)", "code"),
    ("const x = 1;\nlet y = 2;\nvar z = 3;", "code"),
    ("```python\nprint(1)\n```", "code"),
    # prompt
    ("你是一个资深架构师，请评审以下方案", "prompt"),
    ("You are a helpful assistant. Answer concisely.", "prompt"),
    ("Act as a translator. Translate everything to French.", "prompt"),
    ("请你扮演面试官，提出五个问题", "prompt"),
    ("### Instruction\nSummarize the text", "prompt"),
    ("<system>be terse</system>", "prompt"),
    # text (incl. boundary cases)
    ("明天下午三点开会", "text"),
    ("Buy milk and eggs", "text"),
    ("ERROR: single occurrence", "text"),
    ("https://example.com is a great site", "text"),
    ("gitlab is down", "text"),
    ("/path with space", "text"),
    ("x = 1\ny = 2", "text"),
]

# ---------------------------------------------------------------- SG-1

GH_TOKEN = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"          # 36 after ghp_
GH_PAT = "github_pat_" + "11ABCDEFG0123456789abcdefghij"            # 29 after prefix
GOOGLE_KEY = "AIza" + "SyFAKE1234567890abcdefghijklmnopqrs"         # 35 after AIza
AWS_SECRET_40 = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"          # canonical fake, 40
AWS_SECRET_40B = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl+="         # 40

SG_CASES: list[tuple[str, bool, str | None, list[str]]] = [
    # SG-PEM
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEfakefake\n-----END RSA PRIVATE KEY-----",
     True, "hard", ["SG-PEM"]),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaGtleQ==\n-----END OPENSSH PRIVATE KEY-----",
     True, "hard", ["SG-PEM"]),
    # SG-PUTTY
    ("PuTTY-User-Key-File-3: ssh-rsa\nEncryption: none", True, "hard", ["SG-PUTTY"]),
    ("PuTTY-User-Key-File-2: ssh-ed25519", True, "hard", ["SG-PUTTY"]),
    # SG-AWS-ID
    ("AKIAIOSFODNN7EXAMPLE", True, "hard", ["SG-AWS-ID"]),
    ("my access id is AKIAIY5GZ2QXAMPLE777 ok", True, "hard", ["SG-AWS-ID"]),
    # SG-AWS-SECRET
    (f'aws_secret_access_key = "{AWS_SECRET_40}"', True, "hard", ["SG-AWS-SECRET"]),
    (f"AWS key: '{AWS_SECRET_40B}'", True, "hard", ["SG-AWS-SECRET"]),
    # SG-GH
    (GH_TOKEN, True, "hard", ["SG-GH"]),
    (f"token for CI: {GH_PAT}", True, "hard", ["SG-GH"]),
    # SG-SLACK
    ("xoxb-1234567890-abcdefghij", True, "hard", ["SG-SLACK"]),
    ("slack bot xoxp-9876543210-zyxwvut123 here", True, "hard", ["SG-SLACK"]),
    # SG-OPENAI
    ("sk-abcdefghijklmnopqrstuv", True, "hard", ["SG-OPENAI"]),
    ("sk-ant-api03-FAKEFAKEFAKEFAKEFAKE12", True, "hard", ["SG-OPENAI"]),
    # SG-GOOGLE
    (GOOGLE_KEY, True, "hard", ["SG-GOOGLE"]),
    (f"maps key {GOOGLE_KEY} in config", True, "hard", ["SG-GOOGLE"]),
    # SG-JWT
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
     True, "hard", ["SG-JWT"]),
    ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJ0ZXN0In0.c2lnbmF0dXJl",
     True, "hard", ["SG-JWT"]),
    # SG-ASSIGN
    ("password = hunter2hunter2", True, "hard", ["SG-ASSIGN"]),
    ("api_key: 0123456789abcdef", True, "hard", ["SG-ASSIGN"]),
    # SG-CONNSTR
    ("postgresql://admin:s3cretpw@db.local:5432/app", True, "hard", ["SG-CONNSTR"]),
    ("mongodb+srv://root:hunter22@cluster0.example.net/test", True, "hard", ["SG-CONNSTR"]),
    # SG-ENV
    ("DB_HOST=localhost\nDB_PASSWORD=hunter2\nDB_PORT=5432", True, "hard", ["SG-ENV"]),
    ("API_TOKEN=abc12345\nAPI_URL=https://x.example.com", True, "hard", ["SG-ENV"]),
    # SG-ENTROPY (suspect)
    ("q7VxT2mKp9LzR4wNb8YcJ3hFs6Dg", True, "suspect", ["SG-ENTROPY"]),
    ("VGhpc0lzQVZlcnlGYWtlU2VjcmV0S2V5MTIzNDU2Nzg5", True, "suspect", ["SG-ENTROPY"]),
    # negatives — common clipboard content that must NOT be flagged
    ("https://example.com/very/long/path?with=query&params=true", False, None, []),
    ("3f786850e387550fdab836ed7e6dc881de23001b", False, None, []),       # git sha1
    ("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",  # sha256
     False, None, []),
    ("5d41402abc4b2a76b9719d911017c592", False, None, []),               # md5
    ("550e8400-e29b-41d4-a716-446655440000", False, None, []),           # uuid
    ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk",     # png base64
     False, None, []),
    ("Meeting tomorrow at 3pm with the design team", False, None, []),
    ("今天的会议纪要已经整理好了", False, None, []),
    ('git commit -m "fix: update parser"', False, None, []),
    ("The password policy requires 12 characters", False, None, []),
    ("abc123XYZ", False, None, []),
    ("/usr/local/lib/python3.11/site-packages", False, None, []),
]


def validate() -> list[str]:
    """Sanity-check hand-authored expectations against the implementation."""
    from clipvault.core import classifier, normalize, secret_guard

    errors = []
    assert len(GH_TOKEN) == 4 + 36, "GH token length"
    assert len(GOOGLE_KEY) == 4 + 35, "Google key length"
    assert len(AWS_SECRET_40) == 40 and len(AWS_SECRET_40B) == 40, "AWS secret length"

    for raw, expected in NORM_CASES:
        got = normalize.normalize(raw)
        if got != expected:
            errors.append(f"NORM {raw!r}: expected {expected!r}, got {got!r}")
    for content, expected in CLS_CASES:
        got = classifier.classify(content)
        if got != expected:
            errors.append(f"CLS {content!r}: expected {expected}, got {got}")
    for content, is_secret, level, reasons in SG_CASES:
        v = secret_guard.scan(content)
        if (v.is_secret, v.level, sorted(v.reasons)) != (is_secret, level, sorted(reasons)):
            errors.append(
                f"SG {content!r}: expected ({is_secret},{level},{reasons}), "
                f"got ({v.is_secret},{v.level},{v.reasons})"
            )
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"{len(errors)} mismatches — nothing written:")
        for e in errors:
            print(" ", e)
        return 1

    out = REPO / "contracts" / "vectors"
    out.mkdir(parents=True, exist_ok=True)

    norm = [
        {"raw": raw, "normalized": exp,
         "hash": hashlib.sha256(exp.encode("utf-8")).hexdigest()}
        for raw, exp in NORM_CASES
    ]
    cls = [{"content": c, "expected_type": t} for c, t in CLS_CASES]
    sg = [
        {"content": c, "is_secret": s, "level": lv, "reasons": r}
        for c, s, lv, r in SG_CASES
    ]

    for name, data in [
        ("normalization.json", norm),
        ("classifier.json", cls),
        ("secret_guard.json", sg),
    ]:
        (out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(f"wrote {out / name} ({len(data)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
