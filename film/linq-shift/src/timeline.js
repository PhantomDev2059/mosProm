// сгенерировано shift_data.py
FILM.TIMELINE = {
 "title": "LinQ Guard — смена",
 "bpm": 120,
 "fps": 24,
 "width": 1920,
 "height": 1080,
 "safeBottom": 1080,
 "duration": 73.0,
 "shots": [
  {
   "id": "open",
   "file": "01-open.js",
   "start": 0,
   "end": 6,
   "mode": "schematic",
   "title": "Смена 24.10",
   "post": 0.3
  },
  {
   "id": "v01-ppe",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Нет каски и жилета",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Нет каски и жилета: ppe 1.20+6.0",
   "start": 6.0,
   "end": 12.0,
   "post": 0.3
  },
  {
   "id": "v02-phone",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Телефон в рабочей зоне",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Телефон в рабочей зоне: phone 1.20+6.0",
   "start": 12.0,
   "end": 18.0,
   "post": 0.3
  },
  {
   "id": "v03-proximity",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Пешеход у погрузчика",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Пешеход у погрузчика: proximity 1.00+6.0",
   "start": 18.0,
   "end": 24.0,
   "post": 0.3
  },
  {
   "id": "v04-smoking",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Курение на территории",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Курение на территории: smoking 7.40+6.0",
   "start": 24.0,
   "end": 30.0,
   "post": 0.3
  },
  {
   "id": "v05-fall",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Падение человека",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Падение человека: fall 0.20+6.0",
   "start": 30.0,
   "end": 36.0,
   "post": 0.3
  },
  {
   "id": "v06-immobile",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Лежит без движения",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Лежит без движения: fall 10.60+6.0",
   "start": 36.0,
   "end": 42.0,
   "post": 0.3
  },
  {
   "id": "v07-running",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Бег по цеху",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 140,
    "fps": 25
   },
   "brief": "Бег по цеху: running 0.40+6.0",
   "start": 42.0,
   "end": 48.0,
   "post": 0.3
  },
  {
   "id": "v08-fire",
   "file": "10-violations.js",
   "mode": "schematic",
   "title": "Открытый огонь",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 158,
    "fps": 25
   },
   "brief": "Открытый огонь: fire 0.80+6.0",
   "start": 48.0,
   "end": 54.0,
   "post": 0.3
  },
  {
   "id": "v09-smoke",
   "file": "10-violations.js",
   "mode": "illustrated",
   "title": "Задымление",
   "transitionIn": {
    "kind": "fade",
    "dur": 0.25
   },
   "video": {
    "n": 55,
    "fps": 25
   },
   "brief": "Задымление: smoke 6.80+6.0",
   "start": 54.0,
   "end": 60.0,
   "post": 0.3
  },
  {
   "id": "summary",
   "file": "90-summary.js",
   "start": 60.0,
   "end": 68.0,
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
   "start": 68.0,
   "end": 73.0,
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
   "t": 6.0,
   "kind": "cut"
  },
  {
   "t": 8.0,
   "kind": "alarm",
   "sev": "high"
  },
  {
   "t": 10.7,
   "kind": "resolve"
  },
  {
   "t": 12.0,
   "kind": "cut"
  },
  {
   "t": 14.0,
   "kind": "alarm",
   "sev": "medium"
  },
  {
   "t": 16.7,
   "kind": "resolve"
  },
  {
   "t": 18.0,
   "kind": "cut"
  },
  {
   "t": 20.0,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 22.7,
   "kind": "resolve"
  },
  {
   "t": 24.0,
   "kind": "cut"
  },
  {
   "t": 26.0,
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
   "t": 32.0,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 34.7,
   "kind": "resolve"
  },
  {
   "t": 36.0,
   "kind": "cut"
  },
  {
   "t": 38.0,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 40.7,
   "kind": "resolve"
  },
  {
   "t": 42.0,
   "kind": "cut"
  },
  {
   "t": 44.0,
   "kind": "alarm",
   "sev": "medium"
  },
  {
   "t": 46.7,
   "kind": "resolve"
  },
  {
   "t": 48.0,
   "kind": "cut"
  },
  {
   "t": 50.0,
   "kind": "alarm",
   "sev": "critical"
  },
  {
   "t": 52.7,
   "kind": "resolve"
  },
  {
   "t": 54.0,
   "kind": "cut"
  },
  {
   "t": 56.0,
   "kind": "alarm",
   "sev": "high"
  },
  {
   "t": 58.7,
   "kind": "resolve"
  },
  {
   "t": 60.0,
   "kind": "summary"
  },
  {
   "t": 68.0,
   "kind": "close"
  }
 ]
};
