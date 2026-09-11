from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from persistence.database import DatabaseTarget, coerce_database
from repositories.data_status_repository import DataStatusRepository

CALCULATION_VERSION = "COMPLETENESS_V1"

@dataclass(frozen=True)
class DataStatus:
    store_id: int
    business_date: str
    expected_sources: tuple[str, ...]
    received_sources: tuple[str, ...]
    missing_sources: tuple[str, ...]
    user_declared_complete: bool
    completeness_level: str
    as_of_time: int
    calculation_version: str = CALCULATION_VERSION

class CompletenessService:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path
        self.repo = DataStatusRepository(self.db)

    def _connect(self):
        return self.db.compat_connect()

    def _operating_dates(self, store_id:int, before_date:str, limit:int=30)->list[str]:
        with self._connect() as con:
            rows=con.execute("""SELECT business_date FROM (
                    SELECT DISTINCT business_date FROM daily_channel_sales WHERE store_id=? AND business_date<?
                    UNION SELECT DISTINCT business_date FROM daily_store_sales_totals WHERE store_id=? AND business_date<?
                ) ORDER BY business_date DESC LIMIT ?""",(store_id,before_date,store_id,before_date,limit)).fetchall()
        return [str(r["business_date"]) for r in rows]

    def _sources_for_dates(self, store_id:int, dates:list[str])->dict[str,set[str]]:
        result={d:set() for d in dates}
        if not dates: return result
        ph=','.join('?' for _ in dates)
        with self._connect() as con:
            rows=con.execute(f"""SELECT s.business_date,c.code FROM daily_channel_sales s JOIN channels c ON c.id=s.channel_id
                                  WHERE s.store_id=? AND s.business_date IN ({ph})""",[store_id,*dates]).fetchall()
            totals=con.execute(f"SELECT business_date FROM daily_store_sales_totals WHERE store_id=? AND business_date IN ({ph})",[store_id,*dates]).fetchall()
        for row in rows: result.setdefault(row["business_date"],set()).add(row["code"])
        for row in totals: result.setdefault(row["business_date"],set()).add('WHOLE_STORE')
        return result

    def _received(self, store_id:int, business_date:str)->set[str]:
        return self._sources_for_dates(store_id,[business_date]).get(business_date,set())

    def _expected(self, store_id:int, business_date:str)->set[str]:
        dates30=self._operating_dates(store_id,business_date,30)
        by_day=self._sources_for_dates(store_id,dates30)
        last7=dates30[:7]
        count30=Counter(src for d in dates30 for src in by_day.get(d,set()))
        count7=Counter(src for d in last7 for src in by_day.get(d,set()))
        expected={src for src in set(count30)|set(count7) if count7[src]>=5 or count30[src]>=12}
        # Store learned source metadata for inspection/UI; counts are derived from facts, not guessed.
        now=int(time.time()*1000)
        all_sources=set(count30)
        for src in all_sources:
            channel=None if src=='WHOLE_STORE' else src
            self.repo.upsert_source(store_id=store_id,channel_code=channel,data_type='SALES',report_scope='WHOLE_STORE' if src=='WHOLE_STORE' else 'CHANNEL',
                                    seen_at=now,appearance_days=count30[src],recent_30d_days=count30[src],is_expected=src in expected)
        return expected

    def get_status(self, store_id:int, business_date:str)->DataStatus:
        persisted=self.repo.get_daily_status(store_id,business_date)
        declared=bool(persisted and persisted.get('user_declared_complete'))
        expected=self._expected(store_id,business_date); received=self._received(store_id,business_date)
        missing=expected-received
        if declared: level='DECLARED_COMPLETE'
        elif not received: level='NO_DATA'
        elif expected and not missing: level='COMPLETE'
        else: level='PARTIAL'
        now=int(time.time()*1000)
        self.repo.upsert_daily_status(store_id=store_id,business_date=business_date,expected_source_count=len(expected),received_source_count=len(received),
                                      user_declared_complete=declared,completeness_level=level,as_of_time=now,calculation_version=CALCULATION_VERSION)
        return DataStatus(store_id,business_date,tuple(sorted(expected)),tuple(sorted(received)),tuple(sorted(missing)),declared,level,now)

    def declare_complete(self, store_id:int, business_date:str)->DataStatus:
        status=self.get_status(store_id,business_date)
        now=int(time.time()*1000)
        self.repo.upsert_daily_status(store_id=store_id,business_date=business_date,expected_source_count=len(status.expected_sources),received_source_count=len(status.received_sources),
                                      user_declared_complete=True,completeness_level='DECLARED_COMPLETE',as_of_time=now,calculation_version=CALCULATION_VERSION)
        return self.get_status(store_id,business_date)
