from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="The release installer uses Windows PowerShell and Windows executables.",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = REPO_ROOT / "packaging" / "windows" / "install_windows.ps1"
PACKAGE_VERSION = "0.2.0"

SMOKE_APP_SOURCE = r"""
using System;
using System.IO;
using System.Threading;

public static class Program
{
    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--hold")
        {
            Thread.Sleep(60000);
            return 0;
        }

        string failureRoot = Environment.GetEnvironmentVariable("FAKE_SMOKE_FAIL_ROOT");
        string currentRoot = Path.GetFullPath(Environment.CurrentDirectory)
            .TrimEnd(Path.DirectorySeparatorChar);
        if (
            failureRoot == "all" ||
            String.Equals(
                currentRoot,
                failureRoot,
                StringComparison.OrdinalIgnoreCase
            )
        )
        {
            string report = Environment.GetEnvironmentVariable(
                "POGLED_ASSIST_PACKAGE_SMOKE_REPORT"
            );
            if (!String.IsNullOrWhiteSpace(report))
            {
                File.WriteAllText(report, "forced smoke failure");
            }
            return 7;
        }

        return 0;
    }
}
"""


def _compile_smoke_app(tmp_path: Path) -> Path:
    source_path = tmp_path / "SmokeApp.cs"
    executable_path = tmp_path / "PogledAssist.exe"
    compiler_path = tmp_path / "compile-smoke-app.ps1"
    source_path.write_text(SMOKE_APP_SOURCE, encoding="utf-8")
    compiler_path.write_text(
        "param([string]$SourcePath, [string]$OutputPath)\n"
        "Add-Type -Path $SourcePath -OutputAssembly $OutputPath "
        "-OutputType ConsoleApplication\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(compiler_path),
            "-SourcePath",
            str(source_path),
            "-OutputPath",
            str(executable_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert executable_path.is_file()
    return executable_path


def _release_package(tmp_path: Path, smoke_app: Path) -> Path:
    package = tmp_path / "release" / "PogledAssist"
    (package / "_internal").mkdir(parents=True)
    shutil.copy2(smoke_app, package / "PogledAssist.exe")
    shutil.copy2(INSTALLER_SCRIPT, package / "install_windows.ps1")
    (package / "start_gaze_mouse.ps1").write_text("# fake launcher\n", encoding="utf-8")
    (package / "update_windows.ps1").write_text("# fake updater\n", encoding="utf-8")
    (package / "README.md").write_text("fake release\n", encoding="utf-8")
    (package / "VERSION").write_text(f"{PACKAGE_VERSION}\n", encoding="utf-8")
    (package / "new-app-file.txt").write_text("new application\n", encoding="utf-8")
    return package


def _existing_installation(tmp_path: Path) -> Path:
    install_root = tmp_path / "installed"
    (install_root / "data").mkdir(parents=True)
    (install_root / "logs").mkdir()
    (install_root / "data" / "app_settings.json").write_bytes(b'{"keep":true}')
    (install_root / "data" / "speech_phrases.json").write_bytes(b'["keep"]')
    (install_root / "data" / "speech_learning.json").write_bytes(b'{"keep":"learning"}')
    (install_root / "logs" / "latest.txt").write_bytes(b"existing log\r\n")
    (install_root / "install_info.json").write_bytes(b'{"keep":"metadata"}')
    (install_root / "setup_windows.log").write_bytes(b"existing setup log\r\n")
    (install_root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (install_root / "old-app-file.txt").write_text("old application\n", encoding="utf-8")
    return install_root


def _installer_environment(**overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PSMODULEPATH", None)
    environment.update(overrides)
    return environment


def _run_installer(
    package: Path,
    install_root: Path,
    *,
    expected_version: str = PACKAGE_VERSION,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(package / "install_windows.ps1"),
            "-InstallRoot",
            str(install_root),
            "-ExpectedVersion",
            expected_version,
            "-NoElevation",
            "-NoDesktopShortcut",
        ],
        cwd=package,
        env=environment or _installer_environment(),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _persistent_snapshot(install_root: Path) -> dict[str, bytes]:
    return {
        relative_path: (install_root / relative_path).read_bytes()
        for relative_path in (
            "data/app_settings.json",
            "data/speech_phrases.json",
            "data/speech_learning.json",
            "logs/latest.txt",
            "install_info.json",
            "setup_windows.log",
        )
    }


def _assert_no_transaction_files(install_root: Path) -> None:
    leftovers = list(install_root.parent.glob(f".{install_root.name}.install-*"))
    leftovers += list(install_root.parent.glob(f".{install_root.name}.backup-*"))
    assert leftovers == []


def test_installer_swaps_verified_package_and_preserves_persistent_content(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)
    persistent_before = _persistent_snapshot(install_root)

    completed = _run_installer(package, install_root)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == PACKAGE_VERSION
    assert (install_root / "new-app-file.txt").is_file()
    assert not (install_root / "old-app-file.txt").exists()
    assert _persistent_snapshot(install_root) == persistent_before
    _assert_no_transaction_files(install_root)


def test_installed_smoke_failure_restores_previous_installation(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)
    persistent_before = _persistent_snapshot(install_root)
    environment = _installer_environment(FAKE_SMOKE_FAIL_ROOT=str(install_root.resolve()))

    completed = _run_installer(
        package,
        install_root,
        environment=environment,
    )

    assert completed.returncode != 0
    assert "Installed application verification failed" in completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert (install_root / "old-app-file.txt").is_file()
    assert not (install_root / "new-app-file.txt").exists()
    assert _persistent_snapshot(install_root) == persistent_before
    _assert_no_transaction_files(install_root)


def test_staged_smoke_failure_never_changes_existing_installation(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)
    persistent_before = _persistent_snapshot(install_root)
    environment = _installer_environment(FAKE_SMOKE_FAIL_ROOT="all")

    completed = _run_installer(
        package,
        install_root,
        environment=environment,
    )

    assert completed.returncode != 0
    assert "Staged application verification failed" in completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert (install_root / "old-app-file.txt").is_file()
    assert _persistent_snapshot(install_root) == persistent_before
    _assert_no_transaction_files(install_root)


def test_interrupted_directory_swap_is_recovered_before_next_attempt(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)
    persistent_before = _persistent_snapshot(install_root)
    backup_root = install_root.parent / f".{install_root.name}.backup-interrupted"
    staging_root = install_root.parent / f".{install_root.name}.install-interrupted"
    transaction_path = install_root.parent / f".{install_root.name}.install-transaction.json"
    install_root.rename(backup_root)
    staging_root.mkdir()
    (staging_root / "partial.txt").write_text("partial update\n", encoding="utf-8")
    transaction_path.write_text(
        json.dumps(
            {
                "InstallRoot": str(install_root.resolve()),
                "StagingRoot": str(staging_root.resolve()),
                "BackupRoot": str(backup_root.resolve()),
            }
        ),
        encoding="utf-8",
    )

    completed = _run_installer(
        package,
        install_root,
        environment=_installer_environment(FAKE_SMOKE_FAIL_ROOT="all"),
    )

    assert completed.returncode != 0
    assert "Restored the previous installation" in completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert (install_root / "old-app-file.txt").is_file()
    assert _persistent_snapshot(install_root) == persistent_before
    _assert_no_transaction_files(install_root)


def test_running_application_blocks_install_before_files_change(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)
    running_app = subprocess.Popen([str(smoke_app), "--hold"])
    try:
        time.sleep(0.3)
        completed = _run_installer(package, install_root)
    finally:
        running_app.terminate()
        running_app.wait(timeout=5)

    assert completed.returncode != 0
    assert "Close Pogled Assist" in completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert (install_root / "old-app-file.txt").is_file()


def test_expected_version_mismatch_stops_before_staging(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)
    install_root = _existing_installation(tmp_path)

    completed = _run_installer(package, install_root, expected_version="9.9.9")

    assert completed.returncode != 0
    assert "does not match expected release" in completed.stdout + completed.stderr
    assert (install_root / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert (install_root / "old-app-file.txt").is_file()


def test_legacy_install_root_is_never_modified(tmp_path):
    smoke_app = _compile_smoke_app(tmp_path)
    package = _release_package(tmp_path, smoke_app)

    completed = _run_installer(package, Path(r"C:\TobiiExec"))

    assert completed.returncode != 0
    output = " ".join((completed.stdout + completed.stderr).split())
    assert "belongs to the previous application and will not be changed" in output
