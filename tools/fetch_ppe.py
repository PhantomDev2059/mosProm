"""Докачка изображений jhboyo/ppe-dataset: только недостающие, параллельно, с повтором при ограничении частоты.

Разметка берётся из git-клона без LFS (одним запросом), изображения — прямыми ссылками resolve/main.
Готовые изображения из прошлой загрузки переиспользуются. Итог — папка в формате YOLO, готовая к обучению.
"""
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "https://huggingface.co/datasets/jhboyo/ppe-dataset/resolve/main"
GIT = Path("/workspace/linq/train/ppe-git")            # разметка + LFS-указатели
OLD = Path("/workspace/linq/train/ppe-dataset")        # то, что успела скачать прошлая загрузка
OUT = Path("/workspace/linq/train/ppe")                # итоговый набор


def is_pointer(p: Path) -> bool:
    with p.open("rb") as f:
        return f.read(24).startswith(b"version https://git-lfs")


def fetch(rel: str) -> bool:
    dst = OUT / rel
    if dst.exists() and dst.stat().st_size > 1000:
        return True
    old = OLD / rel
    if old.exists() and old.stat().st_size > 1000 and not is_pointer(old):
        shutil.copyfile(old, dst)
        return True
    for attempt in range(8):
        try:
            with urllib.request.urlopen(f"{REPO}/{rel}", timeout=60) as r:
                data = r.read()
            dst.write_bytes(data)
            return True
        except urllib.error.HTTPError as e:
            time.sleep(min(60, 2 ** attempt) if e.code in (429, 500, 502, 503) else 1)
        except Exception:
            time.sleep(2 ** attempt)
    return False


def main() -> None:
    todo = []
    for split in ("train", "val", "test"):
        (OUT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT / split / "labels").mkdir(parents=True, exist_ok=True)
        for lbl in (GIT / split / "labels").glob("*.txt"):
            shutil.copyfile(lbl, OUT / split / "labels" / lbl.name)
        todo += [f"{split}/images/{p.name}" for p in (GIT / split / "images").iterdir()]
    done = failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 8) as pool:
        for ok in pool.map(fetch, todo):
            done += ok
            failed += not ok
            if (done + failed) % 500 == 0:
                print(f"{done + failed}/{len(todo)} готово, ошибок {failed}, {time.time() - t0:.0f} с", flush=True)
    print(f"ИТОГО {done}/{len(todo)}, ошибок {failed}, {time.time() - t0:.0f} с", flush=True)


if __name__ == "__main__":
    main()
