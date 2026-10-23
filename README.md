# ICEQ · контроль нарушений ТБ по видео на LinQ TPU

Кейс LinQ хакатона «Открой Моспром». Сейчас здесь — демо-стенд: YOLOv8n (COCO), скомпилированный
под LinQ TPU, локальный сайт с картинками/видео, описанием задачи и ускорителя, замерами FPS и задержки.

## Архитектура

```
браузер ── localhost:8000 (app/server.py, FastAPI)
              │  кадр → JPEG ~50–150 КБ           (VPN ≈ 4 Мбит/с, тензор 640² float32 = 4.9 МБ не пролезет)
              ▼
        RabbitMQ 10.0.114.30:5672  очередь <login>.relay
              ▼
        relay на сервере Jupyter (app/relay.py): letterbox → тензор → tpu.direct/input → TPU
              ▲                                   DFL + sigmoid + NMS (app/yolo.py) ← ответ TPU
              └── JSON с боксами и таймингами (pre / tpu / post / roundtrip)
```

На TPU выполняется YOLOv8 до последних свёрток головы (6 выходов: box 64 = 4×16 DFL и cls 80
на страйдах 8/16/32); остальное — на CPU. `compile/check_postprocess.py` сверяет нашу
постобработку с полной ONNX-моделью.

## Запуск

1. VPN: OpenVPN GUI → Hakathon → Connect (конфиг и пароль в `%USERPROFILE%\OpenVPN\config`).
2. `.env` в корне: `LINQ_HUB`, `LINQ_USER`, `LINQ_PASS`, `LINQ_AMQP_HOST`, `LINQ_AMQP_PORT`.
3. Сайт:
   ```
   .venv\Scripts\python -m uvicorn app.server:app --port 8000
   ```
   → http://127.0.0.1:8000 (вкладки «Демо», «Задача», «LinQ TPU»; режим CPU — для сравнения).
4. Если relay не отвечает: `.venv\Scripts\python tools\deploy_relay.py` (с `--programs` — ещё и залить `.tpu`).

## Компиляция модели под TPU (WSL, Ubuntu 24.04)

```
wsl -e bash compile/setup_sdk.sh              # окружение SDK ~/linq/sdk (torch CPU, tensorflow, wheels LinQ)
wsl -e bash compile/export_yolo.sh 640        # ultralytics → models/yolov8n_640.onnx
wsl -e bash -c "cd /mnt/d/dev/mosprom && ~/linq/sdk/bin/python compile/compile_yolo.py models/yolov8n_640.onnx"
.venv\Scripts\python tools\deploy_relay.py --programs
```

Калибровка квантования — 64 картинки coco128 (`models/calib`).

## Чего нет в репозитории

- `data/` — рассылка организаторов (кейс, инструкции, `Hakathon.ovpn` с ключом VPN): положить вручную.
- `org_materials/` — SDK и примеры API (ссылки в `data/Процедура_подготовки_и_инференса_сетей_на_LinQ_TPU.pdf`)
  и файлы с сервера Jupyter; на сайте видны во вкладке «Материалы оргов».
- Примеры видео (Intel IoT sample videos, CC BY 4.0):
  ```
  curl -L -o app/static/samples/worker-zone-detection.mp4 https://github.com/intel-iot-devkit/sample-videos/raw/master/worker-zone-detection.mp4
  curl -L -o app/static/samples/people-detection.mp4 https://github.com/intel-iot-devkit/sample-videos/raw/master/people-detection.mp4
  ```
- Датасетов организаторы не выдают (слайд 8 кейса): данные — на выбор команды.

## Утилиты

- `tools/linq_remote.py` — `ls`, `exec [-f file]`, `upload`, `deploy model.tpu` на JupyterHub.
- `tools/tpu_smoke.py` — эталонный ResNet50 напрямую через RabbitMQ (проверка стенда).
- `org_materials/` — SDK, примеры API и файлы с сервера Jupyter от организаторов.
