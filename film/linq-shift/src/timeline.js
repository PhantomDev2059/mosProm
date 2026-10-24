// сгенерировано shift_data.py
FILM.TIMELINE = {
 "title": "LinQ Guard — смена",
 "bpm": 120,
 "fps": 24,
 "width": 1920,
 "height": 1080,
 "safeBottom": 1080,
 "duration": 66.0,
 "shots": [
  {
   "id": "open",
   "file": "01-open.js",
   "start": 0,
   "end": 5,
   "mode": "schematic",
   "title": "Смена 24.10",
   "post": 0.3
  },
  {
   "id": "v01-helmet",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Нет каски",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Нет каски: ppe 2.20+5.0",
   "start": 5.0,
   "end": 10.0,
   "post": 0.3
  },
  {
   "id": "v02-vest",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Нет сигнального жилета",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Нет сигнального жилета: ppe 1.60+5.0",
   "start": 10.0,
   "end": 15.0,
   "post": 0.3
  },
  {
   "id": "v03-phone",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Телефон в рабочей зоне",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Телефон в рабочей зоне: phone 1.60+5.0",
   "start": 15.0,
   "end": 20.0,
   "post": 0.3
  },
  {
   "id": "v04-proximity",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Пешеход у погрузчика",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Пешеход у погрузчика: proximity 1.40+5.0",
   "start": 20.0,
   "end": 25.0,
   "post": 0.3
  },
  {
   "id": "v05-smoking",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Курение на территории",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Курение на территории: smoking 7.80+5.0",
   "start": 25.0,
   "end": 30.0,
   "post": 0.3
  },
  {
   "id": "v06-fall",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Падение человека",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Падение человека: fall 0.60+5.0",
   "start": 30.0,
   "end": 35.0,
   "post": 0.3
  },
  {
   "id": "v07-immobile",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Лежит без движения",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Лежит без движения: fall 11.00+5.0",
   "start": 35.0,
   "end": 40.0,
   "post": 0.3
  },
  {
   "id": "v08-running",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Бег по цеху",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 130,
    "fps": 25
   },
   "brief": "Бег по цеху: running 0.80+5.0",
   "start": 40.0,
   "end": 45.0,
   "post": 0.3
  },
  {
   "id": "v09-fire",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Открытый огонь",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 133,
    "fps": 25
   },
   "brief": "Открытый огонь: fire 0.96+5.0",
   "start": 45.0,
   "end": 50.0,
   "post": 0.3
  },
  {
   "id": "v10-smoke",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Задымление",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 45,
    "fps": 25
   },
   "brief": "Задымление: smoke 7.20+5.0",
   "start": 50.0,
   "end": 55.0,
   "post": 0.3
  },
  {
   "id": "summary",
   "file": "90-summary.js",
   "start": 55.0,
   "end": 62.0,
   "mode": "illustrated",
   "title": "Итог смены",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.4
   },
   "post": 0.3
  },
  {
   "id": "close",
   "file": "99-close.js",
   "start": 62.0,
   "end": 66.0,
   "mode": "schematic",
   "title": "Финал",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.4
   },
   "post": 0.3
  }
 ],
 "cues": [
  {
   "t": 0,
   "kind": "open"
  },
  {
   "t": 5.0,
   "kind": "cut"
  },
  {
   "t": 6.6,
   "kind": "alarm",
   "sev": "high"
  },
  {
   "t": 8.7,
   "kind": "resolve"
  },
  {
   "t": 10.0,
   "kind": "cut"
  },
  {
   "t": 11.6,
   "kind": "alarm",
   "sev": "medium"
  },
  {
   "t": 13.7,
   "kind": "resolve"
  },
  {
   "t": 15.0,
   "kind": "cut"
  },
  {
   "t": 16.6,
   "kind": "alarm",
   "sev": "medium"
  },
  {
   "t": 18.7,
   "kind": "resolve"
  },
  {
   "t": 20.0,
   "kind": "cut"
  },
  {
   "t": 21.6,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 23.7,
   "kind": "resolve"
  },
  {
   "t": 25.0,
   "kind": "cut"
  },
  {
   "t": 26.6,
   "kind": "alarm",
   "sev": "high"
  },
  {
   "t": 28.7,
   "kind": "resolve"
  },
  {
   "t": 30.0,
   "kind": "cut"
  },
  {
   "t": 31.6,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 33.7,
   "kind": "resolve"
  },
  {
   "t": 35.0,
   "kind": "cut"
  },
  {
   "t": 36.6,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 38.7,
   "kind": "resolve"
  },
  {
   "t": 40.0,
   "kind": "cut"
  },
  {
   "t": 41.6,
   "kind": "alarm",
   "sev": "medium"
  },
  {
   "t": 43.7,
   "kind": "resolve"
  },
  {
   "t": 45.0,
   "kind": "cut"
  },
  {
   "t": 46.6,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 48.7,
   "kind": "resolve"
  },
  {
   "t": 50.0,
   "kind": "cut"
  },
  {
   "t": 51.6,
   "kind": "alarm",
   "sev": "high"
  },
  {
   "t": 53.7,
   "kind": "resolve"
  },
  {
   "t": 55.0,
   "kind": "summary"
  },
  {
   "t": 62.0,
   "kind": "close"
  }
 ]
};
