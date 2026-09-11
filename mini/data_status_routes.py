from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Header, HTTPException, Query
from mini.deps import current_store, get_database
from services.completeness_service import CompletenessService, DataStatus

router=APIRouter()

def _day(store:dict,requested:str|None)->str:
    if requested:
        try: return date.fromisoformat(requested).isoformat()
        except ValueError as exc: raise HTTPException(400,"日期格式应为 YYYY-MM-DD") from exc
    try: return datetime.now(ZoneInfo(store.get('timezone') or 'Asia/Shanghai')).date().isoformat()
    except Exception: return date.today().isoformat()

def _out(s:DataStatus)->dict:
    return {'business_date':s.business_date,'expected_sources':list(s.expected_sources),'received_sources':list(s.received_sources),
            'missing_sources':list(s.missing_sources),'expected_source_count':len(s.expected_sources),'received_source_count':len(s.received_sources),
            'user_declared_complete':s.user_declared_complete,'completeness_level':s.completeness_level,'as_of_time':s.as_of_time,
            'calculation_version':s.calculation_version}

@router.get('/data-status/today')
def get_data_status(business_date:str|None=Query(default=None),authorization:str|None=Header(default=None)):
    store=current_store(authorization); day=_day(store,business_date)
    return {'data_status':_out(CompletenessService(get_database()).get_status(int(store['id']),day))}

@router.post('/data-status/today/complete')
def declare_data_complete(business_date:str|None=Query(default=None),authorization:str|None=Header(default=None)):
    store=current_store(authorization); day=_day(store,business_date)
    return {'data_status':_out(CompletenessService(get_database()).declare_complete(int(store['id']),day))}
