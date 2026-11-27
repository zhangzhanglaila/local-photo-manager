"""Window chrome wiring: status bar panels, theme and language menus."""

from __future__ import annotations

from types import SimpleNamespace

from iPhoto.gui.coordinators.window_chrome_coordinator import WindowChromeCoordinator


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _Action:
    def __init__(self) -> None:
        self.triggered = _Signal()
        self.checked: bool | None = None

    def setChecked(self, value: bool) -> None:
        self.checked = value


class _Settings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value


class _StatusBar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __getattr__(self, name: str):
        if not name.startswith("handle_"):
            raise AttributeError(name)

        def record(*args) -> None:
            self.calls.append((name, args))

        return record


class _Dialog:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class _Grid:
    def __init__(self) -> None:
        self.scan_started = 0
        self.scan_completed = 0
        self.retranslated = 0

    def set_scan_started(self) -> None:
        self.scan_started += 1

    def set_scan_completed(self) -> None:
        self.scan_completed += 1

    def retranslate(self) -> None:
        self.retranslated += 1


class _Retranslatable:
    def __init__(self) -> None:
        self.retranslated = 0

    def retranslate(self) -> None:
        self.retranslated += 1


def _make(settings: _Settings):
    grid = _Grid()
    header = _Retranslatable()
    sidebar = _Retranslatable()
    library = SimpleNamespace(
        scanProgress=_Signal(),
        scanFinished=_Signal(),
        errorRaised=_Signal(),
    )
    updates = SimpleNamespace(scanProgress=_Signal(), scanFinished=_Signal())
    facade = SimpleNamespace(
        library_updates=updates,
        scanBatchFailed=_Signal(),
        scanProgress=_Signal(),
        scanFinished=_Signal(),
        loadStarted=_Signal(),
        loadProgress=_Signal(),
        loadFinished=_Signal(),
        import_service=SimpleNamespace(
            importStarted=_Signal(),
            importProgress=_Signal(),
            importFinished=_Signal(),
        ),
        move_service=SimpleNamespace(
            moveStarted=_Signal(),
            moveProgress=_Signal(),
            moveFinished=_Signal(),
        ),
        errorRaised=_Signal(),
    )
    ui = SimpleNamespace(
        grid_view=grid,
        main_header=header,
        sidebar=sidebar,
        theme_system=_Action(),
        theme_light=_Action(),
        theme_dark=_Action(),
        language_zh=_Action(),
        language_en=_Action(),
    )
    context = SimpleNamespace(
        settings=settings,
        library=library,
        language=SimpleNamespace(languageChanged=_Signal()),
    )
    status = _StatusBar()
    dialog = _Dialog()
    moves: list[tuple] = []
    chrome = WindowChromeCoordinator(
        SimpleNamespace(ui=ui),
        context,
        facade=facade,
        status_bar=status,
        dialog=dialog,
        on_move_finished=lambda *args: moves.append(args),
    )
    return chrome, ui, context, facade, status, dialog, moves


def test_connect_restores_theme_and_language_and_forwards_signals() -> None:
    settings = _Settings({"ui.theme": "light", "ui.language": "en"})
    chrome, ui, _context, facade, status, _dialog, moves = _make(settings)

    chrome.connect()
    facade.library_updates.scanProgress.emit(1, 2)
    facade.move_service.moveFinished.emit("source", "destination", True, "ok")

    assert ui.theme_light.checked is True
    assert ui.language_en.checked is True
    assert status.calls[0][0] == "handle_scan_progress"
    assert moves == [("source", "destination", True, "ok")]


def test_theme_and_language_actions_write_settings_and_retranslate() -> None:
    chrome, ui, context, _facade, _status, _dialog, _moves = _make(_Settings())

    chrome.connect()
    ui.theme_dark.triggered.emit()
    ui.language_zh.triggered.emit()
    context.language.languageChanged.emit("zh")

    assert context.settings.values["ui.theme"] == "dark"
    assert context.settings.values["ui.language"] == "zh"
    assert ui.grid_view.retranslated == 1
    assert ui.main_header.retranslated == 1
    assert ui.sidebar.retranslated == 1
