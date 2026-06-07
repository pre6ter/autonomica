"""Менеджер задач: запускает агентный цикл в фоне, хранит статусы.

Только ОДНА задача может выполняться одновременно (агент физически владеет
мышью и клавиатурой), поэтому используется глобальная блокировка.
"""
from __future__ import annotations

import threading
import uuid

from agent import Agent, TaskState, TaskStatus


class TaskBusyError(RuntimeError):
    pass


class TaskManager:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()
        self._active_id: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def submit(self, command: str) -> TaskState:
        with self._lock:
            if self._active_id is not None:
                raise TaskBusyError(
                    f"Уже выполняется задача {self._active_id}. Дождитесь завершения или отмените её."
                )
            task_id = uuid.uuid4().hex[:12]
            task = TaskState(id=task_id, command=command)
            self._tasks[task_id] = task
            cancel_event = threading.Event()
            self._cancel_events[task_id] = cancel_event
            self._active_id = task_id

        thread = threading.Thread(
            target=self._run, args=(task, cancel_event), name=f"agent-{task_id}", daemon=True
        )
        thread.start()
        return task

    def _run(self, task: TaskState, cancel_event: threading.Event) -> None:
        try:
            self.agent.run(task, cancel_event)
        finally:
            with self._lock:
                if self._active_id == task.id:
                    self._active_id = None

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def list(self) -> list[TaskState]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        event = self._cancel_events.get(task_id)
        if event is None:
            return False
        task = self._tasks.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            event.set()
            return True
        return False
