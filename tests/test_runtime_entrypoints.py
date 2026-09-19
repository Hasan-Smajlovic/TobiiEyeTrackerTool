from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import gaze_mouse.main as main_module
from gaze_mouse import (
    app_icon,
    dpi,
    logging_setup,
    tobii_stream_engine_bridge,
    windows_keyboard,
    windows_startup,
)


def test_app_icon_finds_project_asset_and_returns_empty_icon_when_missing(monkeypatch, tmp_path):
    root = tmp_path / "app"
    icon = root / "assets" / "icon.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"not a real image")
    monkeypatch.setattr(app_icon, "get_project_root", lambda: root)

    assert app_icon.app_icon_path() == icon

    icon.unlink()
    monkeypatch.setattr(app_icon, "__file__", str(tmp_path / "package" / "app_icon.py"))
    assert app_icon.app_icon_path() is None
    assert app_icon.load_app_icon().isNull()


def test_app_icon_prefers_frozen_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    icon = bundle / "assets" / "icon.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"icon")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(app_icon, "get_project_root", lambda: tmp_path / "other")

    assert app_icon.app_icon_path() == icon


def test_dpi_awareness_uses_modern_api_then_falls_back(monkeypatch):
    calls = []

    class Shcore:
        def SetProcessDpiAwareness(self, value):
            calls.append(("modern", value))

    class User32:
        def SetProcessDPIAware(self):
            calls.append(("fallback", None))

    monkeypatch.setattr(dpi.sys, "platform", "win32")
    monkeypatch.setattr(
        "ctypes.windll",
        type("Windll", (), {"shcore": Shcore(), "user32": User32()})(),
        raising=False,
    )
    dpi.enable_windows_dpi_awareness()
    assert calls == [("modern", 2)]

    calls.clear()

    class BrokenShcore:
        def SetProcessDpiAwareness(self, _value):
            raise OSError("unsupported")

    monkeypatch.setattr(
        "ctypes.windll",
        type("Windll", (), {"shcore": BrokenShcore(), "user32": User32()})(),
        raising=False,
    )
    dpi.enable_windows_dpi_awareness()
    assert calls == [("fallback", None)]


def test_stream_to_logger_buffers_lines_and_exposes_file_interface():
    records = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    class Fallback:
        def fileno(self):
            return 7

    logger = logging.getLogger("test.stream")
    logger.handlers = [ListHandler()]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    stream = logging_setup.StreamToLogger(logger, logging.INFO, Fallback())

    assert stream.write("first\npartial") == len("first\npartial")
    stream.flush()

    assert records == ["first", "partial"]
    assert stream.fileno() == 7
    assert stream.encoding == "utf-8"
    assert stream.errors == "replace"
    assert stream.isatty() is False
    assert stream.writable() is True


def test_logging_setting_and_archive_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(logging_setup, "get_project_root", lambda: tmp_path)
    settings = tmp_path / "data" / "app_settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"gaze": {"logging_enabled": "off"}}), encoding="utf-8")
    assert logging_setup._read_logging_enabled_setting() is False

    latest = tmp_path / "logs" / "latest.txt"
    latest.parent.mkdir()
    latest.write_text("old log", encoding="utf-8")
    archived = logging_setup._archive_latest_log(latest)
    assert archived is not None
    assert archived.read_text(encoding="utf-8") == "old log"
    assert latest.read_text(encoding="utf-8") == "old log"


def test_windows_keyboard_candidates_and_fallback(monkeypatch):
    monkeypatch.setattr(windows_keyboard.sys, "platform", "win32")
    attempts = []

    def shell_execute(target):
        attempts.append(target)
        if len(attempts) == 1:
            raise windows_keyboard.WindowsKeyboardError("blocked")

    monkeypatch.setattr(windows_keyboard, "_shell_execute", shell_execute)

    label = windows_keyboard.open_windows_keyboard()

    assert label == "Windows on-screen keyboard"
    assert len(attempts) == 2
    targets = [target.lower() for _label, target in windows_keyboard._keyboard_candidates()]
    assert len(targets) == len(set(targets))


def test_windows_keyboard_rejects_other_platforms(monkeypatch):
    monkeypatch.setattr(windows_keyboard.sys, "platform", "linux")
    with pytest.raises(windows_keyboard.WindowsKeyboardError, match="only available on Windows"):
        windows_keyboard.open_windows_keyboard()


def test_bridge_emit_helpers_write_json(capsys):
    tobii_stream_engine_bridge._emit_gaze(0.25, 0.75, 10)
    tobii_stream_engine_bridge._emit_eye_status(True, False, 11)

    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0]) == {"type": "gaze", "x": 0.25, "y": 0.75, "timestamp": 10}
    assert json.loads(lines[1]) == {
        "type": "eyes",
        "left_open": True,
        "right_open": False,
        "timestamp": 11,
    }


def test_bridge_main_rejects_64_bit_python(monkeypatch, capsys):
    monkeypatch.setattr(tobii_stream_engine_bridge, "_pointer_size", lambda: 8)

    assert tobii_stream_engine_bridge.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "type": "error",
        "message": "Tobii bridge must run with 32-bit Python.",
    }


@pytest.mark.parametrize(
    ("arguments", "simulate_gaze"),
    [([], False), ([main_module.MOUSE_GAZE_SIMULATION_ARG], True)],
)
def test_main_builds_and_runs_application(monkeypatch, tmp_path, arguments, simulate_gaze):
    import PySide6.QtWidgets

    from gaze_mouse import toolbar

    calls = []
    test_app = PySide6.QtWidgets.QApplication.instance()

    class FakeIcon:
        def isNull(self):
            return False

    class FakeApp:
        def __init__(self, args):
            calls.append(("app", args))

        def setApplicationName(self, name):
            calls.append(("name", name))

        def setOrganizationName(self, name):
            calls.append(("org", name))

        def setWindowIcon(self, _icon):
            calls.append(("app_icon", True))

        def exec(self):
            return 17

        @staticmethod
        def instance():
            return test_app

    class FakeWindow:
        def __init__(self, *, simulate_gaze=False):
            calls.append(("simulate_gaze", simulate_gaze))

        def setWindowIcon(self, _icon):
            calls.append(("window_icon", True))

        def show(self):
            calls.append(("show", True))

    monkeypatch.setattr(
        main_module, "setup_application_logging", lambda: calls.append(("logging", True))
    )
    monkeypatch.setattr(
        main_module, "enable_windows_dpi_awareness", lambda: calls.append(("dpi", True))
    )
    monkeypatch.setattr(
        main_module, "install_qt_message_handler", lambda: calls.append(("qt_logging", True))
    )
    monkeypatch.setattr(PySide6.QtWidgets, "QApplication", FakeApp)
    monkeypatch.setattr(app_icon, "load_app_icon", FakeIcon)
    monkeypatch.setattr(app_icon, "app_icon_path", lambda: tmp_path / "icon.png")
    monkeypatch.setattr(toolbar, "HotbarWindow", FakeWindow)
    monkeypatch.setattr(main_module.sys, "argv", ["run_gaze_mouse.py", *arguments])

    assert main_module.main() == 17
    assert ("show", True) in calls
    assert ("name", "Pogled Assist") in calls
    assert ("org", "Pogled Assist") in calls
    assert ("simulate_gaze", simulate_gaze) in calls


def test_frozen_application_rejects_mouse_gaze_simulation(monkeypatch, capsys):
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        main_module.sys,
        "argv",
        ["PogledAssist.exe", main_module.MOUSE_GAZE_SIMULATION_ARG],
    )

    assert main_module.main() == 2
    assert "only from a development checkout" in capsys.readouterr().out


def test_frozen_startup_launcher_is_next_to_executable(monkeypatch, tmp_path):
    executable = tmp_path / "PogledAssist.exe"
    monkeypatch.setattr(windows_startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_startup.sys, "executable", str(executable))

    assert windows_startup.launcher_script_path() == tmp_path / "start_gaze_mouse.ps1"


def test_package_smoke_loads_the_bundled_suggestion_model(monkeypatch, tmp_path):
    report = tmp_path / "smoke.txt"
    monkeypatch.setenv(main_module.PACKAGE_SMOKE_REPORT_ENV, str(report))

    assert main_module.package_smoke_test() == 0
    contents = report.read_text(encoding="utf-8")
    assert "suggestions_loaded=True" in contents
    assert "bosnian-model.json.gz exists=True" in contents
    assert "bosnian-model.meta.json exists=True" in contents


def test_windows_package_declares_the_bundled_suggestion_files():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "windows" / "PogledAssist.spec").read_text(encoding="utf-8")
    build = (root / "scripts" / "build_windows_package.ps1").read_text(encoding="utf-8")

    for name in ("bosnian-model.json.gz", "bosnian-model.meta.json"):
        assert name in spec
        assert name in build


def test_main_routes_package_smoke_test_without_starting_gui(monkeypatch):
    monkeypatch.setattr(main_module.sys, "argv", ["PogledAssist.exe", "--package-smoke-test"])
    monkeypatch.setattr(main_module, "package_smoke_test", lambda: 23)

    assert main_module.main() == 23


def test_source_tree_passes_package_smoke_test():
    assert main_module.package_smoke_test() == 0


def test_setup_application_logging_handles_missing_standard_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "__stdout__", None)
    monkeypatch.setattr(sys, "__stderr__", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(logging_setup, "get_project_root", lambda: tmp_path)

    try:
        latest_log = logging_setup.setup_application_logging()
        root_logger = logging.getLogger()
        stream_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        ]
        assert not stream_handlers

        print("hello from stdout")
        sys.stderr.write("hello from stderr\n")
        logging.shutdown()

        content = latest_log.read_text(encoding="utf-8")
        assert "Logging initialized." in content
        assert "hello from stdout" in content
        assert "hello from stderr" in content
    finally:
        logging_setup._restore_standard_streams()
        logging_setup._clear_root_handlers()


def test_stream_to_logger_reentrancy_protection():
    calls = []

    class ReentrantLogger:
        def log(self, level, message):
            calls.append((level, message))
            stream.write("recursive message\n")

    logger = ReentrantLogger()
    stream = logging_setup.StreamToLogger(logger, logging.INFO, fallback_stream=None)
    stream.write("initial message\n")

    assert calls == [(logging.INFO, "initial message")]
