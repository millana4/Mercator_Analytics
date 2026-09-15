# ---------- Логирование: stdout + файл с ротацией ----------
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("/srv/logs")
LOG_FILE = LOG_DIR / "analytics.log"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)

    # 10 файлов по 20 МБ - хватит, чтобы разобрать историю расчётов за месяцы
    rotating = RotatingFileHandler(LOG_FILE, maxBytes=20 * 1024 * 1024,
                                   backupCount=10, encoding="utf-8")
    rotating.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(stream)
    root.addHandler(rotating)