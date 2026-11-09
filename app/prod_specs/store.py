# ---------- Рекомендации по закупкам: чтение и запись анализов страт ----------
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ProdSpecsAnalysis
from app.db.session import SessionLocal


def get_analysis(session: Session, stratum_id: str) -> ProdSpecsAnalysis | None:
    """Анализ страты (одна запись на страту)."""
    return session.scalar(select(ProdSpecsAnalysis).where(
        ProdSpecsAnalysis.stratum_id == UUID(str(stratum_id))))


def list_analyses(session: Session, *, category: str, geo: str, marketplace: str,
                  set_ids: list[str] | None) -> list[ProdSpecsAnalysis]:
    """Анализы связки по подписи страты. set_ids - только анализы этих сетов (активный сет
    связки); None - ядро недоступно, фильтра нет; пустой список - активного сета нет."""
    if set_ids is not None and not set_ids:
        return []
    query = select(ProdSpecsAnalysis).where(
        ProdSpecsAnalysis.category == category, ProdSpecsAnalysis.geo == geo,
        ProdSpecsAnalysis.marketplace == marketplace)
    if set_ids is not None:
        query = query.where(ProdSpecsAnalysis.source_set_id.in_([UUID(str(s)) for s in set_ids]))
    return list(session.scalars(query.order_by(ProdSpecsAnalysis.stratum_label)))


def find_source(session: Session, selection_hash: str) -> ProdSpecsAnalysis | None:
    """Самый свежий анализ с таким составом отобранных карточек - в любой страте и любом сете.
    Из него копируется результат, если состав не изменился."""
    return session.scalar(select(ProdSpecsAnalysis).where(
        ProdSpecsAnalysis.selection_hash == selection_hash
    ).order_by(ProdSpecsAnalysis.analyzed_at.desc()).limit(1))


def save_analysis(session: Session, record: dict) -> ProdSpecsAnalysis:
    """Записывает анализ страты: новый заменяет прежний (одна запись на страту).
    record: set_id, marketplace, category, geo, stratum_id, stratum_label, selection_hash,
    skus, n_cards, model, result, copied_from_stratum."""
    stratum_id = UUID(str(record["stratum_id"]))
    row = session.scalar(select(ProdSpecsAnalysis).where(ProdSpecsAnalysis.stratum_id == stratum_id))
    if row is None:
        row = ProdSpecsAnalysis(stratum_id=stratum_id)
        session.add(row)
    row.source_set_id = UUID(str(record["set_id"]))
    row.marketplace = str(record["marketplace"])
    row.category = str(record["category"])
    row.geo = str(record["geo"])
    row.stratum_label = str(record["stratum_label"])[:512]
    row.selection_hash = record["selection_hash"]
    row.skus = list(record["skus"])
    row.n_cards = int(record["n_cards"])
    row.model = record.get("model")
    row.result = record["result"]
    copied = record.get("copied_from_stratum")
    row.copied_from_stratum = UUID(str(copied)) if copied else None
    row.analyzed_at = datetime.now()
    session.flush()
    return row


def set_label(session: Session, stratum_id: str, label: str) -> bool:
    """Меняет подпись анализа страты; анализа нет или подпись та же - False."""
    row = get_analysis(session, stratum_id)
    label = label[:512]
    if row is None or row.stratum_label == label:
        return False
    row.stratum_label = label
    session.flush()
    return True


def delete_not_in_sets(session: Session, set_ids: list[str]) -> int:
    """Удаляет анализы сетов, которых нет в списке (сет выведен или удалён).
    Пустой список - ничего не удаляется: активные сеты неизвестны."""
    if not set_ids:
        return 0
    result = session.execute(delete(ProdSpecsAnalysis).where(
        ProdSpecsAnalysis.source_set_id.not_in([UUID(str(s)) for s in set_ids])))
    session.flush()
    return result.rowcount or 0


class SqlStore:
    """Хранилище для запуска: каждая операция - своя сессия и фиксация."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._factory = session_factory

    @staticmethod
    def _analysis(row: ProdSpecsAnalysis | None):
        from app.prod_specs.runner import Analysis
        if row is None:
            return None
        return Analysis(stratum_id=str(row.stratum_id), selection_hash=row.selection_hash,
                        prompt_version=(row.result or {}).get("prompt_version"),
                        model=row.model, result=row.result, analyzed_at=row.analyzed_at)

    def get(self, stratum_id: str):
        with self._factory() as session:
            return self._analysis(get_analysis(session, stratum_id))

    def find_source(self, selection_hash: str):
        with self._factory() as session:
            return self._analysis(find_source(session, selection_hash))

    def save(self, record: dict) -> None:
        with self._factory() as session:
            save_analysis(session, record)
            session.commit()

    def set_label(self, stratum_id: str, label: str) -> bool:
        with self._factory() as session:
            changed = set_label(session, stratum_id, label)
            session.commit()
            return changed

    def delete_not_in_sets(self, set_ids: list[str]) -> int:
        with self._factory() as session:
            removed = delete_not_in_sets(session, set_ids)
            session.commit()
            return removed