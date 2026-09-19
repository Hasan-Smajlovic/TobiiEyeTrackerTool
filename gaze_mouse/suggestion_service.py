"""Keep model work and personal-data writes off the Qt event loop."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .suggestion_learning import LearningStore
from .suggestion_model import WordModel, load_model


class SuggestionService(QObject):
    predictions_ready = Signal(object, int, object)
    status_changed = Signal(str)
    storage_finished = Signal(bool)
    _prediction_done = Signal(object)
    _storage_done = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        path: Path | None = None,
        model: WordModel | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = LearningStore(path)
        self._model = model
        self._model_error = ""
        self._pending: dict[object, tuple[int, str]] = {}
        self._predicting = False
        self._saving = False
        self._retry = False
        self._closing = False
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-prediction")
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-learning")
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(150)
        self._save_timer.timeout.connect(self._save)
        self._prediction_done.connect(self._finish_prediction)
        self._storage_done.connect(self._finish_save)
        worker, writer, store = self._worker, self._writer, self.store
        self.destroyed.connect(lambda: worker.shutdown(wait=False, cancel_futures=True))
        self.destroyed.connect(lambda: writer.shutdown(wait=False, cancel_futures=True))
        self.destroyed.connect(
            lambda: store.save() if store.revision != store.saved_revision else None
        )

    @property
    def status(self) -> str:
        return self.store.error or self._model_error

    def request(self, owner: object, revision: int, text: str) -> None:
        if self._closing:
            return
        self._pending[owner] = revision, text
        self._dispatch()

    def persist(self) -> None:
        if not self._closing and self.store.revision != self.store.saved_revision:
            self._save_timer.start()

    def forget(self, word: str) -> None:
        self.store.forget(word)
        self._save_timer.stop()
        self._save()

    def retry(self) -> None:
        self._retry = True
        self._save_timer.stop()
        self._save()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._save_timer.stop()
        self._pending.clear()
        self._worker.shutdown(wait=True, cancel_futures=True)
        self._writer.shutdown(wait=True, cancel_futures=True)
        if self.store.revision != self.store.saved_revision:
            self.store.save()

    def _dispatch(self) -> None:
        if self._predicting or not self._pending or self._closing:
            return
        owner = next(iter(self._pending))
        revision, text = self._pending.pop(owner)
        personal = self.store.snapshot()
        self._predicting = True

        def predict() -> tuple:
            if self._model is None:
                try:
                    self._model = load_model()
                except (OSError, ValueError, KeyError, TypeError):
                    self._model = WordModel({})
                    self._model_error = (
                        "Prijedlozi iz rječnika nisu dostupni. Možete nastaviti pisati."
                    )
            return owner, revision, self._model.predict(text, personal)

        self._worker.submit(predict).add_done_callback(
            lambda future: self._deliver(self._prediction_done, future)
        )

    def _save(self) -> None:
        if self._saving or self._closing:
            return
        self._saving = True
        retry, self._retry = self._retry, False
        self._writer.submit(self.store.save, retry=retry).add_done_callback(
            lambda future: self._deliver(self._storage_done, future)
        )

    @staticmethod
    def _deliver(signal, future: Future) -> None:
        try:
            result = future.result()
        except Exception:
            result = None
        with suppress(RuntimeError):
            signal.emit(result)

    def _finish_prediction(self, result: object) -> None:
        self._predicting = False
        if self._closing:
            return
        if result is not None:
            owner, revision, candidates = result
            self.predictions_ready.emit(owner, revision, candidates)
        else:
            self._model_error = "Prijedlozi nisu dostupni. Možete nastaviti pisati."
        self.status_changed.emit(self.status)
        self._dispatch()

    def _finish_save(self, result: object) -> None:
        self._saving = False
        if self._closing:
            return
        if (result and self.store.revision != self.store.saved_revision) or self._retry:
            self._save()
            return
        self.status_changed.emit(self.status)
        self.storage_finished.emit(bool(result))
