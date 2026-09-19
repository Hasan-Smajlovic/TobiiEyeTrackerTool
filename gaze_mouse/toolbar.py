"""Top hotbar UI."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QCursor, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QStyle,
    QToolButton,
    QWidget,
)

from .appbar import WindowsAppBar
from .controller_window import CONTROLLER_WINDOW_ACTION_PREFIX, ControllerWindow
from .gaze_bubble import GazeBubbleWindow
from .gaze_feedback import set_gaze_feedback
from .gaze_provider import TobiiGazeProvider
from .interaction_overlay import InteractionOverlayWindow
from .keyboard_window import KEYBOARD_WINDOW_ACTION_PREFIX, KeyboardWindow
from .logging_setup import get_project_root
from .mouse_controller import (
    CLICK_ACTIONS,
    CONTROLLER,
    DOUBLE_LEFT_CLICK,
    HIDE_HOTBAR,
    KEYBOARD,
    LEFT_CLICK,
    QUICK_ACTIONS,
    RIGHT_CLICK,
    SETTINGS,
    SHOW_HOTBAR,
    SPEECH,
    GazeMouseController,
)
from .mouse_gaze_provider import MouseGazeProvider
from .quick_action_menu import CANCEL_QUICK_ACTION, QuickActionRadialMenu
from .quick_action_zoom import QuickActionZoomWindow
from .release_update import ReleaseUpdateError, ReleaseUpdateManager
from .settings_store import load_app_settings, save_app_settings
from .settings_window import SettingsWindow
from .speech_library import speech_library_store
from .speech_service import SpeechService
from .speech_window import SPEECH_WINDOW_ACTION_PREFIX, SpeechWindow
from .suggestion_service import SuggestionService
from .tobii_calibration import launch_tobii_guest_calibration
from .windows_input import WindowsInputController

logger = logging.getLogger(__name__)

CLICK_BUTTONS = [
    (LEFT_CLICK, "Lijevi klik", "fa5s.mouse-pointer"),
    (RIGHT_CLICK, "Desni klik", "fa5s.mouse"),
    (DOUBLE_LEFT_CLICK, "Dvostruki klik", "fa5s.hand-pointer"),
]
SECONDARY_BUTTONS = [
    (SPEECH, "Govor", "fa5s.microphone"),
    (KEYBOARD, "Tastatura", "fa5s.keyboard"),
    (CONTROLLER, "Upravljač", "fa5s.gamepad"),
]
SETTINGS_BUTTON = (SETTINGS, "Postavke", "fa5s.cog")
QUICK_ACTION_BUTTON = (QUICK_ACTIONS, "Brze radnje", "fa5s.bolt")
SPEECH_TEST_TEXT = "Zdravo. Ovo je test govora na bosanskom jeziku."


class HotbarWindow(QWidget):
    """Frameless top toolbar that reserves desktop work area on Windows."""

    BAR_HEIGHT = 76

    def __init__(self, *, simulate_gaze: bool = False) -> None:
        super().__init__()
        logger.info("Creating hotbar window.")
        self.setWindowTitle("Pogled Assist")
        self.setWindowFlags(_no_focus_tool_window_flags())
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.BAR_HEIGHT)

        self._started = False
        self._buttons: dict[str, QToolButton] = {}
        self._appbar = WindowsAppBar()
        self._gaze = MouseGazeProvider(self) if simulate_gaze else TobiiGazeProvider(self)
        self._initial_gaze_settings, self._initial_speech_settings = load_app_settings()
        self._speech = SpeechService()
        self._speech.update_settings(self._initial_speech_settings)
        self._speech_library_store = speech_library_store(get_project_root())
        self._speech_library_store.load()
        self._suggestions = SuggestionService(
            self, path=get_project_root() / "data" / "speech_learning.json"
        )
        self._release_update_manager = ReleaseUpdateManager(parent=self)
        self._speech_window: SpeechWindow | None = None
        self._keyboard_window: KeyboardWindow | None = None
        self._controller_window: ControllerWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._restore_button: QToolButton | None = None
        self._zoom_context: str | None = None
        self._foreground_input: WindowsInputController | None = None
        self._last_external_foreground_window: int | None = None
        self._last_external_cursor_position: tuple[int, int] | None = None
        self._foreground_timer = QTimer(self)
        self._foreground_timer.setInterval(250)
        self._foreground_timer.timeout.connect(self._update_last_external_foreground_window)
        self._quick_menu = QuickActionRadialMenu()
        self._quick_zoom = QuickActionZoomWindow()
        self._mouse = GazeMouseController(
            self.action_at_global_point,
            self.action_center_at_global_point,
            self.contains_global_point,
            self,
            pointer_movement_enabled=not simulate_gaze,
        )
        self._mouse.update_settings(self._initial_gaze_settings)
        self._gaze_bubble = GazeBubbleWindow()
        self._gaze_bubble.set_enabled(self._mouse.settings.show_gaze_bubble)
        self._interaction_overlay = InteractionOverlayWindow()
        self._interaction_overlay.set_enabled(self._mouse.settings.show_interaction_overlay)

        self._build_ui()
        self._build_restore_button()
        self._connect_signals()
        self._install_shortcuts()
        self._prime_foreground_tracking()
        logger.info("Hotbar window initialized.")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        logger.info("Hotbar show event received.")
        self._position_on_primary_screen()
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start_services)

    def closeEvent(self, event: QCloseEvent) -> None:
        logger.info("Hotbar close event received.")
        if self._speech_window is not None:
            self._speech_window.close()
        if self._keyboard_window is not None:
            self._keyboard_window.close()
        if self._controller_window is not None:
            self._controller_window.close()
        if self._settings_window is not None:
            self._settings_window.close()
        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._gaze_bubble.set_enabled(False)
        self._interaction_overlay.set_enabled(False)
        self._gaze_bubble.close()
        self._interaction_overlay.close()
        self._foreground_timer.stop()
        if self._restore_button is not None:
            self._restore_button.close()
        self._speech.stop()
        self._suggestions.close()
        self._gaze.stop()
        self._appbar.unregister()
        super().closeEvent(event)

    def action_at_global_point(self, point: QPoint) -> str | None:
        if self._quick_zoom.isVisible():
            return None

        if self._quick_menu.isVisible():
            return None

        if self._speech_window is not None and self._speech_window.contains_global_point(point):
            return self._speech_window.action_at_global_point(point)

        if self._keyboard_window is not None and self._keyboard_window.contains_global_point(point):
            return self._keyboard_window.action_at_global_point(point)

        if self._controller_window is not None and self._controller_window.contains_global_point(
            point
        ):
            return self._controller_window.action_at_global_point(point)

        if self._settings_window is not None and self._settings_window.isVisible():
            return None

        for action, button in self._buttons.items():
            if not button.isVisible() or not button.isEnabled():
                continue

            top_left = button.mapToGlobal(QPoint(0, 0))
            rect = QRect(top_left, button.size())
            if rect.contains(point):
                return action

        return None

    def action_center_at_global_point(self, action: str, point: QPoint) -> QPoint | None:
        if self._quick_zoom.isVisible():
            return None

        if self._quick_menu.isVisible():
            return None

        if self._speech_window is not None and self._speech_window.contains_global_point(point):
            return self._speech_window.action_center_at_global_point(action, point)

        if self._keyboard_window is not None and self._keyboard_window.contains_global_point(point):
            return self._keyboard_window.action_center_at_global_point(action, point)

        if self._controller_window is not None and self._controller_window.contains_global_point(
            point
        ):
            return self._controller_window.action_center_at_global_point(action, point)

        button = self._buttons.get(action)
        if button is None or not button.isVisible() or not button.isEnabled():
            return None

        top_left = button.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left, button.size())
        if not rect.contains(point):
            return None

        return rect.center()

    def contains_global_point(self, point: QPoint) -> bool:
        if self._quick_zoom.isVisible():
            return True

        if self._quick_menu.isVisible():
            return True

        if self._speech_window is not None and self._speech_window.contains_global_point(point):
            return True

        if self._keyboard_window is not None and self._keyboard_window.contains_global_point(point):
            return True

        if self._controller_window is not None and self._controller_window.contains_global_point(
            point
        ):
            return True

        if self._settings_window is not None and self._settings_window.isVisible():
            return True

        if self._restore_button is not None and self._restore_button.isVisible():
            top_left = self._restore_button.mapToGlobal(QPoint(0, 0))
            if QRect(top_left, self._restore_button.size()).contains(point):
                return True

        if not self.isVisible():
            return False

        top_left = self.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self.size()).contains(point)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #111318;
                color: #f6f7fb;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 12px;
            }
            QLabel#trackerDot {
                border-radius: 9px;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
            }
            QLabel#eyeDot {
                border-radius: 7px;
                min-width: 14px;
                max-width: 14px;
                min-height: 14px;
                max-height: 14px;
            }
            QToolButton {
                background: #1c2029;
                border: 1px solid #303747;
                border-radius: 8px;
                color: #eef2f8;
                padding: 5px;
            }
            QToolButton:hover {
                background: #262c38;
                border-color: #4c5970;
            }
            QToolButton:checked {
                background: #245f9f;
                border-color: #67b7dc;
                color: #ffffff;
            }
            QToolButton#hideButton {
                background: #2b1f27;
                border-color: #684354;
                font-weight: 650;
            }
            QToolButton#hideButton:hover {
                background: #3a2933;
                border-color: #9d647c;
            }
            QToolButton[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 3px solid #ffe58a;
                color: #111318;
            }
            QToolButton[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 3px solid #bbf7d0;
                color: #ffffff;
            }
            """
        )

        layout = QGridLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setHorizontalSpacing(8)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 0)

        self._hide_button = QToolButton(self)
        self._hide_button.setObjectName("hideButton")
        self._hide_button.setText("Sakrij")
        self._hide_button.setIcon(self._icon("fa5s.chevron-up", HIDE_HOTBAR))
        self._hide_button.setIconSize(QSize(18, 18))
        self._hide_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._hide_button.setFixedSize(58, 58)
        self._hide_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_button.setProperty("gazeTarget", False)
        self._hide_button.setProperty("gazePulse", "")
        self._hide_button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
        self._hide_button.clicked.connect(
            lambda checked=False: self._run_toolbar_action(
                HIDE_HOTBAR,
                checked=checked,
                source="mouse",
            )
        )
        self._buttons[HIDE_HOTBAR] = self._hide_button

        action, label, icon_name = SETTINGS_BUTTON
        self._settings_button = QToolButton(self)
        self._settings_button.setText(label)
        self._settings_button.setIcon(self._icon(icon_name, action))
        self._settings_button.setIconSize(QSize(22, 22))
        self._settings_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._settings_button.setFixedSize(104, 58)
        self._settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_button.setProperty("gazeTarget", False)
        self._settings_button.setProperty("gazePulse", "")
        self._settings_button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
        self._settings_button.clicked.connect(
            lambda checked=False: self._run_toolbar_action(
                SETTINGS,
                checked=checked,
                source="mouse",
            )
        )
        self._buttons[SETTINGS] = self._settings_button

        action, label, icon_name = QUICK_ACTION_BUTTON
        self._quick_actions_button = QToolButton(self)
        self._quick_actions_button.setText(label)
        self._quick_actions_button.setIcon(self._icon(icon_name, action))
        self._quick_actions_button.setIconSize(QSize(22, 22))
        self._quick_actions_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._quick_actions_button.setFixedSize(112, 58)
        self._quick_actions_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quick_actions_button.setCheckable(True)
        self._quick_actions_button.setProperty("gazeTarget", False)
        self._quick_actions_button.setProperty("gazePulse", "")
        self._quick_actions_button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
        self._quick_actions_button.clicked.connect(
            lambda checked=False: self._run_toolbar_action(
                QUICK_ACTIONS,
                checked=checked,
                source="mouse",
            )
        )
        self._buttons[QUICK_ACTIONS] = self._quick_actions_button

        left_controls = QWidget(self)
        left_controls.setStyleSheet("background: transparent;")
        left_layout = QHBoxLayout(left_controls)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._hide_button)
        left_layout.addSpacing(16)
        left_layout.addWidget(self._settings_button)
        left_layout.addSpacing(18)
        left_layout.addWidget(self._quick_actions_button)
        left_layout.addSpacing(8)

        for action, label, icon_name in CLICK_BUTTONS:
            button = QToolButton(left_controls)
            button.setText(label)
            button.setIcon(self._icon(icon_name, action))
            button.setIconSize(QSize(22, 22))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setFixedSize(104, 58)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(action in CLICK_ACTIONS)
            button.setProperty("gazeTarget", False)
            button.setProperty("gazePulse", "")
            button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
            button.clicked.connect(
                lambda checked=False, item=action: self._run_toolbar_action(
                    item,
                    checked=checked,
                    source="mouse",
                )
            )
            self._buttons[action] = button
            left_layout.addWidget(button)

        left_layout.addSpacing(26)

        for action, label, icon_name in SECONDARY_BUTTONS:
            button = QToolButton(left_controls)
            button.setText(label)
            button.setIcon(self._icon(icon_name, action))
            button.setIconSize(QSize(22, 22))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setFixedSize(104, 58)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(action in {KEYBOARD, CONTROLLER})
            button.setProperty("gazeTarget", False)
            button.setProperty("gazePulse", "")
            button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
            button.clicked.connect(
                lambda checked=False, item=action: self._run_toolbar_action(
                    item,
                    checked=checked,
                    source="mouse",
                )
            )
            self._buttons[action] = button
            left_layout.addWidget(button)

        self._tracker_dot = QLabel(self)
        self._tracker_dot.setObjectName("trackerDot")
        self._tracker_dot.setFixedSize(18, 18)
        self._tracker_dot.setAlignment(Qt.AlignCenter)
        self._set_tracker_dot("yellow", "Praćenje: čekanje")

        self._left_eye_dot = QLabel(self)
        self._left_eye_dot.setObjectName("eyeDot")
        self._left_eye_dot.setFixedSize(14, 14)
        self._left_eye_dot.setAlignment(Qt.AlignCenter)

        self._right_eye_dot = QLabel(self)
        self._right_eye_dot.setObjectName("eyeDot")
        self._right_eye_dot.setFixedSize(14, 14)
        self._right_eye_dot.setAlignment(Qt.AlignCenter)
        self._set_eye_indicators(False, False)

        right_controls = QWidget(self)
        right_controls.setStyleSheet("background: transparent;")
        right_layout = QHBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(7)
        right_layout.addWidget(self._left_eye_dot)
        right_layout.addWidget(self._right_eye_dot)
        right_layout.addSpacing(8)
        right_layout.addWidget(self._tracker_dot)

        layout.addWidget(left_controls, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(right_controls, 0, 1, Qt.AlignRight | Qt.AlignVCenter)

    def _build_restore_button(self) -> None:
        button = QToolButton()
        button.setObjectName("restoreHotbarButton")
        button.setWindowTitle("Prikaži Pogled Assist")
        button.setWindowFlags(_no_focus_tool_window_flags())
        button.setAttribute(Qt.WA_ShowWithoutActivating, True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setText("Prikaži")
        button.setIcon(self._icon("fa5s.chevron-down", SHOW_HOTBAR))
        button.setIconSize(QSize(26, 26))
        button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        button.setFixedSize(86, 76)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("gazeTarget", False)
        button.setProperty("gazePulse", "")
        button.pressed.connect(self._mouse.cancel_gaze_interactions_for_mouse)
        button.setStyleSheet(
            """
            QToolButton#restoreHotbarButton {
                background: #111318;
                border: 2px solid #67b7dc;
                border-radius: 8px;
                color: #eef2f8;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 13px;
                font-weight: 700;
                padding: 7px;
            }
            QToolButton#restoreHotbarButton:hover {
                background: #1c2029;
                border-color: #a6e7ff;
            }
            QToolButton#restoreHotbarButton[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #111318;
            }
            QToolButton#restoreHotbarButton[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 4px solid #bbf7d0;
                color: #ffffff;
            }
            """
        )
        button.clicked.connect(
            lambda checked=False: self._run_toolbar_action(
                SHOW_HOTBAR,
                checked=checked,
                source="mouse",
            )
        )
        self._restore_button = button
        self._buttons[SHOW_HOTBAR] = button
        button.hide()

    def _connect_signals(self) -> None:
        self._gaze.gaze_updated.connect(self._mouse.handle_gaze)
        self._gaze.eye_status_changed.connect(self._mouse.handle_eye_status)
        self._gaze.eye_status_changed.connect(self._handle_eye_status_changed)
        self._gaze.status_changed.connect(self._set_status)
        self._gaze.status_changed.connect(self._update_tracker_dot_from_status)
        self._gaze.tracker_changed.connect(self._update_tracker_dot_from_tracker)
        self._mouse.gaze_position_changed.connect(self._gaze_bubble.handle_gaze)
        self._mouse.gaze_position_changed.connect(self._quick_menu.handle_gaze)
        self._mouse.gaze_position_changed.connect(self._quick_zoom.handle_gaze)
        self._mouse.status_changed.connect(self._set_status)
        self._mouse.interaction_progress_changed.connect(self._interaction_overlay.show_progress)
        self._mouse.interaction_finished.connect(self._interaction_overlay.show_fired)
        self._mouse.interaction_cancelled.connect(self._interaction_overlay.clear)
        self._mouse.toolbar_action_requested.connect(
            lambda action: self._run_toolbar_action(action, source="gaze")
        )
        self._mouse.toolbar_gaze_target_changed.connect(self._set_toolbar_gaze_target)
        self._mouse.mode_changed.connect(self._sync_mode_buttons)
        self._mouse.quick_actions_mode_changed.connect(self._sync_quick_actions_button)
        self._mouse.quick_action_zoom_requested.connect(self._open_quick_action_zoom)
        self._mouse.quick_action_menu_requested.connect(self._open_quick_action_menu)
        self._mouse.click_zoom_requested.connect(self._open_click_action_zoom)
        self._mouse.action_fired.connect(self._show_action_fired)
        self._quick_zoom.selection_progress_changed.connect(self._interaction_overlay.show_progress)
        self._quick_zoom.selection_cancelled.connect(self._interaction_overlay.clear)
        self._quick_zoom.target_selected.connect(self._quick_zoom_target_selected)
        self._quick_zoom.cancelled.connect(self._quick_zoom_cancelled)
        self._quick_menu.selection_progress_changed.connect(self._interaction_overlay.show_progress)
        self._quick_menu.selection_cancelled.connect(self._interaction_overlay.clear)
        self._quick_menu.action_selected.connect(self._quick_action_selected)

    def _install_shortcuts(self) -> None:
        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self.close)

    def _start_services(self) -> None:
        logger.info("Starting hotbar services.")
        self._position_on_primary_screen()
        self._register_appbar()
        self._mouse.start()
        self._start_foreground_tracking()
        self._gaze.start()

    def _position_on_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry()
        logger.info(
            "Positioning hotbar on primary screen: left=%s top=%s width=%s height=%s.",
            geometry.left(),
            geometry.top(),
            geometry.width(),
            geometry.height(),
        )
        self.setGeometry(geometry.left(), geometry.top(), geometry.width(), self.BAR_HEIGHT)
        if self._appbar.supported:
            self._appbar.set_position(self.height())

    def _register_appbar(self) -> None:
        if self._appbar.register(int(self.winId()), self.height()):
            self._set_status("Gornji dio radne površine je zauzet.")
        elif sys.platform == "win32":
            self._set_status("Radna površina nije rezervisana; alatna traka ostaje iznad prozora.")

    def _run_toolbar_action(
        self,
        action: str,
        *,
        checked: bool | None = None,
        source: str = "unknown",
    ) -> None:
        logger.info("Toolbar action requested by %s: %s", source, action)
        if source == "mouse":
            self._mouse.cancel_gaze_interactions_for_mouse()
        if action.startswith(KEYBOARD_WINDOW_ACTION_PREFIX):
            if self._keyboard_window is not None:
                self._keyboard_window.handle_gaze_action(action)
            return

        if action.startswith(CONTROLLER_WINDOW_ACTION_PREFIX):
            if self._controller_window is not None:
                self._controller_window.handle_gaze_action(action)
            return

        if action.startswith(SPEECH_WINDOW_ACTION_PREFIX):
            if self._speech_window is not None:
                self._speech_window.handle_gaze_action(action)
            return

        if action in CLICK_ACTIONS:
            if source == "mouse" and checked is False and self._mouse.active_mode == action:
                self._mouse.set_mode(None)
                return

            self._mouse.set_mode(action)
            return

        for button_action, button in self._buttons.items():
            if button_action in CLICK_ACTIONS:
                button.setChecked(False)

        if action == KEYBOARD:
            self._mouse.set_mode(None)
            self._toggle_keyboard(checked, source)
        elif action == CONTROLLER:
            self._mouse.set_mode(None)
            self._toggle_controller(checked, source)
        elif action == SPEECH:
            self._mouse.set_mode(None)
            self._open_speech()
        elif action == SETTINGS:
            self._mouse.set_mode(None)
            self._open_settings()
        elif action == QUICK_ACTIONS:
            self._toggle_quick_actions(checked, source)
        elif action == HIDE_HOTBAR:
            self._mouse.set_mode(None)
            self._hide_hotbar()
        elif action == SHOW_HOTBAR:
            self._mouse.set_mode(None)
            self._show_hotbar()

    def _sync_mode_buttons(self, mode: object) -> None:
        for action, button in self._buttons.items():
            if action in CLICK_ACTIONS:
                button.setChecked(action == mode)

    def _sync_quick_actions_button(self, enabled: bool) -> None:
        button = self._buttons.get(QUICK_ACTIONS)
        if button is not None:
            button.setChecked(enabled)
        self._set_click_action_buttons_visible(not enabled)

    def _set_click_action_buttons_visible(self, visible: bool) -> None:
        for action in CLICK_ACTIONS:
            button = self._buttons.get(action)
            if button is not None:
                button.setVisible(visible)

    def _set_toolbar_gaze_target(self, action: object) -> None:
        for button_action, button in self._buttons.items():
            set_gaze_feedback(button, button_action == action)
        if self._speech_window is not None:
            speech_action = (
                action
                if isinstance(action, str) and action.startswith(SPEECH_WINDOW_ACTION_PREFIX)
                else None
            )
            self._speech_window.set_gaze_target_action(speech_action)
        if self._keyboard_window is not None:
            keyboard_action = (
                action
                if isinstance(action, str) and action.startswith(KEYBOARD_WINDOW_ACTION_PREFIX)
                else None
            )
            self._keyboard_window.set_gaze_target_action(keyboard_action)
        if self._controller_window is not None:
            controller_action = (
                action
                if isinstance(action, str) and action.startswith(CONTROLLER_WINDOW_ACTION_PREFIX)
                else None
            )
            self._controller_window.set_gaze_target_action(controller_action)

    def _show_action_fired(self, action: str, point: QPoint) -> None:
        names = {
            LEFT_CLICK: "Lijevi klik",
            RIGHT_CLICK: "Desni klik",
            DOUBLE_LEFT_CLICK: "Dvostruki klik",
        }
        self._set_status(f"{names.get(action, action)} na {point.x()}, {point.y()}.")

    def _toggle_quick_actions(self, checked: bool | None, source: str) -> None:
        if source == "mouse" and checked is not None:
            enabled = checked
        else:
            enabled = not self._mouse.quick_actions_enabled

        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._mouse.set_quick_actions_enabled(enabled)

    def _open_quick_action_zoom(self, center: QPoint) -> None:
        self._zoom_context = "quick"
        self._interaction_overlay.clear()
        self._quick_menu.close_menu()
        self._quick_zoom.set_selection_settings(
            pause_ms=self._mouse.settings.selection_pause_ms,
            dwell_ms=self._mouse.settings.dwell_ms,
            radius_px=self._mouse.settings.dwell_radius_px,
        )
        self._quick_zoom.show_at(center)
        self._set_status("Otvoreno je precizno uvećanje za brzu radnju.")

    def _open_click_action_zoom(self, center: QPoint) -> None:
        self._zoom_context = "click"
        self._interaction_overlay.clear()
        self._quick_menu.close_menu()
        self._quick_zoom.set_selection_settings(
            pause_ms=self._mouse.settings.selection_pause_ms,
            dwell_ms=self._mouse.settings.dwell_ms,
            radius_px=self._mouse.settings.dwell_radius_px,
        )
        self._quick_zoom.show_at(center)
        self._set_status("Otvoreno je precizno uvećanje za klik.")

    def _quick_zoom_target_selected(self, point: QPoint) -> None:
        context = self._zoom_context
        self._zoom_context = None
        self._quick_zoom.close_zoom()
        self._interaction_overlay.clear()
        if context == "click":
            self._mouse.execute_zoomed_click(point)
            self._set_status("Odabran je uvećani cilj klika.")
            return

        if context == "quick":
            self._mouse.set_quick_target_from_logical(point)
            self._open_quick_action_menu(point)
            self._set_status("Odabran je cilj brze radnje.")
            return

        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._set_status("Cilj uvećanja je zanemaren.")

    def _quick_zoom_cancelled(self) -> None:
        context = self._zoom_context
        self._zoom_context = None
        self._quick_zoom.close_zoom()
        self._interaction_overlay.clear()
        if context == "click":
            self._mouse.cancel_zoomed_click()
            self._set_status("Uvećani klik je otkazan.")
            return

        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._set_status("Brza radnja je otkazana.")

    def _open_quick_action_menu(self, center: QPoint) -> None:
        self._interaction_overlay.clear()
        self._quick_zoom.close_zoom()
        self._quick_menu.set_selection_settings(
            pause_ms=self._mouse.settings.selection_pause_ms,
            dwell_ms=self._mouse.settings.dwell_ms,
            radius_px=self._mouse.settings.dwell_radius_px,
        )
        self._quick_menu.show_at(center)
        self._set_status("Otvoren je izbornik brzih radnji.")

    def _quick_action_selected(self, action: str) -> None:
        self._quick_menu.close_menu()
        self._interaction_overlay.clear()
        if action == CANCEL_QUICK_ACTION:
            self._mouse.cancel_quick_action_menu()
            self._set_status("Brza radnja je otkazana.")
            return

        self._mouse.execute_quick_action(action)

    def _hide_hotbar(self) -> None:
        logger.info("Hiding hotbar.")
        self._set_toolbar_gaze_target(None)
        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._interaction_overlay.clear()
        self._appbar.unregister()
        self.hide()
        if self._keyboard_window is not None and self._keyboard_window.isVisible():
            self._keyboard_window.set_reserved_top_height(0)
            self._keyboard_window.set_full_height(True)
        if self._controller_window is not None and self._controller_window.isVisible():
            self._controller_window.set_reserved_top_height(0)
            self._controller_window.set_full_height(True)
        self._show_restore_button()
        self._set_status("Alatna traka je sakrivena.")

    def _show_hotbar(self) -> None:
        logger.info("Showing hotbar.")
        self._set_toolbar_gaze_target(None)
        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        if self._restore_button is not None:
            self._restore_button.hide()
        self.show()
        self._position_on_primary_screen()
        self._register_appbar()
        if self._keyboard_window is not None and self._keyboard_window.isVisible():
            self._keyboard_window.set_reserved_top_height(self.BAR_HEIGHT)
            self._keyboard_window.set_full_height(False)
        if self._controller_window is not None and self._controller_window.isVisible():
            self._controller_window.set_reserved_top_height(self.BAR_HEIGHT)
            self._controller_window.set_full_height(False)
        self.raise_()
        self._set_status("Alatna traka je prikazana.")

    def _show_restore_button(self) -> None:
        if self._restore_button is None:
            return

        self._position_restore_button()
        self._restore_button.show()
        self._restore_button.raise_()

    def _start_foreground_tracking(self) -> None:
        if self._foreground_input is None:
            try:
                self._foreground_input = WindowsInputController()
            except Exception:
                logger.exception("Foreground window tracking failed to start.")
                self._set_status("Praćenje aktivnog prozora nije dostupno.")
                return

        self._update_last_external_foreground_window()
        if not self._foreground_timer.isActive():
            self._foreground_timer.start()
        logger.info("Foreground window tracking started.")

    def _update_last_external_foreground_window(self) -> None:
        if self._foreground_input is None:
            return

        try:
            hwnd = self._foreground_input.foreground_window()
            if hwnd is not None and not self._foreground_input.belongs_to_current_process(hwnd):
                if hwnd != self._last_external_foreground_window:
                    logger.info("Last external foreground window updated: hwnd=%s.", hwnd)
                self._last_external_foreground_window = hwnd
                if self._keyboard_window is not None:
                    self._keyboard_window.set_target_window(hwnd)
                if self._controller_window is not None:
                    self._controller_window.set_target_window(hwnd)

            self._update_last_external_cursor_position()
        except Exception:
            logger.exception("Foreground window tracking update failed.")
            self._foreground_timer.stop()
            self._set_status("Praćenje aktivnog prozora je zaustavljeno zbog greške.")

    def _update_last_external_cursor_position(self) -> None:
        if self._foreground_input is None:
            return

        logical_cursor = QCursor.pos()
        if self.contains_global_point(logical_cursor):
            return

        physical_cursor = self._foreground_input.cursor_position()
        if physical_cursor == self._last_external_cursor_position:
            return

        self._last_external_cursor_position = physical_cursor
        if self._controller_window is not None:
            self._controller_window.set_target_cursor_position(physical_cursor)

    def _prime_foreground_tracking(self) -> None:
        try:
            self._foreground_input = WindowsInputController()
            self._update_last_external_foreground_window()
            logger.info("Foreground window tracking primed.")
        except Exception:
            logger.exception("Foreground window tracking could not be primed.")

    def _position_restore_button(self) -> None:
        if self._restore_button is None:
            return

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry()
        margin = 18
        x = geometry.left() + margin
        y = geometry.top() + margin
        self._restore_button.move(x, y)

    def _toggle_keyboard(self, checked: bool | None, source: str) -> None:
        if source == "mouse" and checked is not None:
            enabled = checked
        else:
            enabled = not (self._keyboard_window is not None and self._keyboard_window.isVisible())

        if enabled:
            self._show_keyboard_sidebar()
        else:
            self._hide_keyboard_sidebar()

    def _show_keyboard_sidebar(self) -> None:
        self._hide_controller_sidebar()
        if self._keyboard_window is None:
            self._keyboard_window = KeyboardWindow(self._speech.settings, self)
            self._keyboard_window.closed.connect(self._keyboard_window_closed)
            self._keyboard_window.status_changed.connect(self._set_status)
            self._keyboard_window.interaction_context_changed.connect(
                lambda: self._mouse.cancel_toolbar_interaction(require_leave=True)
            )
            self._keyboard_window.mouse_action_started.connect(
                self._mouse.cancel_gaze_interactions_for_mouse
            )

        self._update_last_external_foreground_window()
        self._keyboard_window.set_target_window(self._last_external_foreground_window)
        self._keyboard_window.set_reserved_top_height(self.BAR_HEIGHT if self.isVisible() else 0)
        self._keyboard_window.update_settings(self._speech.settings)
        self._keyboard_window.show_sidebar(full_height=not self.isVisible())
        self._set_keyboard_button_checked(True)
        self._set_status("Tastatura je otvorena.")

    def _hide_keyboard_sidebar(self) -> None:
        if self._keyboard_window is not None and self._keyboard_window.isVisible():
            self._keyboard_window.hide_sidebar()
            self._set_status("Tastatura je zatvorena.")
        self._set_keyboard_button_checked(False)

    def _keyboard_window_closed(self) -> None:
        self._set_keyboard_button_checked(False)
        self._set_status("Tastatura je zatvorena.")

    def _set_keyboard_button_checked(self, checked: bool) -> None:
        button = self._buttons.get(KEYBOARD)
        if button is not None:
            button.setChecked(checked)

    def _toggle_controller(self, checked: bool | None, source: str) -> None:
        if source == "mouse" and checked is not None:
            enabled = checked
        else:
            enabled = not (
                self._controller_window is not None and self._controller_window.isVisible()
            )

        if enabled:
            self._show_controller_sidebar()
        else:
            self._hide_controller_sidebar()

    def _show_controller_sidebar(self) -> None:
        self._hide_keyboard_sidebar()
        if self._controller_window is None:
            self._controller_window = ControllerWindow(
                self._mouse.settings,
                self._speech.settings,
                self,
            )
            self._controller_window.closed.connect(self._controller_window_closed)
            self._controller_window.status_changed.connect(self._set_status)
            self._controller_window.speech_requested.connect(self._open_speech_from_controller)
            self._controller_window.gaze_settings_changed.connect(self._update_gaze_settings)
            self._controller_window.interaction_context_changed.connect(
                lambda: self._mouse.cancel_toolbar_interaction(require_leave=True)
            )
            self._controller_window.mouse_action_started.connect(
                self._mouse.cancel_gaze_interactions_for_mouse
            )

        self._update_last_external_foreground_window()
        self._controller_window.set_target_window(self._last_external_foreground_window)
        self._controller_window.set_target_cursor_position(self._last_external_cursor_position)
        self._controller_window.set_reserved_top_height(self.BAR_HEIGHT if self.isVisible() else 0)
        self._controller_window.update_gaze_settings(self._mouse.settings)
        self._controller_window.update_speech_settings(self._speech.settings)
        self._controller_window.show_sidebar(full_height=not self.isVisible())
        self._set_controller_button_checked(True)
        self._set_status("Upravljač je otvoren.")

    def _hide_controller_sidebar(self) -> None:
        if self._controller_window is not None and self._controller_window.isVisible():
            self._controller_window.hide_sidebar()
            self._set_status("Upravljač je zatvoren.")
        self._set_controller_button_checked(False)

    def _controller_window_closed(self) -> None:
        self._set_controller_button_checked(False)
        self._set_status("Upravljač je zatvoren.")

    def _set_controller_button_checked(self, checked: bool) -> None:
        button = self._buttons.get(CONTROLLER)
        if button is not None:
            button.setChecked(checked)

    def _open_speech_from_controller(self) -> None:
        self._hide_controller_sidebar()
        self._open_speech()

    def _open_speech(self) -> None:
        logger.info("Opening speech window.")
        if self._speech_window is None:
            self._speech_window = SpeechWindow(
                self._speech,
                self,
                library_store=self._speech_library_store,
                suggestions=self._suggestions,
            )
            self._speech_window.closed.connect(
                lambda: self._set_status("Prozor za govor je zatvoren.")
            )
            self._speech_window.interaction_context_changed.connect(
                lambda: self._mouse.cancel_toolbar_interaction(require_leave=True)
            )
            self._speech_window.mouse_action_started.connect(
                self._mouse.cancel_gaze_interactions_for_mouse
            )
            self._speech_window.quit_requested.connect(self._quit_application)
        self._speech_window.update_settings(self._speech.settings)

        self._speech_window.show_full_screen()
        self._set_status("Prozor za govor je otvoren.")

    def _open_settings(self) -> None:
        logger.info("Opening fullscreen settings window.")
        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._hide_keyboard_sidebar()
        self._hide_controller_sidebar()
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return

        window = SettingsWindow(
            self._mouse.settings,
            self._speech.settings,
            self,
            update_manager=self._release_update_manager,
            suggestions=self._suggestions,
        )
        window.gaze_settings_changed.connect(self._update_gaze_settings)
        window.speech_settings_changed.connect(self._update_speech_settings)
        window.calibration_requested.connect(self._launch_tobii_calibration)
        window.speech_test_requested.connect(self._test_current_speech_settings)
        window.update_requested.connect(self._start_release_update)
        window.quit_requested.connect(self._quit_application)
        window.closed.connect(self._settings_window_closed)
        self._mouse.gaze_position_changed.connect(window.handle_gaze)
        window.interaction_progress_changed.connect(self._interaction_overlay.show_progress)
        window.interaction_finished.connect(self._interaction_overlay.show_fired)
        window.interaction_cancelled.connect(self._interaction_overlay.clear)

        self._settings_window = window
        window.show_fullscreen_on_primary()
        self._set_status("Postavke su otvorene.")

    def _settings_window_closed(self) -> None:
        window = self._settings_window
        if window is None:
            return

        try:
            self._mouse.gaze_position_changed.disconnect(window.handle_gaze)
        except (RuntimeError, TypeError):
            logger.info("Settings gaze handler was already disconnected.")

        try:
            window.interaction_progress_changed.disconnect(self._interaction_overlay.show_progress)
            window.interaction_finished.disconnect(self._interaction_overlay.show_fired)
            window.interaction_cancelled.disconnect(self._interaction_overlay.clear)
        except (RuntimeError, TypeError):
            logger.info("Settings interaction overlay handlers were already disconnected.")

        self._settings_window = None
        window.deleteLater()
        self._set_status("Postavke su zatvorene.")

    def _quit_application(self) -> None:
        logger.info("Quit application requested.")
        self._set_toolbar_gaze_target(None)
        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._mouse.cancel_quick_action_menu()
        self._hide_keyboard_sidebar()
        self._hide_controller_sidebar()
        self._interaction_overlay.clear()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _start_release_update(self) -> None:
        try:
            self._release_update_manager.launch()
        except ReleaseUpdateError as error:
            logger.exception("Could not launch the release updater.")
            self._set_status("Pokretanje ažuriranja nije uspjelo.")
            if self._settings_window is not None:
                self._settings_window.show_update_error(str(error))
            return

        logger.info("Release updater started; closing the current application.")
        self._quit_application()

    def _update_gaze_settings(self, settings: object) -> None:
        self._mouse.update_settings(settings)
        self._gaze_bubble.set_enabled(bool(getattr(settings, "show_gaze_bubble", True)))
        self._interaction_overlay.set_enabled(
            bool(getattr(settings, "show_interaction_overlay", True))
        )
        if self._controller_window is not None:
            self._controller_window.update_gaze_settings(self._mouse.settings)
        save_app_settings(self._mouse.settings, self._speech.settings)

    def _update_speech_settings(self, settings: object) -> None:
        self._speech.update_settings(settings)
        if self._speech_window is not None:
            self._speech_window.update_settings(self._speech.settings)
        if self._keyboard_window is not None:
            self._keyboard_window.update_settings(self._speech.settings)
        if self._controller_window is not None:
            self._controller_window.update_speech_settings(self._speech.settings)
        save_app_settings(self._mouse.settings, self._speech.settings)

    def _test_current_speech_settings(self) -> None:
        try:
            if self._speech.speak(SPEECH_TEST_TEXT):
                self._set_status("Test govora je pokrenut.")
                if self._settings_window is not None:
                    self._settings_window.set_status("Test govora je pokrenut.")
            else:
                self._set_status(
                    "Govor nije uspio: odabrani glas nije pronađen ili se nije mogao pokrenuti."
                )
                if self._settings_window is not None:
                    self._settings_window.set_status("Govor nije uspio.")
        except Exception:
            logger.exception("Speech settings test failed.")
            self._set_status("Govor nije uspio.")
            if self._settings_window is not None:
                self._settings_window.set_status("Govor nije uspio.")

    def _launch_tobii_calibration(self) -> None:
        if self._settings_window is not None:
            self._settings_window.cancel_gaze_interaction()
            self._settings_window.hide()
            self._interaction_overlay.clear()

        try:
            message = launch_tobii_guest_calibration()
            self._set_status(message)
            if self._settings_window is not None:
                self._settings_window.set_status(message)
        except Exception:
            logger.exception("Tobii calibration launch failed.")
            self._set_status("Kalibracija nije uspjela.")
            if self._settings_window is not None:
                self._settings_window.set_status("Kalibracija nije uspjela.")

    def _set_status(self, text: str) -> None:
        logger.info("Status: %s", text)
        self.setToolTip(text)
        if self._restore_button is not None:
            self._restore_button.setToolTip(text)

    def _handle_eye_status_changed(self, left_open: bool, right_open: bool) -> None:
        self._set_eye_indicators(left_open, right_open)
        if left_open and right_open:
            return

        self._quick_zoom.close_zoom()
        self._quick_menu.close_menu()
        self._zoom_context = None
        self._mouse.cancel_zoomed_click(reset_mode=False)
        self._interaction_overlay.clear()
        if self._speech_window is not None:
            self._speech_window.cancel_gaze_interaction()
        if self._keyboard_window is not None:
            self._keyboard_window.cancel_gaze_interaction()
        if self._controller_window is not None:
            self._controller_window.cancel_gaze_interaction()
        if self._settings_window is not None:
            self._settings_window.pause_gaze_interaction()

    def _set_eye_indicators(self, left_open: bool, right_open: bool) -> None:
        self._set_eye_dot(self._left_eye_dot, bool(left_open), "Lijevo")
        self._set_eye_dot(self._right_eye_dot, bool(right_open), "Desno")

    def _set_eye_dot(self, dot: QLabel, open_: bool, label: str) -> None:
        fill = "#ffffff" if open_ else "transparent"
        border = "#ffffff" if open_ else "transparent"
        dot.setStyleSheet(
            f"QLabel#eyeDot {{background: {fill};border: 1px solid {border};border-radius: 7px;}}"
        )
        state = "otvoreno" if open_ else "zatvoreno"
        dot.setToolTip(f"{label} oko: {state}")

    def _update_tracker_dot_from_tracker(self, text: str) -> None:
        state = "yellow" if text.strip().lower() in {"retrying", "ponovni pokušaj"} else "green"
        self._set_tracker_dot(state, f"Praćenje: {text}")

    def _update_tracker_dot_from_status(self, text: str) -> None:
        self._set_tracker_dot(_tracker_dot_state(text), text)

    def _set_tracker_dot(self, state: str, tooltip: str) -> None:
        colors = {
            "green": ("#22c55e", "#86efac"),
            "yellow": ("#f59e0b", "#fde68a"),
            "red": ("#ef4444", "#fecaca"),
        }
        fill, border = colors.get(state, colors["yellow"])
        self._tracker_dot.setStyleSheet(
            "QLabel#trackerDot {"
            f"background: {fill};"
            f"border: 2px solid {border};"
            "border-radius: 9px;"
            "}"
        )
        self._tracker_dot.setToolTip(tooltip)

    def _icon(self, icon_name: str, action: str) -> QIcon:
        try:
            import qtawesome as qta

            color = "#ffffff" if action in CLICK_ACTIONS else "#dce6f3"
            return qta.icon(icon_name, color=color)
        except Exception:
            logger.exception("Could not load qtawesome icon %s; using fallback.", icon_name)
            fallback = {
                LEFT_CLICK: QStyle.StandardPixmap.SP_ArrowForward,
                RIGHT_CLICK: QStyle.StandardPixmap.SP_DialogApplyButton,
                DOUBLE_LEFT_CLICK: QStyle.StandardPixmap.SP_BrowserReload,
                QUICK_ACTIONS: QStyle.StandardPixmap.SP_ComputerIcon,
                SPEECH: QStyle.StandardPixmap.SP_MediaVolume,
                KEYBOARD: QStyle.StandardPixmap.SP_FileDialogDetailedView,
                CONTROLLER: QStyle.StandardPixmap.SP_DesktopIcon,
                SETTINGS: QStyle.StandardPixmap.SP_FileDialogInfoView,
                HIDE_HOTBAR: QStyle.StandardPixmap.SP_TitleBarMinButton,
                SHOW_HOTBAR: QStyle.StandardPixmap.SP_TitleBarNormalButton,
            }.get(action, QStyle.StandardPixmap.SP_FileIcon)
            return self.style().standardIcon(fallback)


def _tracker_dot_state(status: str) -> str:
    normalized = status.strip().lower()
    if "tracking with" in normalized or "praćenje je aktivno" in normalized:
        return "green"

    yellow_markers = (
        "trying",
        "retrying",
        "waiting",
        "scanning",
        "starting",
        "pokušavam",
        "ponovni pokušaj",
        "čekanje",
        "pretraga",
        "pokrećem",
    )
    if any(marker in normalized for marker in yellow_markers):
        return "yellow"

    red_markers = (
        "failed",
        "failure",
        "unavailable",
        "missing",
        "not found",
        "disabled",
        "error",
        "nije uspjelo",
        "nije uspio",
        "nije dostupno",
        "nije dostupan",
        "nije dostupna",
        "nedostaje",
        "nije pronađen",
        "nije pronađena",
        "isključeno",
        "greška",
    )
    if any(marker in normalized for marker in red_markers):
        return "red"

    return "yellow"


def _no_focus_tool_window_flags() -> Qt.WindowFlags:
    flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    no_focus = getattr(Qt, "WindowDoesNotAcceptFocus", None)
    if no_focus is None:
        no_focus = getattr(Qt.WindowType, "WindowDoesNotAcceptFocus", None)
    if no_focus is not None:
        flags |= no_focus
    return flags
