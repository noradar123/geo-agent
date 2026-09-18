import subprocess

import pytest

from scripts import check_secrets


@pytest.mark.parametrize(
    "path",
    [".env", ".env.backup", "runs/scene.png", ".browser-profile/Default/Cookies", "config.json"],
)
def test_private_files_are_blocked(path):
    assert check_secrets.prohibited(path)
    assert not check_secrets.prohibited(".env.example")


def test_secret_detection_reports_labels_without_values(monkeypatch):
    local_key = b"local-" + b"test-value-" * 3
    fake_api_key = b"sk-" + b"example" * 8
    monkeypatch.setattr(check_secrets, "git", lambda *a: local_key + b" " + fake_api_key)
    findings = check_secrets.check([("source.py", "blob")], [local_key])
    assert ("source.py", "matches a local API key") in findings
    assert ("source.py", "OpenAI key") in findings
    assert local_key.decode() not in str(findings)
    assert fake_api_key.decode() not in str(findings)


def test_keys_in_filenames_are_redacted():
    key = b"sk-" + b"example" * 8
    assert check_secrets.redacted_path(f"file-{key.decode()}.txt", [key]) == "file-[REDACTED].txt"


def test_removed_secret_is_still_detected_in_history(monkeypatch, tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True).stdout

    git("init")
    git("config", "user.name", "Secret scanner test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    fake_api_key = b"sk-" + b"example" * 8
    source = tmp_path / "source.txt"
    source.write_bytes(fake_api_key)
    git("add", "source.txt")
    git("commit", "-m", "Synthetic test fixture")
    source.write_text("Clean current content", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "Remove synthetic token")
    monkeypatch.setattr(check_secrets, "git", git)
    assert not check_secrets.check(check_secrets.staged_blobs(), [])
    assert ("source.txt", "OpenAI key") in check_secrets.check(check_secrets.history_blobs(), [])
