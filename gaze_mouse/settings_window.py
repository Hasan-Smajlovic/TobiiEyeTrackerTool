"""Fullscreen gaze-selectable settings UI."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .gaze_feedback import set_gaze_feedback
from .gaze_selection import (
    MAX_SELECTION_PAUSE_MS,
    MIN_SELECTION_PAUSE_MS,
    GazeSelectionTimer,
)
from .logging_setup import set_application_logging_enabled
from .mouse_controller import GazeSettings
from .release_update import ReleaseCheckResult, ReleaseUpdateManager
from .speech_service import VOICE_PRESET_DEFAULT, VOICE_PRESETS, SpeechSettings
from .suggestion_service import SuggestionService
from .windows_startup import is_windows_startup_enabled, set_windows_startup_enabled

logger = logging.getLogger(__name__)

GazeCallback = Callable[[], None]


class SettingsWindow(QWidget):
    """Fullscreen settings surface that can be operated by gaze dwell."""

    closed = Signal()
    gaze_settings_changed = Signal(object)
    speech_settings_changed = Signal(object)
    calibration_requested = Signal()
    speech_test_requested = Signal()
    update_requested = Signal()
    quit_requested = Signal()
    interaction_progress_changed = Signal(QPoint, float, str)
    interaction_finished = Signal(QPoint, str)
    interaction_cancelled = Signal()

    def __init__(
        self,
        gaze_settings: GazeSettings,
        speech_settings: SpeechSettings,
        parent: QWidget | None = None,
        *,
        update_manager: ReleaseUpdateManager | None = None,
        suggestions: SuggestionService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Postavke")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setObjectName("settingsRoot")

        self._gaze_settings = replace(gaze_settings)
        self._speech_settings = replace(speech_settings)
        self._update_manager = update_manager
        self._suggestions = suggestions or SuggestionService(self)
        self._word_page = 0
        self._chosen_word: str | None = None
        self._visible_words: list[str] = []
        self._learning_busy = False
        self._last_learning_point: QPoint | None = None
        self._blocked_learning_button: QWidget | None = None
        self._gaze_actions: dict[QWidget, GazeCallback] = {}
        self._gaze_names: dict[QWidget, str] = {}
        self._gaze_target: QWidget | None = None
        self._gaze_selection = GazeSelectionTimer()
        self._last_gaze_action_ms = 0.0
        self._interaction_active = False

        self._sync_startup_setting_from_windows()
        self._build_ui()
        self._suggestions.status_changed.connect(self._learning_status_changed)
        self._suggestions.storage_finished.connect(self._learning_saved)
        self._install_shortcuts()
        self._refresh_values()
        self._initialize_release_update()
        logger.info("Settings window initialized.")

    def show_fullscreen_on_primary(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry()
        logger.info(
            "Showing settings fullscreen on primary screen: left=%s top=%s width=%s height=%s.",
            geometry.left(),
            geometry.top(),
            geometry.width(),
            geometry.height(),
        )
        self.setGeometry(geometry)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        logger.info("Settings window closed.")
        self.cancel_gaze_interaction()
        self.closed.emit()
        super().closeEvent(event)

    def handle_gaze(self, point: QPoint) -> None:
        self._last_learning_point = QPoint(point)
        if self._blocked_learning_button is not None:
            button = self._blocked_learning_button
            if QRect(button.mapToGlobal(QPoint(0, 0)), button.size()).contains(point):
                self.cancel_gaze_interaction(require_leave=True)
                return
            self._blocked_learning_button = None
        if not self.isVisible():
            self.cancel_gaze_interaction()
            return

        target = self._gaze_action_at(point)
        now_ms = time.monotonic() * 1000
        if target is None:
            self.cancel_gaze_interaction()
            return

        widget, action = target
        update = self._gaze_selection.update(
            widget,
            now_ms,
            pause_ms=self._gaze_settings.selection_pause_ms,
            dwell_ms=self._gaze_settings.dwell_ms,
        )
        if update.progress is None:
            self._set_gaze_target(None)
            self._cancel_interaction()
            return

        if widget is not self._gaze_target:
            self._set_gaze_target(widget)
            self._set_status(f"Cilj: {self._gaze_names.get(widget, 'kontrola')}")
        self._emit_interaction_progress(widget, update.progress)

        cooled = now_ms - self._last_gaze_action_ms >= self._gaze_settings.click_cooldown_ms
        if update.ready and cooled:
            name = self._gaze_names.get(widget, "kontrola")
            logger.info("Settings gaze action fired: %s", name)
            self._last_gaze_action_ms = now_ms
            self._gaze_selection.complete()
            self._finish_interaction(widget)
            self._set_gaze_target(None)
            action()

    def cancel_gaze_interaction(self, *, require_leave: bool = False) -> None:
        self._gaze_selection.cancel(require_leave=require_leave)
        self._set_gaze_target(None)
        self._cancel_interaction()

    def pause_gaze_interaction(self) -> None:
        self._gaze_selection.pause()
        self._set_gaze_target(None)
        self._cancel_interaction()

    def set_status(self, text: str) -> None:
        self._set_status(text)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget#settingsRoot {
                background: #111312;
                color: #f4f1ea;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 15px;
            }
            QWidget#settingsRoot QLabel { color: #f4f1ea; }
            QLabel#titleLabel {
                color: #ffffff;
                font-size: 25px;
                font-weight: 650;
            }
            QLabel#statusLabel {
                color: #b8c5c0;
                font-size: 14px;
            }
            QLabel#sectionTitle {
                color: #ffffff;
                font-size: 24px;
                font-weight: 650;
            }
            QLabel#sectionDescription {
                color: #aeb9b3;
                font-size: 14px;
            }
            QLabel#navSectionLabel, QLabel#groupLabel {
                color: #8fa099;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#navNote {
                color: #8fa099;
                font-size: 13px;
            }
            QLabel#settingTitle {
                color: #ffffff;
                font-size: 17px;
                font-weight: 600;
            }
            QLabel#settingHint {
                color: #b9b2a5;
                font-size: 13px;
            }
            QLabel#updateStatusLabel {
                color: #d8d3c8;
                font-size: 15px;
            }
            QLabel#valueLabel {
                background: #0f1110;
                border: 1px solid #3a3d3b;
                border-radius: 10px;
                color: #ffffff;
                font-size: 24px;
                font-weight: 650;
                padding: 12px 18px;
            }
            QFrame#navPanel, QFrame#contentPanel {
                background: #191c1a;
                border: 1px solid #343a36;
                border-radius: 14px;
            }
            QFrame#settingRow {
                background: #222522;
                border: 1px solid #3a403c;
                border-radius: 10px;
            }
            QToolButton {
                background: #272a28;
                border: 1px solid #414742;
                border-radius: 10px;
                color: #f5f3ef;
                font-size: 15px;
                font-weight: 600;
                padding: 10px 14px;
            }
            QToolButton:hover {
                background: #323633;
                border-color: #69736d;
            }
            QToolButton:disabled {
                background: #242624;
                border-color: #363a37;
                color: #747b76;
            }
            QToolButton:checked {
                background: #1d6f68;
                border-color: #74d3c6;
                color: #ffffff;
            }
            QToolButton#navButton {
                background: transparent;
                border-color: transparent;
                text-align: left;
            }
            QToolButton#navButton:hover {
                background: #252a27;
                border-color: #3c4540;
            }
            QToolButton#navButton:checked, QToolButton#primaryButton {
                background: #1d6f68;
                border-color: #74d3c6;
                color: #ffffff;
            }
            QToolButton#adjustButton {
                background: #242825;
            }
            QToolButton[gazeTarget="true"] {
                background: #3a3420;
                border: 3px solid #f0c84a;
                color: #ffffff;
            }
            QToolButton[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #14140f;
            }
            QToolButton[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 4px solid #bbf7d0;
                color: #ffffff;
            }
            QToolButton#dangerButton {
                background: #492326;
                border-color: #804047;
            }
            QToolButton#dangerButton[gazeTarget="true"] {
                background: #5d3422;
                border: 3px solid #f0c84a;
            }
            QToolButton#dangerButton[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #14140f;
            }
            QToolButton#dangerButton[gazeTarget="true"][gazePulse="1"] {
                background: #dc2626;
                border: 4px solid #fecaca;
                color: #ffffff;
            }
            QCheckBox {
                background: #222522;
                border: 1px solid #3a403c;
                border-radius: 10px;
                color: #f5f3ef;
                font-size: 15px;
                font-weight: 600;
                padding: 14px 18px;
                spacing: 14px;
            }
            QCheckBox:hover {
                background: #303330;
                border-color: #69736d;
            }
            QCheckBox::indicator {
                width: 28px;
                height: 28px;
                border: 2px solid #8b938d;
                border-radius: 6px;
                background: #101010;
            }
            QCheckBox::indicator:checked {
                background: #1d6f68;
                border-color: #74d3c6;
                image: url("__CHECKBOX_X_IMAGE__");
            }
            QCheckBox[gazeTarget="true"] {
                background: #3a3420;
                border: 3px solid #f0c84a;
                color: #ffffff;
            }
            QCheckBox[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #14140f;
            }
            QCheckBox[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 4px solid #bbf7d0;
                color: #ffffff;
            }
            QComboBox {
                background: #0f1110;
                border: 1px solid #4c534e;
                border-radius: 10px;
                color: #ffffff;
                font-size: 20px;
                font-weight: 650;
                min-height: 46px;
                padding: 10px 14px;
            }
            QComboBox:hover {
                background: #181a18;
                border-color: #69736d;
            }
            QComboBox::drop-down {
                border: 0;
                width: 42px;
            }
            QComboBox QAbstractItemView {
                background: #1e1f1e;
                border: 1px solid #4c534e;
                color: #ffffff;
                selection-background-color: #1d6f68;
                selection-color: #ffffff;
            }
            QComboBox[gazeTarget="true"] {
                background: #3a3420;
                border: 3px solid #f0c84a;
                color: #ffffff;
            }
            QComboBox[gazeTarget="true"][gazePulse="0"] {
                background: #f0c84a;
                border: 4px solid #ffe58a;
                color: #14140f;
            }
            QComboBox[gazeTarget="true"][gazePulse="1"] {
                background: #16a34a;
                border: 4px solid #bbf7d0;
                color: #ffffff;
            }
            """.replace("__CHECKBOX_X_IMAGE__", _checkbox_x_image_url())
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(18)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(16)

        title = QLabel("Postavke", self)
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        top_bar.addWidget(title, 1)

        self._status_label = QLabel("Spremno", self)
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self._status_label.setMinimumWidth(360)
        top_bar.addWidget(self._status_label, 0)
        self._exit_button = self._make_button(
            "Zatvori",
            self.close,
            icon_name="fa5s.times",
            object_name="dangerButton",
            minimum_size=QSize(128, 58),
        )
        top_bar.addWidget(self._exit_button, 0, Qt.AlignRight)
        root.addLayout(top_bar)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        nav_panel = QFrame(self)
        nav_panel.setObjectName("navPanel")
        nav_panel.setFixedWidth(264)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(14, 14, 14, 14)
        nav_layout.setSpacing(10)

        nav_label = QLabel("KATEGORIJE", nav_panel)
        nav_label.setObjectName("navSectionLabel")
        nav_label.setContentsMargins(8, 4, 8, 2)
        nav_layout.addWidget(nav_label)

        self._general_tab_button = self._make_button(
            "Opće postavke",
            lambda: self._select_tab(0),
            icon_name="fa5s.sliders-h",
            object_name="navButton",
            checkable=True,
            minimum_size=QSize(234, 72),
        )
        self._gaze_tab_button = self._make_button(
            "Postavke pogleda",
            lambda: self._select_tab(1),
            icon_name="fa5s.eye",
            object_name="navButton",
            checkable=True,
            minimum_size=QSize(234, 72),
        )
        self._speech_tab_button = self._make_button(
            "Postavke govora",
            lambda: self._select_tab(2),
            icon_name="fa5s.volume-up",
            object_name="navButton",
            checkable=True,
            minimum_size=QSize(234, 72),
        )
        nav_layout.addWidget(self._general_tab_button)
        nav_layout.addWidget(self._gaze_tab_button)
        nav_layout.addWidget(self._speech_tab_button)
        nav_layout.addStretch(1)
        nav_note = QLabel("Promjene se primjenjuju i čuvaju automatski.", nav_panel)
        nav_note.setObjectName("navNote")
        nav_note.setWordWrap(True)
        nav_note.setContentsMargins(8, 0, 8, 6)
        nav_layout.addWidget(nav_note)

        body.addWidget(nav_panel, 0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_general_page())
        self._stack.addWidget(self._build_gaze_page())
        self._stack.addWidget(self._build_speech_page())
        self._stack.addWidget(self._build_learning_page())
        body.addWidget(self._stack, 1)

        self._select_tab(0)

    def _build_general_page(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("contentPanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        header = QGridLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setHorizontalSpacing(16)
        header.setVerticalSpacing(4)
        header.setColumnStretch(0, 1)
        title = QLabel("Opće postavke", page)
        title.setObjectName("sectionTitle")
        description = QLabel("Pokretanje, zapisivanje i ažuriranje aplikacije.", page)
        description.setObjectName("sectionDescription")
        header.addWidget(title, 0, 0, Qt.AlignVCenter | Qt.AlignLeft)
        header.addWidget(description, 1, 0, Qt.AlignVCenter | Qt.AlignLeft)
        self._quit_button = self._make_button(
            "Isključi aplikaciju",
            self._request_quit,
            icon_name="fa5s.power-off",
            object_name="dangerButton",
            minimum_size=QSize(154, 58),
        )
        header.addWidget(self._quit_button, 0, 1, 2, 1, Qt.AlignVCenter | Qt.AlignRight)
        layout.addLayout(header)

        options_label = QLabel("POKRETANJE I DIJAGNOSTIKA", page)
        options_label.setObjectName("groupLabel")
        layout.addWidget(options_label)
        actions = QGridLayout()
        actions.setHorizontalSpacing(12)
        actions.setVerticalSpacing(12)
        self._startup_checkbox = self._make_checkbox(
            "Pokreni uz Windows (administrator)",
            self._toggle_start_with_windows,
            minimum_size=QSize(260, 72),
        )
        self._logging_checkbox = self._make_checkbox(
            "Uključi zapisivanje",
            self._toggle_logging_enabled,
            minimum_size=QSize(260, 72),
        )
        self._launcher_window_checkbox = self._make_checkbox(
            "Prikaži PowerShell pri pokretanju",
            self._toggle_show_launcher_window,
            minimum_size=QSize(260, 72),
        )
        actions.addWidget(self._startup_checkbox, 0, 0)
        actions.addWidget(self._logging_checkbox, 0, 1)
        actions.addWidget(self._launcher_window_checkbox, 0, 2)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)

        update_label = QLabel("VERZIJA APLIKACIJE", page)
        update_label.setObjectName("groupLabel")
        layout.addWidget(update_label)
        update_row = QFrame(page)
        update_row.setObjectName("settingRow")
        update_layout = QGridLayout(update_row)
        update_layout.setContentsMargins(16, 14, 16, 14)
        update_layout.setHorizontalSpacing(12)
        update_layout.setVerticalSpacing(6)
        update_layout.setColumnStretch(0, 1)

        update_title = QLabel("Ažuriranja", update_row)
        update_title.setObjectName("settingTitle")
        self._update_status_label = QLabel("Pripremam provjeru ažuriranja.", update_row)
        self._update_status_label.setObjectName("updateStatusLabel")
        self._update_status_label.setWordWrap(True)
        self._check_update_button = self._make_button(
            "Provjeri ažuriranja",
            self._check_for_updates,
            icon_name="fa5s.sync-alt",
            minimum_size=QSize(210, 64),
        )
        self._update_button = self._make_button(
            "Ažuriraj i ponovo pokreni",
            self._request_update,
            icon_name="fa5s.download",
            minimum_size=QSize(250, 64),
        )
        self._update_button.hide()

        update_layout.addWidget(update_title, 0, 0)
        update_layout.addWidget(self._update_status_label, 1, 0)
        update_layout.addWidget(self._check_update_button, 0, 1, 2, 1)
        update_layout.addWidget(self._update_button, 0, 2, 2, 1)
        layout.addWidget(update_row)
        layout.addStretch(1)

        return page

    def _build_gaze_page(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("contentPanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title = QLabel("Postavke pogleda", page)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel("Podesite brzinu, stabilnost i povratnu informaciju pogleda.", page)
        description.setObjectName("sectionDescription")
        layout.addWidget(description)

        self._selection_pause_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Pauza prije odabira",
                "Vrijeme čekanja prije nego što se krug napretka počne puniti.",
                self._selection_pause_value,
                lambda: self._adjust_selection_pause(-50),
                lambda: self._adjust_selection_pause(50),
            )
        )

        self._dwell_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Vrijeme zadržavanja pogleda",
                "Vrijeme punjenja kruga napretka nakon početne pauze.",
                self._dwell_value,
                lambda: self._adjust_dwell_ms(-50),
                lambda: self._adjust_dwell_ms(50),
            )
        )

        self._radius_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Radijus stabilnog pogleda",
                "Koliko mirno pogled mora ostati prije pokretanja odabrane radnje.",
                self._radius_value,
                lambda: self._adjust_dwell_radius(-2),
                lambda: self._adjust_dwell_radius(2),
            )
        )

        self._cooldown_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Pauza između radnji",
                "Vrijeme čekanja nakon jedne radnje prije pokretanja sljedeće.",
                self._cooldown_value,
                lambda: self._adjust_click_cooldown(-50),
                lambda: self._adjust_click_cooldown(50),
            )
        )

        self._smoothing_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Uglađivanje pokazivača",
                "Niže vrijednosti su mirnije, a više brže prate pogled.",
                self._smoothing_value,
                lambda: self._adjust_smoothing(-0.05),
                lambda: self._adjust_smoothing(0.05),
            )
        )

        actions_label = QLabel("PONAŠANJE I KALIBRACIJA", page)
        actions_label.setObjectName("groupLabel")
        layout.addWidget(actions_label)
        actions = QGridLayout()
        actions.setHorizontalSpacing(12)
        actions.setVerticalSpacing(12)
        self._move_pointer_button = self._make_button(
            "Pomjeraj pokazivač pogledom",
            self._toggle_move_pointer,
            icon_name="fa5s.mouse-pointer",
            checkable=True,
            minimum_size=QSize(230, 64),
        )
        self._gaze_bubble_button = self._make_button(
            "Prikaži oznaku pogleda",
            self._toggle_gaze_bubble,
            icon_name="fa5s.bullseye",
            checkable=True,
            minimum_size=QSize(220, 64),
        )
        self._interaction_overlay_button = self._make_button(
            "Prikaži napredak radnje",
            self._toggle_interaction_overlay,
            icon_name="fa5s.circle-notch",
            checkable=True,
            minimum_size=QSize(220, 64),
        )
        self._precision_zoom_checkbox = self._make_checkbox(
            "Koristi precizno uvećanje",
            self._toggle_precision_zoom,
            minimum_size=QSize(230, 64),
        )
        self._calibration_button = self._make_button(
            "Pokreni Tobii kalibraciju",
            self._request_calibration,
            icon_name="fa5s.crosshairs",
            minimum_size=QSize(230, 64),
        )
        for widget in (
            self._move_pointer_button,
            self._gaze_bubble_button,
            self._interaction_overlay_button,
            self._precision_zoom_checkbox,
            self._calibration_button,
        ):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        actions.addWidget(self._move_pointer_button, 0, 0)
        actions.addWidget(self._gaze_bubble_button, 0, 1)
        actions.addWidget(self._interaction_overlay_button, 0, 2)
        actions.addWidget(self._precision_zoom_checkbox, 1, 0)
        actions.addWidget(self._calibration_button, 1, 1, 1, 2)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        layout.addStretch(1)

        return page

    def _build_speech_page(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("contentPanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title = QLabel("Postavke govora", page)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel("Podesite glas i raspored tastature za komunikaciju.", page)
        description.setObjectName("sectionDescription")
        layout.addWidget(description)

        self._speed_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Brzina govora",
                "Broj riječi u minuti koji koristi eSpeak NG.",
                self._speed_value,
                lambda: self._adjust_speech_speed(-5),
                lambda: self._adjust_speech_speed(5),
            )
        )

        self._letters_group_value = self._make_value_label(page)
        layout.addWidget(
            self._make_adjust_row(
                "Broj slova u grupi",
                "Broj slova u svakoj grupi na ekranima za govor i tastaturu.",
                self._letters_group_value,
                lambda: self._adjust_letters_per_group(-1),
                lambda: self._adjust_letters_per_group(1),
            )
        )

        voice_row = QFrame(page)
        voice_row.setObjectName("settingRow")
        voice_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        voice_layout = QGridLayout(voice_row)
        voice_layout.setContentsMargins(16, 14, 16, 14)
        voice_layout.setHorizontalSpacing(16)
        voice_layout.setVerticalSpacing(6)
        voice_layout.setColumnStretch(0, 1)

        voice_title = QLabel("Glas", voice_row)
        voice_title.setObjectName("settingTitle")
        voice_hint = QLabel(
            "Standardni glas koristi eSpeak NG. Prirodni glas koristi Microsoft Edge "
            "bs-BA-GoranNeural.",
            voice_row,
        )
        voice_hint.setObjectName("settingHint")
        voice_hint.setWordWrap(True)

        self._voice_combo = QComboBox(voice_row)
        self._voice_combo.setMinimumSize(QSize(260, 64))
        self._voice_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for value, label in VOICE_PRESETS:
            self._voice_combo.addItem(label, value)
        self._voice_combo.currentIndexChanged.connect(self._voice_combo_changed)
        self._register_gaze(self._voice_combo, self._cycle_voice_preset, "Glas")

        voice_layout.addWidget(voice_title, 0, 0)
        voice_layout.addWidget(voice_hint, 1, 0)
        voice_layout.addWidget(self._voice_combo, 0, 1, 2, 1)
        layout.addWidget(voice_row)

        actions_label = QLabel("AKCIJE", page)
        actions_label.setObjectName("groupLabel")
        layout.addWidget(actions_label)
        actions = QGridLayout()
        actions.setHorizontalSpacing(12)
        self._test_speech_button = self._make_button(
            "Isprobaj govor",
            self._request_speech_test,
            icon_name="fa5s.play",
            object_name="primaryButton",
            minimum_size=QSize(240, 64),
        )
        actions.addWidget(self._test_speech_button, 0, 0)
        self._learned_words_button = self._make_button(
            "Naučene riječi", self._open_learning, minimum_size=QSize(240, 64)
        )
        actions.addWidget(self._learned_words_button, 0, 1)
        actions.setColumnStretch(2, 1)
        layout.addLayout(actions)
        layout.addStretch(1)

        return page

    def _build_learning_page(self) -> QWidget:
        page = QFrame(self)
        page.setObjectName("contentPanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Naučene riječi", page)
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        header.addWidget(
            self._make_button("Nazad", lambda: self._select_tab(2), minimum_size=QSize(170, 68))
        )
        layout.addLayout(header)
        hint = QLabel(
            "Odaberite riječ, zatim Zaboravi riječ. Riječ iz osnovnog rječnika i dalje se može pojaviti u prijedlozima.",
            page,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(12)
        self._word_buttons = []
        for index in range(6):
            button = self._make_button(
                "·",
                lambda index=index: self._choose_word(index),
                checkable=True,
                minimum_size=QSize(170, 76),
            )
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            grid.addWidget(button, index // 2, index % 2)
            self._word_buttons.append(button)
        layout.addLayout(grid, 1)
        self._word_selection_label = QLabel("Odaberite riječ.", page)
        self._word_selection_label.setWordWrap(True)
        layout.addWidget(self._word_selection_label)
        actions = QHBoxLayout()
        self._word_previous = self._make_button(
            "Prethodna", lambda: self._change_word_page(-1), minimum_size=QSize(160, 68)
        )
        self._word_next = self._make_button(
            "Sljedeća", lambda: self._change_word_page(1), minimum_size=QSize(160, 68)
        )
        self._word_page_label = QLabel("1 / 1", page)
        self._forget_word_button = self._make_button(
            "Zaboravi riječ", self._forget_word, minimum_size=QSize(180, 68)
        )
        for widget in (
            self._word_previous,
            self._word_page_label,
            self._word_next,
            self._forget_word_button,
        ):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self._learning_status_label = QLabel("", page)
        self._learning_status_label.setWordWrap(True)
        layout.addWidget(self._learning_status_label)
        self._retry_learning_button = self._make_button(
            "Pokušaj ponovo", self._retry_learning, minimum_size=QSize(200, 68)
        )
        layout.addWidget(self._retry_learning_button)
        self._refresh_learning()
        return page

    def _open_learning(self) -> None:
        self._select_tab(2)
        self._stack.setCurrentIndex(3)
        self._word_page = 0
        self._chosen_word = None
        self._refresh_learning()

    def _refresh_learning(self) -> None:
        if self._last_learning_point is not None:
            for button in self._word_buttons:
                if button.isVisible() and QRect(
                    button.mapToGlobal(QPoint(0, 0)), button.size()
                ).contains(self._last_learning_point):
                    self._blocked_learning_button = button
                    break
        self.cancel_gaze_interaction(require_leave=True)
        learned = self._suggestions.store.learned_words()
        pages = max(1, (len(learned) + 5) // 6)
        self._word_page = min(self._word_page, pages - 1)
        self._visible_words = learned[self._word_page * 6 : self._word_page * 6 + 6]
        if self._chosen_word not in self._visible_words:
            self._chosen_word = None
        for index, button in enumerate(self._word_buttons):
            word = self._visible_words[index] if index < len(self._visible_words) else ""
            button.setText(word.upper() if word else "·")
            button.setAccessibleName(word.upper() if word else "Nema naučene riječi")
            button.setChecked(bool(word) and word == self._chosen_word)
            button.setEnabled(bool(word) and not self._learning_busy)
            self._gaze_names[button] = word.upper()
        self._word_previous.setEnabled(self._word_page > 0 and not self._learning_busy)
        self._word_next.setEnabled(self._word_page + 1 < pages and not self._learning_busy)
        self._word_page_label.setText(f"{self._word_page + 1} / {pages}")
        self._forget_word_button.setEnabled(
            self._chosen_word is not None and not self._learning_busy
        )
        self._retry_learning_button.setEnabled(
            bool(self._suggestions.store.error) and not self._learning_busy
        )
        self._word_selection_label.setText(
            f"Odabrano: {self._chosen_word.upper()}"
            if self._chosen_word
            else "Odaberite riječ."
            if learned
            else "Naučene riječi nisu učitane."
            if self._suggestions.store.error
            else "Nema naučenih riječi."
        )
        self._learning_status_label.setText(
            "Spremam promjenu…"
            if self._learning_busy
            else self._suggestions.store.error or "Učenje je sačuvano."
        )

    def _choose_word(self, index: int) -> None:
        if not self._learning_busy and 0 <= index < len(self._visible_words):
            self._chosen_word = self._visible_words[index]
            self._refresh_learning()

    def _change_word_page(self, delta: int) -> None:
        self._word_page = max(0, self._word_page + delta)
        self._chosen_word = None
        self._refresh_learning()

    def _forget_word(self) -> None:
        if self._chosen_word is None or self._learning_busy:
            return
        word = self._chosen_word
        self._learning_busy = True
        self._refresh_learning()
        self._suggestions.forget(word)

    def _retry_learning(self) -> None:
        if self._learning_busy:
            return
        self._learning_busy = True
        self._refresh_learning()
        self._suggestions.retry()

    def _learning_saved(self, successful: bool) -> None:
        was_busy = self._learning_busy
        self._learning_busy = False
        self._refresh_learning()
        if was_busy:
            self._set_status(
                "Promjena je sačuvana."
                if successful
                else "Promjena nije sačuvana. Pokušajte ponovo."
            )

    def _learning_status_changed(self, _message: str) -> None:
        if self._stack.currentIndex() == 3:
            self._refresh_learning()

    def _make_adjust_row(
        self,
        title: str,
        hint: str,
        value_label: QLabel,
        decrease: GazeCallback,
        increase: GazeCallback,
    ) -> QFrame:
        row = QFrame(self)
        row.setObjectName("settingRow")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QGridLayout(row)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(5)
        layout.setColumnStretch(0, 1)

        title_label = QLabel(title, row)
        title_label.setObjectName("settingTitle")
        hint_label = QLabel(hint, row)
        hint_label.setObjectName("settingHint")
        hint_label.setWordWrap(True)

        minus_button = self._make_button(
            "Manje",
            decrease,
            icon_name="fa5s.minus",
            object_name="adjustButton",
            minimum_size=QSize(112, 58),
        )
        plus_button = self._make_button(
            "Više",
            increase,
            icon_name="fa5s.plus",
            object_name="adjustButton",
            minimum_size=QSize(112, 58),
        )

        layout.addWidget(title_label, 0, 0)
        layout.addWidget(hint_label, 1, 0)
        layout.addWidget(minus_button, 0, 1, 2, 1)
        layout.addWidget(value_label, 0, 2, 2, 1)
        layout.addWidget(plus_button, 0, 3, 2, 1)

        return row

    def _make_value_label(self, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        label.setObjectName("valueLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumWidth(160)
        return label

    def _make_button(
        self,
        text: str,
        callback: GazeCallback,
        icon_name: str = "",
        object_name: str = "",
        checkable: bool = False,
        minimum_size: QSize | None = None,
    ) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setMinimumSize(minimum_size or QSize(150, 58))
        button.setCheckable(checkable)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(22, 22))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if object_name:
            button.setObjectName(object_name)
        if icon_name:
            button.setIcon(self._icon(icon_name))
        button.pressed.connect(lambda: self.cancel_gaze_interaction(require_leave=True))
        button.clicked.connect(lambda _checked=False, item=callback: item())
        self._register_gaze(button, callback, text)
        return button

    def _make_checkbox(
        self,
        text: str,
        callback: GazeCallback,
        minimum_size: QSize | None = None,
    ) -> QCheckBox:
        checkbox = QCheckBox(text, self)
        checkbox.setMinimumSize(minimum_size or QSize(250, 58))
        checkbox.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        checkbox.pressed.connect(lambda: self.cancel_gaze_interaction(require_leave=True))
        checkbox.clicked.connect(lambda _checked=False, item=callback: item())
        self._register_gaze(checkbox, callback, text)
        return checkbox

    def _register_gaze(self, widget: QWidget, callback: GazeCallback, name: str) -> None:
        self._gaze_actions[widget] = callback
        self._gaze_names[widget] = name
        widget.setProperty("gazeTarget", False)
        widget.setProperty("gazePulse", "")

    def _gaze_action_at(self, point: QPoint) -> tuple[QWidget, GazeCallback] | None:
        for widget, action in self._gaze_actions.items():
            if not widget.isVisible() or not widget.isEnabled():
                continue

            top_left = widget.mapToGlobal(QPoint(0, 0))
            rect = QRect(top_left, widget.size())
            if rect.contains(point):
                return widget, action

        return None

    def _set_gaze_target(self, widget: QWidget | None) -> None:
        if widget is self._gaze_target:
            return

        if self._gaze_target is not None:
            self._set_target_property(self._gaze_target, False)

        self._gaze_target = widget

        if self._gaze_target is not None:
            self._set_target_property(self._gaze_target, True)

    def _set_target_property(self, widget: QWidget, active: bool) -> None:
        set_gaze_feedback(widget, active)

    def _emit_interaction_progress(self, widget: QWidget, progress: float) -> None:
        self._interaction_active = True
        self.interaction_progress_changed.emit(
            self._widget_global_center(widget),
            progress,
            self._gaze_names.get(widget, "Odaberi"),
        )

    def _finish_interaction(self, widget: QWidget) -> None:
        self._interaction_active = False
        self.interaction_finished.emit(
            self._widget_global_center(widget),
            self._gaze_names.get(widget, "Odaberi"),
        )

    def _cancel_interaction(self) -> None:
        if not self._interaction_active:
            return

        self._interaction_active = False
        self.interaction_cancelled.emit()

    def _widget_global_center(self, widget: QWidget) -> QPoint:
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, widget.size()).center()

    def _select_tab(self, index: int) -> None:
        self.cancel_gaze_interaction(require_leave=True)
        self._stack.setCurrentIndex(index)
        self._general_tab_button.setChecked(index == 0)
        self._gaze_tab_button.setChecked(index == 1)
        self._speech_tab_button.setChecked(index == 2)
        if index == 0:
            self._set_status("Opće postavke")
        elif index == 1:
            self._set_status("Postavke pogleda")
        elif index == 2:
            self._set_status("Postavke govora")

    def _toggle_move_pointer(self) -> None:
        checked = not self._gaze_settings.move_mouse
        self._gaze_settings = replace(self._gaze_settings, move_mouse=checked)
        self._emit_gaze_settings("Pomjeranje pokazivača je ažurirano.")

    def _toggle_gaze_bubble(self) -> None:
        checked = not self._gaze_settings.show_gaze_bubble
        self._gaze_settings = replace(self._gaze_settings, show_gaze_bubble=checked)
        self._emit_gaze_settings("Oznaka pogleda je ažurirana.")

    def _toggle_interaction_overlay(self) -> None:
        checked = not self._gaze_settings.show_interaction_overlay
        self._gaze_settings = replace(self._gaze_settings, show_interaction_overlay=checked)
        self._emit_gaze_settings("Prikaz napretka radnje je ažuriran.")

    def _toggle_precision_zoom(self) -> None:
        checked = not self._gaze_settings.use_precision_zoom
        self._gaze_settings = replace(self._gaze_settings, use_precision_zoom=checked)
        status = (
            "Precizno uvećanje je uključeno." if checked else "Precizno uvećanje je isključeno."
        )
        self._emit_gaze_settings(status)

    def _toggle_start_with_windows(self) -> None:
        desired = not self._gaze_settings.start_with_windows
        self._set_status(
            "Uključujem pokretanje uz Windows." if desired else "Isključujem pokretanje uz Windows."
        )
        result = set_windows_startup_enabled(
            desired,
            show_launcher_window=self._gaze_settings.show_launcher_window,
        )
        self._gaze_settings = replace(self._gaze_settings, start_with_windows=result.enabled)
        self._emit_gaze_settings(result.message)

    def _toggle_logging_enabled(self) -> None:
        desired = not self._gaze_settings.logging_enabled
        status = "Zapisivanje je uključeno." if desired else "Zapisivanje je isključeno."
        try:
            self._gaze_settings = replace(self._gaze_settings, logging_enabled=desired)
            set_application_logging_enabled(desired)
        except Exception:
            logger.exception("Could not update application logging state.")
            self._gaze_settings = replace(self._gaze_settings, logging_enabled=not desired)
            status = "Ažuriranje zapisivanja nije uspjelo."
        self._emit_gaze_settings(status)

    def _toggle_show_launcher_window(self) -> None:
        desired = not self._gaze_settings.show_launcher_window
        status = (
            "PowerShell prozor će biti prikazan pri pokretanju."
            if desired
            else "PowerShell pokretač će raditi tiho u pozadini."
        )
        self._gaze_settings = replace(self._gaze_settings, show_launcher_window=desired)

        if self._gaze_settings.start_with_windows:
            result = set_windows_startup_enabled(True, show_launcher_window=desired)
            self._gaze_settings = replace(self._gaze_settings, start_with_windows=result.enabled)
            if not result.success:
                status = result.message

        self._emit_gaze_settings(status)

    def _adjust_dwell_ms(self, delta: int) -> None:
        value = _clamp_int(self._gaze_settings.dwell_ms + delta, 150, 5000)
        self._gaze_settings = replace(self._gaze_settings, dwell_ms=value)
        self._emit_gaze_settings("Vrijeme zadržavanja pogleda je ažurirano.")

    def _adjust_selection_pause(self, delta: int) -> None:
        value = _clamp_int(
            self._gaze_settings.selection_pause_ms + delta,
            MIN_SELECTION_PAUSE_MS,
            MAX_SELECTION_PAUSE_MS,
        )
        self._gaze_settings = replace(self._gaze_settings, selection_pause_ms=value)
        self._emit_gaze_settings("Pauza prije odabira je ažurirana.")

    def _adjust_dwell_radius(self, delta: int) -> None:
        value = _clamp_int(self._gaze_settings.dwell_radius_px + delta, 10, 160)
        self._gaze_settings = replace(self._gaze_settings, dwell_radius_px=value)
        self._emit_gaze_settings("Radijus stabilnog pogleda je ažuriran.")

    def _adjust_click_cooldown(self, delta: int) -> None:
        value = _clamp_int(self._gaze_settings.click_cooldown_ms + delta, 100, 5000)
        self._gaze_settings = replace(self._gaze_settings, click_cooldown_ms=value)
        self._emit_gaze_settings("Pauza između radnji je ažurirana.")

    def _adjust_smoothing(self, delta: float) -> None:
        value = round(_clamp_float(self._gaze_settings.smoothing + delta, 0.05, 1.0), 2)
        self._gaze_settings = replace(self._gaze_settings, smoothing=value)
        self._emit_gaze_settings("Uglađivanje pokazivača je ažurirano.")

    def _adjust_speech_speed(self, delta: int) -> None:
        value = _clamp_int(self._speech_settings.speed + delta, 80, 320)
        self._speech_settings = replace(self._speech_settings, speed=value)
        self._emit_speech_settings("Brzina govora je ažurirana.")

    def _adjust_letters_per_group(self, delta: int) -> None:
        value = _clamp_int(self._speech_settings.letters_per_group + delta, 1, 12)
        self._speech_settings = replace(self._speech_settings, letters_per_group=value)
        self._emit_speech_settings("Broj slova u grupi je ažuriran.")

    def _voice_combo_changed(self, index: int) -> None:
        value = self._voice_combo.itemData(index)
        if not isinstance(value, str) or not value:
            value = VOICE_PRESET_DEFAULT

        if value == self._speech_settings.voice_preset:
            return

        self._speech_settings = replace(self._speech_settings, voice_preset=value)
        self._emit_speech_settings("Glas je ažuriran.")

    def _cycle_voice_preset(self) -> None:
        count = self._voice_combo.count()
        if count <= 0:
            return

        next_index = (self._voice_combo.currentIndex() + 1) % count
        self._voice_combo.setCurrentIndex(next_index)

    def _emit_gaze_settings(self, status: str) -> None:
        self._refresh_values()
        self.gaze_settings_changed.emit(replace(self._gaze_settings))
        self._set_status(status)

    def _emit_speech_settings(self, status: str) -> None:
        self._refresh_values()
        self.speech_settings_changed.emit(replace(self._speech_settings))
        self._set_status(status)

    def _request_calibration(self) -> None:
        self._set_status("Pokrećem Tobii kalibraciju.")
        self.calibration_requested.emit()

    def _request_speech_test(self) -> None:
        self._set_status("Isprobavam govor.")
        self.speech_test_requested.emit()

    def _initialize_release_update(self) -> None:
        if self._update_manager is None or not self._update_manager.supported:
            self._check_update_button.setEnabled(False)
            self._update_status_label.setText(
                "Provjera ažuriranja dostupna je u instaliranoj Windows verziji."
            )
            return

        self._update_manager.check_started.connect(self._release_check_started)
        self._update_manager.check_completed.connect(self._release_check_completed)
        self._update_manager.check_failed.connect(self._release_check_failed)
        self._check_for_updates()

    def _check_for_updates(self) -> None:
        if self._update_manager is None:
            return
        self._release_check_started()
        self._update_manager.check()

    def _release_check_started(self) -> None:
        self._check_update_button.setEnabled(False)
        self._update_button.setEnabled(False)
        self._update_button.hide()
        self._update_status_label.setText("Provjeravam posljednje stabilno izdanje...")
        self._set_status("Provjeravam ažuriranja.")

    def _release_check_completed(self, result: object) -> None:
        if not isinstance(result, ReleaseCheckResult):
            self._release_check_failed("GitHub nije vratio ispravne podatke o izdanju.")
            return

        installed = str(result.installed_version)
        latest = str(result.latest_version)
        self._check_update_button.setEnabled(True)
        if result.update_available:
            self._update_status_label.setText(
                f"Dostupna je verzija v{latest}. Trenutno koristite v{installed}."
            )
            self._update_button.setText(f"Ažuriraj na v{latest}")
            self._update_button.setEnabled(True)
            self._update_button.show()
            self._set_status(f"Dostupno je ažuriranje na v{latest}.")
            return

        self._update_button.hide()
        if result.latest_version == result.installed_version:
            message = f"Koristite najnoviju verziju v{installed}."
        else:
            message = f"Instalirana verzija v{installed} novija je od stabilne v{latest}."
        self._update_status_label.setText(message)
        self._set_status(message)

    def _release_check_failed(self, message: str) -> None:
        self._check_update_button.setEnabled(True)
        self._update_button.setEnabled(False)
        self._update_button.hide()
        self._update_status_label.setText(message)
        self._set_status("Provjera ažuriranja nije uspjela.")

    def _request_update(self) -> None:
        self.cancel_gaze_interaction(require_leave=True)
        self._check_update_button.setEnabled(False)
        self._update_button.setEnabled(False)
        self._update_status_label.setText(
            "Pokrećem updater. Aplikacija će se zatvoriti i pokrenuti nakon instalacije."
        )
        self._set_status("Pokrećem ažuriranje.")
        self.update_requested.emit()

    def show_update_error(self, message: str) -> None:
        self._check_update_button.setEnabled(True)
        self._update_button.setEnabled(True)
        self._update_status_label.setText(message)
        self._set_status("Pokretanje ažuriranja nije uspjelo.")

    def _request_quit(self) -> None:
        self._set_status("Isključujem aplikaciju.")
        self.quit_requested.emit()

    def _refresh_values(self) -> None:
        self._selection_pause_value.setText(f"{self._gaze_settings.selection_pause_ms} ms")
        self._dwell_value.setText(f"{self._gaze_settings.dwell_ms} ms")
        self._radius_value.setText(f"{self._gaze_settings.dwell_radius_px} px")
        self._cooldown_value.setText(f"{self._gaze_settings.click_cooldown_ms} ms")
        self._smoothing_value.setText(f"{self._gaze_settings.smoothing:.2f}")
        self._speed_value.setText(f"{self._speech_settings.speed} riječi/min")
        self._letters_group_value.setText(str(self._speech_settings.letters_per_group))
        self._move_pointer_button.setChecked(self._gaze_settings.move_mouse)
        self._gaze_bubble_button.setChecked(self._gaze_settings.show_gaze_bubble)
        self._interaction_overlay_button.setChecked(self._gaze_settings.show_interaction_overlay)
        self._precision_zoom_checkbox.setChecked(self._gaze_settings.use_precision_zoom)
        self._startup_checkbox.setChecked(self._gaze_settings.start_with_windows)
        self._logging_checkbox.setChecked(self._gaze_settings.logging_enabled)
        self._launcher_window_checkbox.setChecked(self._gaze_settings.show_launcher_window)
        self._sync_voice_combo()

    def _sync_voice_combo(self) -> None:
        desired = self._speech_settings.voice_preset or VOICE_PRESET_DEFAULT
        index = self._voice_combo.findData(desired)
        if index < 0:
            index = self._voice_combo.findData(VOICE_PRESET_DEFAULT)
        if index < 0 or index == self._voice_combo.currentIndex():
            return

        previous = self._voice_combo.blockSignals(True)
        try:
            self._voice_combo.setCurrentIndex(index)
        finally:
            self._voice_combo.blockSignals(previous)

    def _set_status(self, text: str) -> None:
        logger.info("Settings status: %s", text)
        self._status_label.setText(text)

    def _install_shortcuts(self) -> None:
        self._escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._escape_shortcut.activated.connect(self.close)

    def _icon(self, icon_name: str) -> QIcon:
        try:
            import qtawesome as qta

            return qta.icon(icon_name, color="#f8f7f2")
        except Exception:
            logger.exception("Could not load qtawesome icon %s; using fallback.", icon_name)
            return self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def _sync_startup_setting_from_windows(self) -> None:
        try:
            self._gaze_settings = replace(
                self._gaze_settings,
                start_with_windows=is_windows_startup_enabled(),
            )
        except Exception:
            logger.exception("Could not sync Windows startup state.")


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, value))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _checkbox_x_image_url() -> str:
    return Path(__file__).with_name("assets").joinpath("checkbox_x.svg").resolve().as_posix()
