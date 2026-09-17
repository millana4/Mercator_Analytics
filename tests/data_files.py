# ---------- Поиск свежего дампа ядра по маске имени файла ----------
# Имена дампов содержат дату (pg_YYYYMMDD_HHMM.sql), поэтому "последний
# по сортировке имени" = самый свежий - тот же приём, что и в MockConnector._latest().
# Меняешь дампы в data/backups - тесты сами подхватят новый файл, ничего
# в тестах менять не нужно.
from pathlib import Path

PG_DUMP_GLOB = "pg_*.sql"


def find_latest_pg_dump(data_dir: Path) -> Path:
    files = sorted(data_dir.glob(PG_DUMP_GLOB))
    if not files:
        raise FileNotFoundError(f"В {data_dir} нет файлов по маске {PG_DUMP_GLOB}")
    return files[-1]