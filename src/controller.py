"""Управление мышью и клавиатурой.

Поддерживаются три бэкенда:
  - pyautogui : X11, чистый Python (нужен установленный python-xlib и scrot/X-сервер)
  - xdotool   : X11, системная утилита
  - ydotool   : Wayland, требует запущенный демон ydotoold и доступ к /dev/uinput

Все методы работают в РЕАЛЬНЫХ пикселях экрана. Перевод из координат модели
выполняется в Capture.to_real() до вызова контроллера.
"""
from __future__ import annotations

import shutil
import subprocess
import time


class ControllerError(RuntimeError):
    pass


# Нормализация имён клавиш, которые может вернуть модель -> внутренние имена
_KEY_ALIASES = {
    "enter": "enter",
    "return": "enter",
    "esc": "escape",
    "escape": "escape",
    "del": "delete",
    "delete": "delete",
    "backspace": "backspace",
    "tab": "tab",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "super": "win",
    "cmd": "win",
    "meta": "win",
}

# Для xdotool требуются X keysym имена
_XDOTOOL_KEYS = {
    "enter": "Return",
    "escape": "Escape",
    "delete": "Delete",
    "backspace": "BackSpace",
    "tab": "Tab",
    "space": "space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "super",
}


def _normalize_combo(combo: str) -> list[str]:
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    return [_KEY_ALIASES.get(p, p) for p in parts]


class BaseController:
    def move(self, x: int, y: int) -> None: ...
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None: ...
    def double_click(self, x: int, y: int) -> None:
        self.click(x, y, "left", 2)
    def type_text(self, text: str) -> None: ...
    def press_keys(self, combo: str) -> None: ...
    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None: ...


class PyAutoGUIController(BaseController):
    def __init__(self) -> None:
        try:
            import pyautogui  # noqa: F401
        except Exception as exc:  # pragma: no cover - зависит от окружения
            raise ControllerError(
                "pyautogui недоступен. Установите зависимости и убедитесь, что "
                "запущена X11-сессия (DISPLAY доступен)."
            ) from exc
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.05
        self._g = pyautogui

    def move(self, x: int, y: int) -> None:
        self._g.moveTo(x, y, duration=0.15)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self._g.click(x=x, y=y, button=button, clicks=clicks, interval=0.08)

    def type_text(self, text: str) -> None:
        self._g.write(text, interval=0.02)

    def press_keys(self, combo: str) -> None:
        keys = _normalize_combo(combo)
        if len(keys) == 1:
            self._g.press(keys[0])
        else:
            self._g.hotkey(*keys)

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self._g.moveTo(x, y, duration=0.1)
        self._g.scroll(amount)


class XdotoolController(BaseController):
    def __init__(self) -> None:
        if not shutil.which("xdotool"):
            raise ControllerError("xdotool не найден. Установите: sudo apt install xdotool")

    def _run(self, *args: str) -> None:
        res = subprocess.run(["xdotool", *args], capture_output=True, text=True)
        if res.returncode != 0:
            raise ControllerError(f"xdotool {' '.join(args)} -> {res.stderr.strip()}")

    def move(self, x: int, y: int) -> None:
        self._run("mousemove", str(x), str(y))

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        self._run("mousemove", str(x), str(y))
        self._run("click", "--repeat", str(clicks), btn)

    def type_text(self, text: str) -> None:
        self._run("type", "--delay", "20", text)

    def press_keys(self, combo: str) -> None:
        keys = _normalize_combo(combo)
        mapped = "+".join(_XDOTOOL_KEYS.get(k, k) for k in keys)
        self._run("key", mapped)

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self._run("mousemove", str(x), str(y))
        button = "4" if amount > 0 else "5"  # 4 = вверх, 5 = вниз
        self._run("click", "--repeat", str(abs(amount) or 1), button)


class YdotoolController(BaseController):
    """Wayland. Требует запущенный ydotoold. Не поддерживает позиционирование мыши
    по абсолютным координатам так же надёжно, как X11 — используйте при крайней
    необходимости."""

    def __init__(self) -> None:
        if not shutil.which("ydotool"):
            raise ControllerError("ydotool не найден. Установите ydotool и запустите ydotoold.")

    def _run(self, *args: str) -> None:
        res = subprocess.run(["ydotool", *args], capture_output=True, text=True)
        if res.returncode != 0:
            raise ControllerError(f"ydotool {' '.join(args)} -> {res.stderr.strip()}")

    def move(self, x: int, y: int) -> None:
        self._run("mousemove", "--absolute", "-x", str(x), "-y", str(y))

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self.move(x, y)
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        for _ in range(clicks):
            self._run("click", code)
            time.sleep(0.05)

    def type_text(self, text: str) -> None:
        self._run("type", text)

    def press_keys(self, combo: str) -> None:
        # ydotool key работает с кодами клавиш Linux; для простоты не разворачиваем
        # сложные комбинации здесь — рекомендуется X11 для надёжной работы.
        raise ControllerError(
            "press_keys через ydotool требует ручного маппинга keycodes. "
            "Рекомендуется бэкенд pyautogui/xdotool на X11."
        )

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        self._run("mousemove", "--wheel", "-y", str(amount))


def make_controller(backend: str) -> BaseController:
    backend = backend.lower().strip()
    if backend == "pyautogui":
        return PyAutoGUIController()
    if backend == "xdotool":
        return XdotoolController()
    if backend == "ydotool":
        return YdotoolController()
    raise ControllerError(f"Неизвестный INPUT_BACKEND: {backend}")
