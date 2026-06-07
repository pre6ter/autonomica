# Autonomica

Автономный сервис для **Debian 12 (GUI)**, который позволяет локальной LLM
управлять компьютером — мышью и клавиатурой — для выполнения задач (в первую
очередь **браузинг**). Модель крутится в **LM Studio** (OpenAI-совместимый API),
а задачи ставятся текстовой командой через HTTP API.

```
POST /task {"command": "..."}  ->  [скриншот] -> LLM -> действие -> ввод -> повтор
```

## Архитектура

```
HTTP API (FastAPI)                src/server.py
   └─ TaskManager (1 задача)      src/task_manager.py
        └─ Agent (цикл)           src/agent.py
             ├─ ScreenCapturer    src/screen.py      (mss: скриншот + масштаб)
             ├─ LLMClient         src/llm_client.py  (LM Studio, vision)
             ├─ prompts           src/prompts.py     (схема действий)
             └─ Controller        src/controller.py  (pyautogui / xdotool / ydotool)
```

Цикл агента: **захват экрана → запрос к LLM → JSON-действие → исполнение ввода → повтор**,
пока модель не вернёт `done`/`fail` или не будет достигнут лимит шагов.

## Важно про модель и зрение

Управление GUI по скриншотам требует **vision-модели** (напр. `Qwen2.5-VL`,
`Qwen3-VL`). Текстовая `qwen3-30b-a3b` **не видит экран**, поэтому:

- `VISION_ENABLED=true` — слать скриншоты (нужна VL-модель). Полноценный режим.
- `VISION_ENABLED=false` — текстовый режим без картинок: агент опирается на
  `open_url`, `launch`, горячие клавиши и ввод текста, но не на точные клики.
  Подходит для простого браузинга (открыть URL, ввести запрос), но не для
  произвольной навигации по элементам.

Рекомендация: для реального браузинга загрузите в LM Studio VL-модель и включите
`VISION_ENABLED=true`.

## Требования

- Debian 12 с графической сессией. **Лучше X11**, а не Wayland
  (см. раздел «X11 vs Wayland»).
- Python 3.10+
- LM Studio с поднятым локальным сервером (по умолчанию `http://localhost:1234/v1`)
- Системные пакеты для X11-бэкендов:
  ```bash
  sudo apt update
  sudo apt install -y scrot xdotool xdg-utils
  # python-xlib ставится из requirements.txt
  ```

## Установка и запуск

```bash
git clone <repo> autonomica && cd autonomica
./run.sh          # создаст venv, поставит зависимости, скопирует .env, запустит сервис
```

Или вручную:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # отредактируйте под себя
python src/server.py
```

## Настройка LM Studio

1. Загрузите модель (для зрения — VL-вариант).
2. Вкладка **Developer / Local Server** → Start Server (порт 1234).
3. Скопируйте имя модели в `MODEL_NAME` в `.env`.

## API

| Метод | Путь | Назначение |
|------|------|------------|
| GET  | `/health` | статус сервиса |
| POST | `/task` | поставить задачу (`{"command": "...", "wait": false}`) |
| GET  | `/task/{id}` | статус и шаги задачи |
| GET  | `/tasks` | список задач |
| POST | `/task/{id}/cancel` | отменить выполнение |

Пример:

```bash
# поставить задачу и не ждать
curl -X POST http://127.0.0.1:8077/task \
  -H 'Content-Type: application/json' \
  -d '{"command": "Открой ya.ru и найди погоду в Москве"}'

# поставить и дождаться итога
curl -X POST http://127.0.0.1:8077/task \
  -H 'Content-Type: application/json' \
  -d '{"command": "Открой википедию про Debian", "wait": true, "timeout": 180}'

# статус
curl http://127.0.0.1:8077/task/<task_id>
```

Если задан `SERVICE_API_KEY`, добавляйте заголовок `-H 'X-API-Key: <ключ>'`.

Одновременно выполняется **только одна** задача (агент владеет мышью/клавиатурой);
повторный `POST /task` во время активной задачи вернёт `409`.

## Веб-чат (`HOST/chat`)

Встроенный чат-интерфейс в стиле ChatGPT: команда вводится в чат, в ответ
показывается живой процесс «thinking» (мысли и действия по шагам), итог
(успешно/неуспешно) и **скриншот конечного состояния экрана**.

- Адрес: `http://<host>:<port>/chat` (через nginx — по вашему маршруту).
- **Авторизация — HTTP Basic:** логин `admin`, пароль = значение `SERVICE_API_KEY`
  из `.env`. Если `SERVICE_API_KEY` пуст — чат отдаёт `503` (задайте ключ).
- Эндпоинты чата: `POST /chat/command`, `GET /chat/task/{id}`,
  `GET /chat/screenshot/{id}` (все под Basic-авторизацией).

Финальный скриншот сохраняется в `logs/shots/<task_id>/final.png` независимо от
`SAVE_STEP_SCREENSHOTS`.

> При работе через nginx убедитесь, что прокси пробрасывает заголовок
> `Authorization` (по умолчанию так и есть) и не режёт долгие запросы
> (`proxy_read_timeout`).

## Действия, доступные модели

`click`, `double_click`, `move`, `type`, `key` (горячие клавиши), `scroll`,
`open_url`, `launch`, `wait`, `done`, `fail`. Полная схема — в `src/prompts.py`.
Координаты модель задаёт в системе координат присланного (уменьшенного) скриншота,
а агент пересчитывает их в реальные пиксели.

## X11 vs Wayland

- **X11 (рекомендуется):** `pyautogui` или `xdotool` работают «из коробки».
  В GNOME на экране входа выберите «GNOME on Xorg».
- **Wayland:** глобальная эмуляция ввода ограничена. Используйте `INPUT_BACKEND=ydotool`
  (нужен запущенный `ydotoold` и доступ к `/dev/uinput`), захват экрана — через
  portal. Горячие клавиши через `ydotool` в этом проекте не реализованы — для
  надёжной работы используйте X11.

## Автозапуск (systemd)

См. `deploy/autonomica.service` — это **user**-сервис (нужен доступ к графической
сессии). Кратко:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/autonomica.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autonomica.service
loginctl enable-linger $USER   # для старта без активного логина
journalctl --user -u autonomica -f
```

## Безопасность

Агент имеет полный контроль над вводом — это потенциально опасно. Рекомендуется:

- запускать под **отдельным пользователем** с урезанными правами или в VM;
- держать `SERVICE_API_KEY` непустым и слушать только `127.0.0.1`;
- помнить про prompt injection: вредоносный текст на экране может повлиять на модель;
- логи и скриншоты шагов пишутся в `logs/` (`SAVE_STEP_SCREENSHOTS`).

## Ограничения

- Точность кликов зависит от качества vision-модели (grounding). Для повышения
  можно добавить препроцессор разметки UI (напр. OmniParser) — не входит в проект.
- Каждый шаг = скриншот + инференс, поэтому скорость ограничена локальной моделью.
- Координаты пересчитываются из уменьшенного изображения — на нестандартных
  разрешениях возможны промахи (настройте `SCREENSHOT_MAX_SIDE`).

## Структура проекта

```
.
├── README.md
├── requirements.txt
├── run.sh
├── .env.example
├── deploy/
│   └── autonomica.service
└── src/
    ├── server.py        # FastAPI: HTTP API
    ├── task_manager.py  # очередь/блокировка задач
    ├── agent.py         # агентный цикл
    ├── screen.py        # захват экрана
    ├── controller.py    # мышь/клавиатура
    ├── llm_client.py    # клиент LM Studio + парсинг JSON
    ├── prompts.py       # системные промпты, схема действий
    └── config.py        # конфигурация из .env
```
