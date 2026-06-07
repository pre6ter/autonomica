"""Агентный цикл управления компьютером: восприятие → решение → действие."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from config import Settings
from controller import BaseController
from llm_client import LLMClient, build_user_message, extract_json
from prompts import system_prompt
from screen import ScreenCapturer

logger = logging.getLogger("autonomica.agent")

# Антизацикливание: на каком числе ОДИНАКОВЫХ подряд действий вмешиваться и
# на каком — аварийно прекращать задачу.
LOOP_WARN = 2    # 3-е одинаковое действие подряд -> Escape + жёсткая подсказка
LOOP_ABORT = 4   # 5-е одинаковое действие подряд -> провал задачи


def _screen_sig(capture) -> str:
    """Дешёвая сигнатура экрана для детекта 'экран не изменился'."""
    thumb = capture.image.convert("L").resize((32, 32))
    return hashlib.md5(thumb.tobytes()).hexdigest()


def _coerce_xy(action: dict[str, Any]) -> tuple[float, float] | None:
    """Извлекает координаты x,y из действия, терпимо к форматам модели.

    Поддерживаются варианты:
      {"x": 10, "y": 20}
      {"x": [10, 20]}            # модель сложила обе координаты в x
      {"coordinate": [10, 20]}   # или отдельным ключом
      {"position": [10, 20]} / {"point": [10, 20]} / {"xy": [10, 20]}
    Возвращает (x, y) или None, если распарсить не удалось.
    """
    def _to_pair(val: Any) -> tuple[float, float] | None:
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                return float(val[0]), float(val[1])
            except (TypeError, ValueError):
                return None
        return None

    # x как массив [x, y]
    pair = _to_pair(action.get("x"))
    if pair is not None:
        return pair

    # отдельные ключи-массивы
    for key in ("coordinate", "coordinates", "position", "point", "xy", "loc"):
        pair = _to_pair(action.get(key))
        if pair is not None:
            return pair

    # классический случай: x и y по отдельности
    x, y = action.get("x"), action.get("y")
    if x is not None and y is not None:
        try:
            return float(x), float(y)
        except (TypeError, ValueError):
            return None
    return None


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class Step:
    index: int
    thought: str
    action: dict[str, Any]
    result: str
    screenshot: str | None = None


@dataclass
class TaskState:
    id: str
    command: str
    status: TaskStatus = TaskStatus.PENDING
    steps: list[Step] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "steps": [
                {
                    "index": s.index,
                    "thought": s.thought,
                    "action": s.action,
                    "result": s.result,
                    "screenshot": s.screenshot,
                }
                for s in self.steps
            ],
        }


class Agent:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        capturer: ScreenCapturer,
        controller: BaseController,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.capturer = capturer
        self.controller = controller

    # --- исполнение отдельных действий ----------------------------------

    def _execute(self, action: dict[str, Any], capture) -> tuple[str, bool]:
        """Выполняет действие. Возвращает (текстовый результат, terminal?)."""
        atype = str(action.get("type", "")).lower()

        if atype in ("done", "fail"):
            return ("", True)

        if atype == "wait":
            secs = float(action.get("seconds", 1.0))
            time.sleep(min(secs, 30.0))
            return (f"Подождали {secs} c", False)

        if atype == "open_url":
            url = str(action.get("url", "")).strip()
            if not url:
                return ("open_url без url", False)
            subprocess.Popen(["xdg-open", url])
            return (f"Открыт URL: {url}", False)

        if atype == "launch":
            cmd = str(action.get("command", "")).strip()
            if not cmd:
                return ("launch без command", False)
            subprocess.Popen(cmd, shell=True)
            return (f"Запущено: {cmd}", False)

        if atype == "type":
            text = str(action.get("text", ""))
            self.controller.type_text(text)
            return (f"Введён текст ({len(text)} симв.)", False)

        if atype == "key":
            combo = str(action.get("keys", ""))
            self.controller.press_keys(combo)
            return (f"Нажато: {combo}", False)

        if atype in ("click", "double_click", "move"):
            xy = _coerce_xy(action)
            if xy is None:
                return (f"Действие {atype} без корректных координат: {action}", False)
            mx, my = xy
            rx, ry = capture.to_real(mx, my)
            if atype == "move":
                self.controller.move(rx, ry)
                return (f"Курсор -> ({rx},{ry})", False)
            if atype == "double_click":
                self.controller.double_click(rx, ry)
                return (f"Двойной клик ({rx},{ry})", False)
            button = str(action.get("button", "left"))
            clicks = int(action.get("clicks", 1))
            self.controller.click(rx, ry, button=button, clicks=clicks)
            return (f"Клик {button} x{clicks} ({rx},{ry})", False)

        if atype == "scroll":
            amount = int(action.get("amount", 0))
            rx = ry = None
            xy = _coerce_xy(action)
            if xy is not None:
                rx, ry = capture.to_real(xy[0], xy[1])
            self.controller.scroll(amount, rx, ry)
            return (f"Скролл {amount}", False)

        return (f"Неизвестное действие: {atype}", False)

    # --- основной цикл ---------------------------------------------------

    def run(self, task: TaskState, cancel_event: threading.Event) -> None:
        task.status = TaskStatus.RUNNING
        vision = self.settings.vision_enabled
        os.makedirs(self.settings.log_dir, exist_ok=True)
        shots_dir = os.path.join(self.settings.log_dir, "shots", task.id)
        os.makedirs(shots_dir, exist_ok=True)

        # начальный системный промпт строим по размеру первого скриншота
        first = self.capturer.capture()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(vision, first.model_width, first.model_height)},
            {"role": "user", "content": f"ЗАДАЧА ПОЛЬЗОВАТЕЛЯ: {task.command}"},
        ]

        last_sig: str | None = None
        identical_streak = 0
        prev_screen_sig: str | None = None
        prev_executed: dict[str, Any] | None = None

        try:
            for step_idx in range(1, self.settings.max_steps + 1):
                if cancel_event.is_set():
                    task.status = TaskStatus.CANCELLED
                    task.result = "Отменено пользователем"
                    break

                capture = self.capturer.capture() if step_idx > 1 else first

                # экран изменился после предыдущего действия?
                screen_sig = _screen_sig(capture)
                screen_changed = prev_screen_sig is None or screen_sig != prev_screen_sig
                prev_screen_sig = screen_sig

                shot_path: str | None = None
                if self.settings.save_step_screenshots:
                    shot_path = os.path.join(shots_dir, f"step_{step_idx:03d}.png")
                    capture.save(shot_path)

                # текст состояния + (опционально) картинка
                no_change = ""
                if prev_executed is not None and not screen_changed:
                    no_change = (
                        " ВНИМАНИЕ: экран НЕ изменился после предыдущего действия — "
                        "оно НЕ сработало. Выбери ДРУГОЕ действие, не повторяй прошлое."
                    )
                state_text = (
                    f"Шаг {step_idx}/{self.settings.max_steps}. "
                    f"Размер изображения: {capture.model_width}x{capture.model_height}. "
                    f"Выбери следующее действие (строго JSON).{no_change}"
                )
                image_url = capture.to_data_url() if vision else None
                self._prune_old_images(messages)
                messages.append(build_user_message(state_text, image_url))

                raw = self.llm.complete(messages)
                messages.append({"role": "assistant", "content": raw})

                try:
                    decision = extract_json(raw)
                except ValueError as exc:
                    logger.warning("Шаг %s: %s", step_idx, exc)
                    messages.append(
                        {"role": "user", "content": "Ответ не был валидным JSON. Верни строго один JSON-объект."}
                    )
                    task.steps.append(Step(step_idx, "(невалидный JSON)", {}, str(exc), shot_path))
                    continue

                thought = str(decision.get("thought", ""))
                action = decision.get("action") or {}
                if not isinstance(action, dict):
                    action = {}
                atype = str(action.get("type", "")).lower()

                logger.info("Шаг %s | %s | %s", step_idx, atype, thought[:120])

                if atype == "done":
                    task.steps.append(Step(step_idx, thought, action, "done", shot_path))
                    task.status = TaskStatus.DONE
                    task.result = str(action.get("summary", "Задача выполнена"))
                    break
                if atype == "fail":
                    task.steps.append(Step(step_idx, thought, action, "fail", shot_path))
                    task.status = TaskStatus.FAILED
                    task.result = str(action.get("reason", "Модель сообщила о невозможности"))
                    break

                # --- антизацикливание ---
                sig = json.dumps(action, sort_keys=True, ensure_ascii=False)
                if sig == last_sig:
                    identical_streak += 1
                else:
                    identical_streak = 0
                    last_sig = sig

                if identical_streak >= LOOP_ABORT:
                    msg = (
                        f"Агент застрял: одно и то же действие ({atype}) повторено "
                        f"{identical_streak + 1} раз подряд без эффекта. Прерываю."
                    )
                    logger.warning(msg)
                    task.steps.append(Step(step_idx, thought, action, "застрял в цикле — прервано", shot_path))
                    task.status = TaskStatus.FAILED
                    task.result = msg
                    break

                if identical_streak >= LOOP_WARN:
                    # принудительно разрываем залипание: Escape закрывает overview,
                    # модальные окна и т.п. Сам повтор НЕ выполняем.
                    try:
                        self.controller.press_keys("escape")
                        broke = "нажат Escape"
                    except Exception:  # noqa: BLE001
                        broke = "Escape не удался"
                    task.steps.append(
                        Step(step_idx, thought, action, f"повтор пропущен ({broke})", shot_path)
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "Ты повторяешь ОДНО И ТО ЖЕ действие, оно не работает. "
                            "Я нажал Escape, чтобы закрыть возможное мешающее окно/режим обзора. "
                            "ЗАПРЕЩЕНО повторять предыдущий клик. Используй другой способ: "
                            "launch (например launch firefox), open_url, горячие клавиши или "
                            "другие координаты."
                        ),
                    })
                    prev_executed = {"type": "key", "keys": "escape"}
                    time.sleep(self.settings.step_delay)
                    continue

                try:
                    result, _ = self._execute(action, capture)
                except Exception as exc:  # noqa: BLE001 - хотим сообщить модели об ошибке
                    result = f"Ошибка выполнения: {exc}"
                    logger.exception("Ошибка действия на шаге %s", step_idx)

                task.steps.append(Step(step_idx, thought, action, result, shot_path))
                messages.append({"role": "user", "content": f"Результат предыдущего действия: {result}"})
                prev_executed = action

                time.sleep(self.settings.step_delay)
            else:
                # цикл завершился без done/fail
                task.status = TaskStatus.FAILED
                task.result = f"Достигнут лимит шагов ({self.settings.max_steps})"

        except Exception as exc:  # noqa: BLE001
            logger.exception("Критическая ошибка агента")
            task.status = TaskStatus.ERROR
            task.error = str(exc)
        finally:
            # финальный скриншот состояния системы (для чат-UI), независимо от
            # настройки save_step_screenshots
            try:
                final = self.capturer.capture()
                final.save(os.path.join(shots_dir, "final.png"))
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось сохранить финальный скриншот", exc_info=True)
            task.finished_at = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _prune_old_images(messages: list[dict[str, Any]]) -> None:
        """Удаляет картинки из всех прошлых user-сообщений, оставляя только текст.

        Это экономит контекст: модели достаточно последнего скриншота, а история
        прошлых действий передаётся текстом.
        """
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [p for p in content if p.get("type") == "text"]
                if text_parts:
                    msg["content"] = text_parts[0].get("text", "")
                else:
                    msg["content"] = ""
