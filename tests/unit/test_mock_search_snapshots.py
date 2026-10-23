# ---------- MockConnector: плитки поисковой выдачи из выгрузки ch_search_*.csv ----------
import pandas as pd

from app.connectors import schema
from app.connectors.mock import MockConnector

SET_KIDS = "468fa94d-be02-4d05-93d9-2407b40abaab"
SET_OTHER = "11111111-1111-1111-1111-111111111111"

# формат выгрузки ClickHouse: все поля в кавычках, NULL - \N
CSV = (
    '"run_at","parsed_at","category","set_id","stratum_id","query","position","sku","price"\n'
    f'"2026-10-21 18:37:00","2026-10-21 19:40:00","kids_clothing","{SET_KIDS}","s1","Майка",1,"0012345",\\N\n'
    f'"2026-10-22 18:37:00","2026-10-22 19:41:00","kids_clothing","{SET_KIDS}","s1","Майка",1,"0012345",990\n'
    f'"2026-10-22 18:37:00","2026-10-22 19:42:00","clothing","{SET_OTHER}","s9","Майка",1,"555",500\n'
)


def test_reads_latest_dump_filters_set_and_types(tmp_path):
    (tmp_path / "ch_search_20261001_0000.csv").write_text(CSV.replace("0012345", "999"),
                                                          encoding="utf-8")
    (tmp_path / "ch_search_20261023_1048.csv").write_text(CSV, encoding="utf-8")
    s = MockConnector(tmp_path).get_search_snapshots(SET_KIDS)
    assert list(s.columns) == list(schema.SEARCH_SNAPSHOTS)
    assert len(s) == 2
    assert s["sku"].tolist() == ["0012345", "0012345"]      # свежий файл, ведущие нули целы
    assert s["run_at"].dtype == "datetime64[ns]"
    assert s["stratum_id"].dtype == "string"


def test_date_from(tmp_path):
    (tmp_path / "ch_search_20261023_1048.csv").write_text(CSV, encoding="utf-8")
    s = MockConnector(tmp_path).get_search_snapshots(SET_KIDS,
                                                     date_from=pd.Timestamp("2026-10-22"))
    assert s["run_at"].tolist() == [pd.Timestamp("2026-10-22 18:37")]


def test_no_dump_empty(tmp_path):
    s = MockConnector(tmp_path).get_search_snapshots(SET_KIDS)
    assert s.empty
    assert list(s.columns) == list(schema.SEARCH_SNAPSHOTS)