"""Refuse to commit secrets or data (blueprint rule 6).

Runs as the pre-commit hook (.pre-commit-config.yaml) and as a standalone check:

    uv run python tools/check_no_secrets.py             # what is staged right now
    uv run python tools/check_no_secrets.py --all       # every tracked file
    uv run python tools/check_no_secrets.py --history   # every blob in every commit
    uv run python tools/check_no_secrets.py --scan desktop/artifacts   # build output, installers

Three independent guards, because each catches a different mistake:

1. paths     - `git add -A` sweeping up .env, a key file, or anything under data/ or logs/.
2. .env      - a real key or the SEC contact address pasted into code, a notebook or a doc.
                The values are read from the local .env and compared, never printed.
3. patterns  - a credential that is not in this machine's .env (a colleague's key, an old one).
                Skipped for the two files in PATTERN_EXEMPT, which hold deliberate fixtures; guard
                2 still applies to them.

--scan is guard 2 for files git never sees: compiled output and the installer, which may be
published as a GitHub release. Every file is compared byte for byte with the .env values in the
forms a build could hold them (text, UTF-16 as .NET stores strings, URL-encoded, base64), and zip
archives (.nupkg, .zip) are opened and checked inside. desktop/scripts/pack.ps1 runs it before and
after packaging.

The failure message names the file and the variable, never the secret.
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SELF = "tools/check_no_secrets.py"

# These two files define and exercise the patterns below, so they contain credential-shaped
# fixtures on purpose. Only the pattern check is skipped for them: their content is still compared
# against the real values in .env, so pasting an actual key here is still refused.
PATTERN_EXEMPT = {SELF, "tests/tools/test_check_no_secrets.py"}

# Paths that must never enter a commit, whatever they contain.
FORBIDDEN_PATHS = [
    re.compile(r"(^|/)\.env(\.|$)(?!example)"),      # .env, .env.local, .env.bak - but .env.example is fine
    re.compile(r"(^|/)(secrets?|credentials?|apikeys?)\.(ya?ml|toml|json|txt|ini)$"),
    re.compile(r"\.(pem|pfx|p12|key|keystore|jks)$"),
    re.compile(r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)$"),
    re.compile(r"^(data|logs|reports|site)/"),       # vendor data and run output (rule 6)
    re.compile(r"\.(duckdb|duckdb\.wal)$"),
]

# Credential shapes, for keys that are not in this machine's .env.
PATTERNS = [
    # A literal value only: quoted, or bare alphanumeric. `"api_key": self._key.get_secret_value()`
    # is how the code is supposed to read a key, so an attribute expression must not match.
    ("vendor token in a URL", re.compile(r"api[_-]?token=(?!YOUR|<|\$|\{|test|demo|your)[A-Za-z0-9]{16,}", re.I)),
    ("quoted API key literal", re.compile(r"""api[_-]?key["']?\s*[:=]\s*["'](?!YOUR|<|\$|\{|test|demo|your)"""
                                          r"""[A-Za-z0-9]{16,}["']""", re.I)),
    ("bare API key literal", re.compile(r"api[_-]?key\s*[:=]\s*(?!YOUR|<|\$|\{|test|demo|your)"
                                        r"[A-Za-z0-9]{20,}", re.I)),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
]

TEXT_SUFFIXES = {".py", ".pyi", ".ipynb", ".md", ".txt", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini",
                 ".bat", ".cmd", ".ps1", ".sh", ".html", ".css", ".js", ".svg", ".csv", ".env", ".example", "",
                 # the C# desktop app (desktop/)
                 ".cs", ".xaml", ".csproj", ".props", ".targets", ".sln", ".slnx", ".resx", ".config", ".xml",
                 ".manifest", ".editorconfig"}


def env_secrets() -> dict[str, str]:
    """`.env` values worth searching for, keyed by variable name. Values stay in memory."""
    out: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if len(value) < 8:
            continue
        out[name.strip()] = value
        for mail in re.findall(r"[^\s<>,;]+@[^\s<>,;]+\.[A-Za-z]{2,}", value):
            out[f"{name.strip()} (contact address)"] = mail       # e.g. the SEC_USER_AGENT email
    return out


def _run(args: list[str]) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def staged_paths() -> list[str]:
    return [p for p in _run(["diff", "--cached", "--name-only", "--diff-filter=ACMR"]).splitlines() if p]


def check_paths(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        for rx in FORBIDDEN_PATHS:
            if rx.search(p):
                bad.append(f"{p}: must never be committed (matches {rx.pattern!r})")
                break
    return bad


def check_content(label: str, path: str, text: str, secrets: dict[str, str]) -> list[str]:
    bad = []
    for name, value in secrets.items():
        if value in text:
            bad.append(f"{label}: contains the value of {name} from .env - remove it and read it from .env")
    if path in PATTERN_EXEMPT:
        return bad                                   # credential-shaped fixtures, checked above
    for what, rx in PATTERNS:
        if rx.search(text):
            bad.append(f"{label}: looks like a {what}")
    return bad


def readable(path: str, blob: bytes) -> str | None:
    if Path(path).suffix.lower() not in TEXT_SUFFIXES or len(blob) > 4_000_000:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_staged(secrets: dict[str, str]) -> list[str]:
    paths = staged_paths()
    bad = check_paths(paths)
    for p in paths:
        blob = subprocess.run(["git", "show", f":{p}"], cwd=ROOT, capture_output=True, check=False).stdout
        text = readable(p, blob)
        if text is not None:
            bad += check_content(p, p, text, secrets)
    return bad


def scan_tracked(secrets: dict[str, str]) -> list[str]:
    paths = [p for p in _run(["ls-files"]).splitlines() if p]
    bad = check_paths(paths)
    for p in paths:
        full = ROOT / p
        if not full.exists():
            continue
        text = readable(p, full.read_bytes())
        if text is not None:
            bad += check_content(p, p, text, secrets)
    return bad


def scan_history(secrets: dict[str, str]) -> list[str]:
    """Every blob in every commit on every ref - what a fresh clone of the remote could reveal."""
    bad = []
    seen_paths: set[str] = set()
    for line in _run(["rev-list", "--objects", "--all"]).splitlines():
        sha, _, path = line.partition(" ")
        if not path:
            continue
        if path not in seen_paths:
            seen_paths.add(path)
        blob = subprocess.run(["git", "cat-file", "-p", sha], cwd=ROOT, capture_output=True, check=False).stdout
        text = readable(path, blob)
        if text is not None:
            bad += check_content(f"{sha[:10]} {path}", path, text, secrets)
    bad += check_paths(sorted(seen_paths))
    return bad


def encodings(secrets: dict[str, str]) -> list[tuple[str, str, bytes]]:
    """Each .env value in the forms a compiled file could hold it, as (variable, form, bytes)."""
    out: list[tuple[str, str, bytes]] = []
    seen: set[bytes] = set()
    for name, value in secrets.items():
        for form, needle in (("text", value.encode()), ("UTF-16 text", value.encode("utf-16-le")),
                             ("URL-encoded", quote(value, safe="").encode()),
                             ("base64", base64.b64encode(value.encode()).rstrip(b"="))):
            if needle not in seen:
                seen.add(needle)
                out.append((name, form, needle))
    return out


def _scan_blob(label: str, blob: bytes, needles: list[tuple[str, str, bytes]], depth: int = 0) -> list[str]:
    bad = [f"{label}: contains the value of {name} from .env ({form})"
           for name, form, needle in needles if needle in blob]
    if depth < 3 and zipfile.is_zipfile(io.BytesIO(blob)):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for entry in archive.infolist():
                    if not entry.is_dir():
                        bad += _scan_blob(f"{label} > {entry.filename}", archive.read(entry), needles, depth + 1)
        except (zipfile.BadZipFile, NotImplementedError):
            pass      # not really an archive; its raw bytes were checked above
    return bad


def scan_files(roots: list[str], secrets: dict[str, str]) -> tuple[list[str], int]:
    """Build output and packages, which git never sees: every file under each root (archives opened)
    compared with the .env values. Credential patterns are not used here, because compiled files are
    full of strings shaped like tokens. Returns the problems and the number of files read."""
    needles = encodings(secrets)
    bad: list[str] = []
    scanned = 0
    for root in roots:
        base = Path(root)
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        if not files:
            bad.append(f"{root}: no files to scan")
        for path in files:
            scanned += 1
            bad += _scan_blob(str(path), path.read_bytes(), needles)
    return bad, scanned


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="scan every tracked file, not just staged changes")
    ap.add_argument("--history", action="store_true", help="scan every blob in every commit")
    ap.add_argument("--scan", nargs="+", metavar="PATH",
                    help="scan build output or packages that git never sees (files or folders; archives are opened)")
    args = ap.parse_args()

    secrets = env_secrets()
    if args.scan:
        bad, scanned = scan_files(args.scan, secrets)
        what = f"build output ({scanned} files, archives opened, {len(encodings(secrets))} encoded forms)"
        if not bad:
            checked = f"{len(secrets)} .env value(s)" if secrets else "no local .env"
            print(f"ok: no .env value in {what}, compared against {checked}")
            return 0
    elif args.history:
        what, bad = "commit history", scan_history(secrets)
    elif args.all:
        what, bad = "tracked files", scan_tracked(secrets)
    else:
        what, bad = "staged changes", scan_staged(secrets)

    if bad:
        print(f"BLOCKED: {len(bad)} problem(s) in {what}\n", file=sys.stderr)
        for line in bad:
            print(f"  - {line}", file=sys.stderr)
        print("\nSecrets belong in .env (git-ignored); data and run output stay under data/, logs/, "
              "reports/ and site/.", file=sys.stderr)
        return 1
    checked = f"{len(secrets)} .env value(s)" if secrets else "no local .env"
    print(f"ok: no secrets or data in {what} (compared against {checked}, {len(PATTERNS)} patterns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
