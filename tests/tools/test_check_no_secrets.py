"""The commit guard (tools/check_no_secrets.py): it blocks leaks and never echoes a secret."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_no_secrets", ROOT / "tools" / "check_no_secrets.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

SECRETS = {"EODHD_API_KEY": "6f1d2a9b4c8e0f3a7b5d1e2c", "SEC_USER_AGENT (contact address)": "a@b.example"}


@pytest.mark.parametrize("path", [
    ".env", ".env.local", ".env.bak", "sub/.env.production", "secrets.yaml", "credentials.json",
    "server.pem", "client.p12", "deploy.key", "id_rsa", "data/raw/eodhd/eod/x.parquet",
    "logs/eqrisk.jsonl", "reports/validation/summary.json", "site/index.html", "model.duckdb",
])
def test_forbidden_paths_are_blocked(path: str) -> None:
    assert guard.check_paths([path]), f"{path} should be refused"


@pytest.mark.parametrize("path", [
    ".env.example", "eqrisk/config.py", "configs/model.yaml", "docs/BLUEPRINT.md",
    "app/main.py", "tests/fixtures/fja_sp500.csv", "desktop/src/EQRisk.Desktop/App.xaml.cs",
    "desktop/scripts/pack.ps1",
])
def test_normal_paths_are_allowed(path: str) -> None:
    assert guard.check_paths([path]) == []


@pytest.mark.parametrize("text", [
    "token = 6f1d2a9b4c8e0f3a7b5d1e2c",                                  # a real .env value, pasted
    "contact a@b.example for access",                                    # the SEC contact address
    'api_key: "AbCd1234EfGh5678XyZw"',                                   # a quoted key literal
    "https://x.example/eod/AAPL?api_token=9c8b7a6d5e4f3a2b1c0d&fmt=json",
    "OPENAI = sk-abcdefghijklmnopqrstuvwxyz012345",
    "gho_abcdefghijklmnopqrstuvwxyz01",
    "AWS AKIAIOSFODNN7EXAMPLE here",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_leaked_content_is_blocked(text: str) -> None:
    assert guard.check_content("f.md", "f.md", text, SECRETS), f"should be refused: {text[:40]}"


@pytest.mark.parametrize("text", [
    'params = {"api_token": self._key.get_secret_value(), "fmt": "json"}',   # how the code reads a key
    'params = {"api_key": key.get_secret_value(), "file_type": "json"}',
    "EODHD_API_KEY=",                                                        # the .env.example template
    "SEC_USER_AGENT=Your Name your.address@example.com",
    "api_key = os.environ['EODHD_API_KEY']",
    'api_token=YOUR_TOKEN_HERE',
    "eodhd_api_key: SecretStr | None = None",
])
def test_ordinary_code_is_not_flagged(text: str) -> None:
    assert guard.check_content("f.py", "f.py", text, SECRETS) == [], f"false positive: {text[:50]}"


def test_the_message_never_contains_the_secret() -> None:
    """A blocked commit prints to a terminal and often into a log; it must stay safe to share."""
    for text in ("token 6f1d2a9b4c8e0f3a7b5d1e2c", "mail a@b.example"):
        problems = guard.check_content("f.md", "f.md", text, SECRETS)
        assert problems
        for line in problems:
            for value in SECRETS.values():
                assert value not in line, line


def test_the_guard_does_not_flag_itself() -> None:
    """It contains the patterns it searches for, so it is skipped by path."""
    src = (ROOT / "tools" / "check_no_secrets.py").read_text(encoding="utf-8")
    assert guard.check_content(guard.SELF, guard.SELF, src, SECRETS) == []


def test_env_values_are_read_but_short_ones_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text('EODHD_API_KEY="longenoughvalue123"\nSHORT=abc\n'
                                   "SEC_USER_AGENT=Fan Zhu someone@example.com\n"
                                   "# comment\n\n", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    found = guard.env_secrets()
    assert found["EODHD_API_KEY"] == "longenoughvalue123"
    assert "SHORT" not in found                                  # too short to search for safely
    assert found["SEC_USER_AGENT (contact address)"] == "someone@example.com"


def test_scan_finds_a_key_in_build_output_in_any_form_and_inside_packages(tmp_path: Path) -> None:
    """--scan, which pack.ps1 runs on the publish folder and the installer before anything ships."""
    key = SECRETS["EODHD_API_KEY"]
    (tmp_path / "EQRisk.dll").write_bytes(b"MZ\x90\x00 no key in here")
    (tmp_path / "appsettings.json").write_text(f'{{"token": "{key}"}}', encoding="utf-8")
    (tmp_path / "Strings.resources").write_bytes(key.encode("utf-16-le"))       # how .NET stores strings
    with zipfile.ZipFile(tmp_path / "EQRiskDesktop-0.2.0-full.nupkg", "w") as package:
        package.writestr("lib/app/config.ini", f"key={key}")
    problems, scanned = guard.scan_files([str(tmp_path)], SECRETS)
    assert scanned == 4
    for leaked in ("appsettings.json", "Strings.resources", "config.ini"):
        assert any(leaked in p for p in problems), leaked
    assert not any("EQRisk.dll" in p for p in problems)
    for line in problems:
        assert key not in line, line                                            # never echoed


def test_scan_of_clean_build_output_passes(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "EQRisk.exe").write_bytes(b"MZ clean")
    with zipfile.ZipFile(tmp_path / "EQRiskDesktop-win-Portable.zip", "w") as package:
        package.writestr("EQRisk.exe", b"MZ clean")
    assert guard.scan_files([str(tmp_path)], SECRETS) == ([], 2)


def test_pattern_exemption_only_skips_patterns() -> None:
    """The two fixture files carry credential-shaped test data on purpose, so the pattern check is
    skipped for them. Guard 2 is not: a real .env value pasted there is still refused."""
    for path in guard.PATTERN_EXEMPT:
        assert guard.check_content(path, path, "AKIAIOSFODNN7EXAMPLE", SECRETS) == []
        assert guard.check_content(path, path, "token 6f1d2a9b4c8e0f3a7b5d1e2c", SECRETS)
        assert guard.check_content(path, path, "mail a@b.example", SECRETS)
