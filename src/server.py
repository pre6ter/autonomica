"""HTTP-сервис Autonomica.

Принимает текстовые команды по API и запускает агента управления компьютером.

Пример:
  curl -X POST http://127.0.0.1:8077/task \\
       -H 'Content-Type: application/json' \\
       -d '{"command": "Открой википедию и найди статью про Debian"}'
"""
from __future__ import annotations

import glob
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from agent import Agent, TaskStatus
from config import get_settings
from controller import ControllerError, make_controller
from llm_client import LLMClient
from screen import ScreenCapturer
from task_manager import TaskBusyError, TaskManager

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("autonomica.server")

# Состояние сервиса (заполняется при старте)
STATE: dict[str, object] = {"manager": None, "init_error": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.log_dir, exist_ok=True)
    try:
        llm = LLMClient(
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=settings.model_name,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )
        capturer = ScreenCapturer(
            monitor_index=settings.monitor_index,
            max_side=settings.screenshot_max_side,
        )
        controller = make_controller(settings.input_backend)
        agent = Agent(settings, llm, capturer, controller)
        STATE["manager"] = TaskManager(agent)
        logger.info(
            "Autonomica запущена. Модель=%s, vision=%s, backend=%s",
            settings.model_name, settings.vision_enabled, settings.input_backend,
        )
    except ControllerError as exc:
        STATE["init_error"] = str(exc)
        logger.error("Не удалось инициализировать контроллер ввода: %s", exc)
    except Exception as exc:  # noqa: BLE001
        STATE["init_error"] = str(exc)
        logger.exception("Ошибка инициализации")
    yield


app = FastAPI(title="Autonomica", version="0.1.0", lifespan=lifespan)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.service_api_key and x_api_key != settings.service_api_key:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий X-API-Key")


# --- Basic-авторизация для веб-чата: логин admin, пароль = SERVICE_API_KEY ---
basic_security = HTTPBasic()
CHAT_USERNAME = "admin"


def require_basic_auth(credentials: HTTPBasicCredentials = Depends(basic_security)) -> str:
    if not settings.service_api_key:
        raise HTTPException(
            status_code=503,
            detail="Веб-чат недоступен: задайте SERVICE_API_KEY в .env (это пароль для входа).",
        )
    user_ok = secrets.compare_digest(credentials.username, CHAT_USERNAME)
    pass_ok = secrets.compare_digest(credentials.password, settings.service_api_key)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _final_screenshot_path(task_id: str) -> str | None:
    """Путь к финальному скриншоту задачи (или к последнему по номеру шагу)."""
    base = os.path.join(settings.log_dir, "shots", task_id)
    final = os.path.join(base, "final.png")
    if os.path.isfile(final):
        return final
    steps = sorted(glob.glob(os.path.join(base, "step_*.png")))
    return steps[-1] if steps else None


def get_manager() -> TaskManager:
    manager = STATE.get("manager")
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail=f"Сервис не инициализирован: {STATE.get('init_error')}",
        )
    return manager  # type: ignore[return-value]


class TaskRequest(BaseModel):
    command: str = Field(..., description="Текстовая команда-задача для агента")
    wait: bool = Field(default=False, description="Дождаться завершения и вернуть итог")
    timeout: float = Field(default=300.0, description="Таймаут ожидания при wait=true, c")


@app.get("/health")
def health() -> dict[str, object]:
    manager = STATE.get("manager")
    return {
        "status": "ok" if manager else "init_error",
        "init_error": STATE.get("init_error"),
        "model": settings.model_name,
        "vision_enabled": settings.vision_enabled,
        "input_backend": settings.input_backend,
        "active_task": manager.active_id if manager else None,  # type: ignore[union-attr]
    }


@app.post("/task", dependencies=[Depends(require_api_key)])
def create_task(req: TaskRequest, manager: TaskManager = Depends(get_manager)) -> dict[str, object]:
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="Пустая команда")
    try:
        task = manager.submit(req.command.strip())
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not req.wait:
        return {"task_id": task.id, "status": task.status.value}

    deadline = time.time() + req.timeout
    terminal = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ERROR}
    while time.time() < deadline:
        if task.status in terminal:
            break
        time.sleep(0.5)
    return task.to_dict()


@app.get("/task/{task_id}", dependencies=[Depends(require_api_key)])
def get_task(task_id: str, manager: TaskManager = Depends(get_manager)) -> dict[str, object]:
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task.to_dict()


@app.get("/tasks", dependencies=[Depends(require_api_key)])
def list_tasks(manager: TaskManager = Depends(get_manager)) -> dict[str, object]:
    return {"tasks": [t.to_dict() for t in manager.list()]}


@app.post("/task/{task_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_task(task_id: str, manager: TaskManager = Depends(get_manager)) -> dict[str, object]:
    ok = manager.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Задачу нельзя отменить (нет такой или уже завершена)")
    return {"task_id": task_id, "cancelling": True}


# =========================== Веб-чат (HOST/chat) ===========================

_CHAT_HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "chat.html")


class ChatCommand(BaseModel):
    command: str = Field(..., description="Команда из чата")


@app.get("/chat", response_class=HTMLResponse)
def chat_page(_: str = Depends(require_basic_auth)) -> HTMLResponse:
    try:
        with open(_CHAT_HTML_PATH, encoding="utf-8") as fh:
            return HTMLResponse(fh.read())
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="chat.html не найден") from None


@app.post("/chat/command")
def chat_command(
    body: ChatCommand,
    _: str = Depends(require_basic_auth),
    manager: TaskManager = Depends(get_manager),
) -> dict[str, object]:
    if not body.command.strip():
        raise HTTPException(status_code=400, detail="Пустая команда")
    try:
        task = manager.submit(body.command.strip())
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task.id, "status": task.status.value}


@app.get("/chat/task/{task_id}")
def chat_task(
    task_id: str,
    _: str = Depends(require_basic_auth),
    manager: TaskManager = Depends(get_manager),
) -> dict[str, object]:
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    data = task.to_dict()
    data["has_screenshot"] = _final_screenshot_path(task_id) is not None
    return data


@app.get("/chat/screenshot/{task_id}")
def chat_screenshot(task_id: str, _: str = Depends(require_basic_auth)) -> FileResponse:
    path = _final_screenshot_path(task_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Скриншот ещё не готов")
    return FileResponse(path, media_type="image/png")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
