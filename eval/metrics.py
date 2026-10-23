"""Метрики качества по событиям во времени.

Разметка (ground truth) и предсказания — интервалы [start, end] в секундах записи с типом нарушения.

1. Precision / Recall / F1 по событиям. Нарушение из разметки найдено, если с ним пересекается хотя бы одно
   событие системы того же типа (с допуском tol секунд). Событие системы ложное, если не пересекается ни с одним
   нарушением своего типа.
2. Взвешенный по тяжести recall (WR): каждое нарушение весит по своему уровню опасности, пропуск критичного
   стоит в разы дороже пропуска среднего.
3. Взвешенное покрытие времени нарушений (WTC): доля времени каждого нарушения, когда тревога уже была поднята
   (от момента подтверждения до конца нарушения), с теми же весами. Поздняя тревога даёт меньшее покрытие.
4. Ложные тревоги в час записи.
5. Время до тревоги: от начала нарушения до подтверждения события (медиана по найденным).
6. Итоговый балл S = WR·WTC-гармоника минус штраф за ложные: S = F1(WR, WTC) · exp(−fa_per_hour / fa_scale).
"""
from dataclasses import dataclass, field
import math

SEVERITY_WEIGHT = {"critical": 10.0, "high": 3.0, "medium": 1.0}


@dataclass
class Interval:
    video: str
    type: str
    start: float
    end: float
    severity: str = "high"
    score: float = 1.0
    meta: dict = field(default_factory=dict)


def _overlap(a: Interval, b: Interval, tol: float) -> float:
    return min(a.end + tol, b.end + tol) - max(a.start - tol, b.start - tol)


def evaluate(gt: list[Interval], pred: list[Interval], hours: float, tol: float = 1.0,
             same_incident: dict[str, str] | None = None, fa_scale: float = 2.0) -> dict:
    """same_incident: какие типы считаются одним инцидентом (например, immobile → fall)."""
    alias = same_incident or {}
    kind = lambda t: alias.get(t, t)                                        # noqa: E731
    types = sorted({kind(g.type) for g in gt} | {kind(p.type) for p in pred})
    per_type, latencies = {}, []
    w_found = w_total = cov_w = 0.0
    fp_total = 0
    for t in types:
        g_t = [g for g in gt if kind(g.type) == t]
        p_t = [p for p in pred if kind(p.type) == t]
        tp = 0
        for g in g_t:
            hits = [p for p in p_t if p.video == g.video and _overlap(g, p, tol) > 0]
            w = SEVERITY_WEIGHT.get(g.severity, 1.0)
            w_total += w
            if hits:
                tp += 1
                w_found += w
                alarm = min(p.start for p in hits)
                latencies.append(max(0.0, alarm - g.start))
                covered = max(0.0, g.end - max(alarm, g.start))
                cov_w += w * (covered / max(g.end - g.start, 1e-6))
        fp = sum(1 for p in p_t if not any(p.video == g.video and _overlap(g, p, tol) > 0 for g in g_t))
        fp_total += fp
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / len(g_t) if g_t else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if g_t else None)
        per_type[t] = {"gt": len(g_t), "pred": len(p_t), "tp": tp, "fp": fp, "fn": len(g_t) - tp,
                       "precision": _r(prec), "recall": _r(rec), "f1": _r(f1)}
    wr = w_found / w_total if w_total else None
    wtc = cov_w / w_total if w_total else None
    fa_h = fp_total / hours if hours > 0 else None
    score = None
    if wr is not None and wtc is not None and fa_h is not None:
        hm = 2 * wr * wtc / (wr + wtc) if wr + wtc else 0.0
        score = hm * math.exp(-fa_h / fa_scale)
    tps = sum(v["tp"] for v in per_type.values())
    preds = sum(v["pred"] for v in per_type.values())
    gts = sum(v["gt"] for v in per_type.values())
    micro_p = tps / preds if preds else None
    micro_r = tps / gts if gts else None
    latencies.sort()
    return {
        "per_type": per_type,
        "micro": {"precision": _r(micro_p), "recall": _r(micro_r),
                  "f1": _r(2 * micro_p * micro_r / (micro_p + micro_r)) if micro_p and micro_r else None},
        "weighted_recall": _r(wr), "weighted_time_coverage": _r(wtc),
        "false_alarms_per_hour": _r(fa_h), "latency_median_s": _r(latencies[len(latencies) // 2]) if latencies else None,
        "score": _r(score), "hours": _r(hours),
    }


def _r(v):
    return None if v is None else round(float(v), 3)
