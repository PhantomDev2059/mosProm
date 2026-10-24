# Где и как работает LinQ Guard

## Схема

```
камеры (RTSP / файлы) ──► сервер с ускорителем ──────────────────────────────┐
                           uvicorn api.main:app (один процесс, supervisor)  │
                           ├─ потоки камер: детекция → правила → события    │
                           ├─ журнал SQLite, кадры и ролики (data/)         │
                           ├─ опознание по лицу (people.db)                  │
                           ├─ смена: тревоги, сроки, отчёты (shift.db)       │
                           └─ Telegram-бот (long polling)  ──► @linq_control_bot
   дашборд ◄── SSH-туннель :8000                                              │
   мини-апп ◄── HTTPS-туннель Cloudflare (только /app, /api/tg, /media) ◄─────┘
```

- **Сервер** — арендованный GPU-сервер (vast.ai, Ubuntu, 2× RTX 3060) для разработки и демо. На стенде LinQ тот же
  код запускается с `LINQ_BACKEND=tpu` и программами `.tpu` из релиза; меняется только бэкенд инференса.
- **Один процесс** (`tools/run_server.sh`, supervisor-программа `linqguard`): веб, камеры, бот. Бот работает
  long polling — второй экземпляр с тем же токеном запускать нельзя.
- **Наружу** через HTTPS (`tools/public_url.sh`, supervisor-программа `tunnel`) видны только мини-апп, его API и
  снимки; дашборд, журнал, зоны — только через SSH-туннель. Адрес туннеля меняется при перезапуске, бот подхватывает
  новый сам. Для постоянного адреса — именованный туннель Cloudflare или свой домен с nginx + Let's Encrypt.
- **Секреты** — `/workspace/linq/secrets/` (токен бота, публичный адрес), не в репозитории.
  Токен: `tools/set_bot_token.sh` (вводится скрыто, пишется сразу на сервер).
- **Данные** — `/workspace/linq/data/`: events.db, shift.db, people.db, review.db, media/, videos/.

## Выкатка изменений

```bash
tools/deploy.sh <порт> <хост>
```
Тесты локально → rsync кода → тесты на сервере → перезапуск с чистым журналом демо → публичный адрес мини-аппа.

## Чистый сервер

1. Код: `rsync` репозитория в `/workspace/linq/app`.
2. Окружение, модели и сервис: `bash tools/bootstrap_vast.sh` (или готовые модели и программы LinQ из релиза v1.0.0 —
   см. README).
3. Демо-камеры с производства: `bash tools/factory_videos.sh`.
4. Лица: `tools/fetch_face_models.sh`.
5. HTTPS для мини-аппа: supervisor-программа `tunnel` с `tools/public_url.sh`.
6. Токен бота: `tools/set_bot_token.sh <порт> <хост>` с ноутбука.
7. Роли: `configs/roles.yaml` (Telegram ID → роль); `demo: true` — роль выбирают сами.

## Репозиторий

GitHub `PhantomDev2059/mosProm`, ветка `linq-guard`; веса, программы LinQ и демо-видео — в релизе `v1.0.0`.
