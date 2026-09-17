# ---------- Подгружает тестовые настройки ДО импорта любого app.* модуля ----------
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.test", override=True)

# app/main.py при импорте вызывает setup_logging(), которая пишет в /srv/logs -
# путь существует только внутри docker-контейнера. Для тестов с хоста
# логирование в файл не нужно вовсе - подменяем на no-op до первого импорта app.*.
_patcher = patch("app.config.logging_setup.setup_logging", lambda level: None)
_patcher.start()