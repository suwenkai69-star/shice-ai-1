from __future__ import annotations
import csv, io, json, sqlite3, time, pathlib
from typing import Any
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mini.routes import router as mini_router

from persistence.database import Database, build_runtime_database
from persistence.migrations import assert_cloud_schema
from persistence.seed import seed_system_data

from db import migrate_database
from services.benchmark_service import BenchmarkService
from recognition.service import recover_interrupted_jobs

from data_foundation import (
    export_full_backup, get_reconciliation, import_daily_sales, import_payments, import_settlements,
    list_channels, list_daily_sales, list_import_batches, list_payments, list_settlements,
    reset_store_data, restore_full_backup,
)

BASE = pathlib.Path(__file__).resolve().parent
DB_PATH = BASE / 'data' / 'shice_ai.db'
STATIC = BASE / 'static'


def _production_environment() -> bool:
    import os
    return os.getenv("SHICE_ENV", "local").strip().lower() == "production" or os.getenv("VERCEL", "").strip() == "1"


def production_route_allowed(path: str) -> bool:
    return path == "/api/health" or path.startswith("/api/mini/v1/") or path == "/api/mini/v1"

DEFAULT_SETTINGS = {
    'storeName':'永民手作','storeType':'奶茶店','period':'2026年6月',
    'foodTargetRate':24.33,'personnelWarn':25,'deliveryWarn':30,
    'localOnly':False,'confirmHighRisk':True
}
EMPTY_STATE = {
    'business':None,'validationBusiness':None,'products':[],'platform':[],'workforce':[],
    'marketingHistory':[],'chatHistory':[],
    'actions':{'platform':'pending','workforce':'pending','food':'hold','product':'pending'},
    'settings':DEFAULT_SETTINGS.copy(),'selectedProduct':None,'lastUpdated':{}
}
DEMO_BUSINESS={'revenue':77194.43,'cups':6593,'ingredientCost':18403.16,'personnelCost':21074.08,'cashProfit':-7025.0,'theoreticalFoodRate':24.33,'deliveryReductionRate':37.95,'source':'服务端演示数据'}
DEMO_PRODUCTS=[
 {'name':'珍珠奶茶','qty':1200,'paidPrice':12.00,'unitCost':2.61},{'name':'柠檬茶','qty':1050,'paidPrice':10.00,'unitCost':1.80},{'name':'杨枝甘露','qty':850,'paidPrice':14.00,'unitCost':4.32},{'name':'芋泥波波','qty':780,'paidPrice':13.00,'unitCost':3.78},{'name':'纯茶','qty':730,'paidPrice':8.00,'unitCost':1.17},{'name':'生椰拿铁','qty':690,'paidPrice':14.00,'unitCost':3.24},{'name':'葡萄冰茶','qty':650,'paidPrice':12.00,'unitCost':3.06},{'name':'奶盖茶','qty':643,'paidPrice':10.82,'unitCost':2.61}
]
DEMO_PLATFORM=[
 {'category':'技术服务/佣金','amount':12000},{'category':'配送相关','amount':7000},{'category':'商家承担优惠','amount':6200},{'category':'推广费用','amount':2500},{'category':'退款及其他','amount':1595.30}
]
DEMO_WORKFORCE=[
 {'hour':'10:00','orders':220,'staffHours':90},{'hour':'11:00','orders':380,'staffHours':120},{'hour':'12:00','orders':680,'staffHours':150},{'hour':'13:00','orders':720,'staffHours':150},{'hour':'14:00','orders':390,'staffHours':120},{'hour':'15:00','orders':280,'staffHours':120},{'hour':'16:00','orders':300,'staffHours':120},{'hour':'17:00','orders':420,'staffHours':120},{'hour':'18:00','orders':700,'staffHours':150},{'hour':'19:00','orders':850,'staffHours':150},{'hour':'20:00','orders':780,'staffHours':150},{'hour':'21:00','orders':590,'staffHours':120},{'hour':'22:00','orders':283,'staffHours':90}
]

APP_VERSION = '0.5.0-R1+MiniV1'
app = FastAPI(title='食策AI Mini V1 API', version=APP_VERSION)
app.include_router(mini_router, prefix='/api/mini/v1')


@app.middleware("http")
async def production_route_guard(request: Request, call_next):
    runtime_db = getattr(app.state, "database", None)
    is_cloud = getattr(runtime_db, "backend", None) == "postgresql" or _production_environment()
    if is_cloud and not production_route_allowed(request.url.path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)

class StatePayload(BaseModel):
    state: dict[str, Any]

class AskPayload(BaseModel):
    question: str

class MarketingPayload(BaseModel):
    product: str
    goal: str
    channel: str


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_sqlite_local(db_path: pathlib.Path) -> None:
    db_path = pathlib.Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS stores (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS app_state (store_id INTEGER PRIMARY KEY, json TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, target TEXT NOT NULL, filename TEXT NOT NULL, row_count INTEGER NOT NULL, created_at INTEGER NOT NULL)')
        c.execute('INSERT OR IGNORE INTO users(id,name,role) VALUES(1,?,?)', ('演示用户','owner'))
        c.execute('INSERT OR IGNORE INTO stores(id,user_id,name,type) VALUES(1,1,?,?)', ('永民手作','奶茶店'))
        row = c.execute('SELECT 1 FROM app_state WHERE store_id=1').fetchone()
        if not row:
            c.execute('INSERT INTO app_state(store_id,json,updated_at) VALUES(1,?,?)', (json.dumps(EMPTY_STATE, ensure_ascii=False), int(time.time()*1000)))
        c.commit()
    migrate_database(db_path)
    seed_path = BASE / "data" / "benchmarks" / "benchmark_seed.json"
    if seed_path.exists():
        BenchmarkService(db_path).load_seed_file(seed_path)
    recover_interrupted_jobs(db_path)


def init_postgres_production(db: Database) -> None:
    assert_cloud_schema(db)
    seed_path = BASE / "data" / "benchmarks" / "benchmark_seed.json"
    if seed_path.exists():
        seed_system_data(db, seed_path)


def init_runtime() -> Database:
    db = build_runtime_database(DB_PATH)
    if db.backend == "postgresql":
        init_postgres_production(db)
    else:
        init_sqlite_local(db.sqlite_path or DB_PATH)
    app.state.database = db
    return db


def init_db():
    """Backward-compatible local/test initializer."""
    init_sqlite_local(DB_PATH)
    app.state.database = Database.sqlite(DB_PATH)

def get_state() -> dict[str, Any]:
    with conn() as c:
        row = c.execute('SELECT json FROM app_state WHERE store_id=1').fetchone()
    try:
        s = json.loads(row['json']) if row else {}
    except Exception:
        s = {}
    out = json.loads(json.dumps(EMPTY_STATE, ensure_ascii=False))
    out.update(s)
    out['settings'] = {**DEFAULT_SETTINGS, **(s.get('settings') or {})}
    out['actions'] = {**EMPTY_STATE['actions'], **(s.get('actions') or {})}
    return out

def save_state(state: dict[str, Any]):
    state = {**EMPTY_STATE, **state}
    state['settings'] = {**DEFAULT_SETTINGS, **(state.get('settings') or {})}
    state['actions'] = {**EMPTY_STATE['actions'], **(state.get('actions') or {})}
    ts = int(time.time()*1000)
    with conn() as c:
        c.execute('INSERT INTO app_state(store_id,json,updated_at) VALUES(1,?,?) ON CONFLICT(store_id) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at', (json.dumps(state, ensure_ascii=False), ts))
        c.execute(
            'UPDATE stores SET name=?, type=?, store_type=?, updated_at=? WHERE id=1',
            (
                state['settings'].get('storeName','未命名门店'),
                state['settings'].get('storeType','餐饮门店'),
                state['settings'].get('storeType','餐饮门店'),
                ts,
            ),
        )
        c.commit()
    return state

def num(v):
    if v is None: return None
    s = str(v).replace('¥','').replace('￥','').replace('%','').replace(',','').strip()
    try: return float(s)
    except: return None

def pick(row, names):
    for n in names:
        if n in row and str(row[n]).strip() != '': return row[n]
    return None

def decode_text_bytes(data: bytes):
    for encoding in ('utf-8-sig','gb18030'):
        try: return data.decode(encoding)
        except UnicodeDecodeError: pass
    raise ValueError('文件编码无法识别，请使用UTF-8或GB18030编码')

def parse_csv_bytes(data: bytes):
    text = decode_text_bytes(data)
    return list(csv.DictReader(io.StringIO(text)))

def normalize_business(rows, settings):
    r = rows[0] if rows else {}
    vals = {
        'revenue':num(pick(r,['销售收入','营业额','revenue','sales'])),
        'cups':num(pick(r,['销量','杯数','订单数','cups','orders'])),
        'ingredientCost':num(pick(r,['食材成本','原料成本','ingredientCost','food_cost'])),
        'personnelCost':num(pick(r,['人员成本','人工成本','personnelCost','labor_cost'])),
        'cashProfit':num(pick(r,['现金利润','利润','cashProfit','profit']))
    }
    if any(v is None for v in vals.values()):
        raise ValueError('经营数据缺少必要字段：销售收入、销量、食材成本、人员成本、现金利润')
    vals['theoreticalFoodRate'] = num(pick(r,['理论食材成本率','理论成本率','theoreticalFoodRate'])) or settings.get('foodTargetRate',24.33)
    vals['deliveryReductionRate'] = num(pick(r,['到家端收入减少比例','到家综合收入减少','deliveryReductionRate'])) or 0
    vals['source']='服务端上传'
    return vals

def normalize_products(rows):
    out=[]
    for r in rows:
        x={'name':str(pick(r,['商品','商品名','产品','name']) or '').strip(), 'qty':num(pick(r,['销量','数量','qty','quantity'])), 'paidPrice':num(pick(r,['平均实付价','实付价','售价','price','paidPrice'])), 'unitCost':num(pick(r,['单位成本','单份成本','成本','unitCost','cost']))}
        if x['name'] and all(x[k] is not None for k in ('qty','paidPrice','unitCost')): out.append(x)
    if not out: raise ValueError('商品CSV需要：商品、销量、平均实付价、单位成本')
    return out

def normalize_platform(rows):
    out=[]
    for r in rows:
        x={'category':str(pick(r,['费用类别','类别','项目','category']) or '').strip(), 'amount':num(pick(r,['金额','费用','amount']))}
        if x['category'] and x['amount'] is not None: out.append(x)
    if not out: raise ValueError('平台CSV需要：费用类别、金额')
    return out

def normalize_workforce(rows):
    out=[]
    for r in rows:
        x={'hour':str(pick(r,['小时','时段','hour']) or '').strip(), 'orders':num(pick(r,['订单数','订单','orders'])), 'staffHours':num(pick(r,['累计人时','人时','staffHours','labor_hours']))}
        if x['hour'] and x['orders'] is not None and x['staffHours'] and x['staffHours']>0: out.append(x)
    if not out: raise ValueError('排班CSV需要：小时、订单数、累计人时')
    return out

def pct(a,b): return (float(a)/float(b)*100) if b else 0

def product_stats(state):
    rows=[]
    for p in state.get('products',[]):
        rev=p['qty']*p['paidPrice']; cost=p['qty']*p['unitCost']; margin=((p['paidPrice']-p['unitCost'])/p['paidPrice']*100) if p['paidPrice'] else 0
        rows.append({**p,'revenue':rev,'cost':cost,'margin':margin,'contribution':rev-cost})
    if not rows: return None
    qs=sorted(x['qty'] for x in rows); ms=sorted(x['margin'] for x in rows)
    def median(a):
        n=len(a); return a[n//2] if n%2 else (a[n//2-1]+a[n//2])/2
    qm,mm=median(qs),median(ms)
    for r in rows:
        hq,hm=r['qty']>=qm,r['margin']>=mm
        r['quad']='core' if hq and hm else 'optimize' if hq else 'grow' if hm else 'review'
    return rows

def answer_question(state, q):
    b=state.get('business'); products=product_stats(state); platform=state.get('platform',[]); workforce=state.get('workforce',[])
    if not b: return '目前还没有经营数据。先上传经营数据后，我才能基于事实回答。'
    profit_rate=pct(b.get('cashProfit',0),b.get('revenue',0)); food_rate=pct(b.get('ingredientCost',0),b.get('revenue',0)); personnel=pct(b.get('personnelCost',0),b.get('revenue',0))
    if '为什么' in q or '亏' in q:
        parts=[f'当前现金利润率为 {profit_rate:.2f}%。']
        if food_rate <= b.get('theoreticalFoodRate',24.33): parts.append('食材实际耗用率未高于当前理论口径，因此暂不把食材成本列为首要异常项。')
        if not platform: parts.append('平台费用仍缺明细。')
        if not workforce: parts.append('人员成本仍缺小时订单与排班数据。')
        return ''.join(parts)
    if '优先' in q or '最应该' in q or q.startswith('先'):
        if not platform: return '优先补平台结算明细，因为当前只有到家端综合收入减少比例，无法定位具体构成。'
        if not workforce: return '平台费用已拆解，下一步补小时订单与排班数据，判断人员成本结构。'
        if not products: return '平台和排班已有数据，下一步补SKU销量与成本，判断商品结构。'
        return '四类核心数据已齐。当前优先看平台可控费用项与低人效时段，再结合高销量低毛利SKU制定动作。'
    if '涨价' in q or '价格' in q:
        if not products: return '现在不能判断是否涨价，因为缺少SKU售价、销量和单位成本。上传商品表后再判断。'
        opt=sorted([x for x in products if x['quad']=='optimize'],key=lambda x:x['qty'],reverse=True)
        if opt:
            x=opt[0]; return f'如果要测试价格调整，优先从“{x["name"]}”这类高销量但相对低毛利商品做小范围验证。当前毛利率约 {x["margin"]:.1f}%，不建议全店统一涨价。'
        return '当前商品结构里没有明显的“高销量低毛利”SKU，不建议仅因为整体亏损就全店涨价。'
    if any(k in q for k in ['人工','排班','人效']):
        if not workforce: return '不建议直接裁员。先上传小时订单与累计人时，判断低峰冗余和高峰承载。'
        arr=sorted([{**x,'eff':x['orders']/x['staffHours']} for x in workforce],key=lambda x:x['eff'])[:3]
        return '最低人效时段集中在 '+ '、'.join(x['hour'] for x in arr) +'，建议先做排班模拟，再用下一周期数据验证。'
    if any(k in q for k in ['商品','SKU','推广','主推']):
        if not products: return '请先上传商品销量与成本。'
        cand=sorted([x for x in products if x['quad']=='grow'],key=lambda x:x['margin'],reverse=True) or sorted(products,key=lambda x:x['contribution'],reverse=True)
        x=cand[0]; return f'可以优先测试“{x["name"]}”。它当前毛利率约 {x["margin"]:.1f}%，适合做小规模曝光测试，并用下一周期贡献利润验证。'
    count=sum([bool(b),bool(products),bool(platform),bool(workforce)])
    return f'我可以基于当前 {count}/4 类数据回答经营、商品、平台费用、排班和营销问题。对于没有导入的数据，我不会给出确定结论。'

def marketing_generate(state, product_name, goal, channel):
    rows=product_stats(state)
    if not rows: raise ValueError('请先导入商品数据')
    p=next((x for x in rows if x['name']==product_name), rows[0])
    t={'grow':'低销量高毛利','optimize':'高销量低毛利','core':'核心商品','review':'待复核商品'}[p['quad']]
    strategy=f'''商品：{p['name']}\n当前分类：{t}\n当前毛利率：{p['margin']:.1f}%\n目标：{goal}\n渠道：{channel}\n\n建议：先做7天小规模测试，不把“曝光增加”直接等同于“利润改善”。测试前记录销量、平均实付价和贡献利润；测试后用同口径复盘。'''
    copy=f'''【{p['name']}｜本周小测试】\n不是全店大促，只想看看大家是不是真的喜欢这一杯。\n本周会给它更多一点曝光，价格与活动以门店实际页面为准。\n\n#{p['name']} #奶茶 #门店新品测试'''
    visual=f'''画面主体：{p['name']}\n目标：{goal}\n建议构图：单品近景 + 真实门店环境，避免堆叠过多促销元素。\n主标题：{p['name']}｜本周重点测试\n副标题：先试口味，再看数据\n信息规则：不使用未经数据支持的极限词；价格如需出现，以实际活动页面为准。'''
    video=f'''15秒脚本｜{p['name']}\n0–3s：产品近景，字幕“这杯我们想重新看看它值不值得继续推”\n3–8s：展示制作过程，旁白“这周只做一个小测试：多给一点曝光，不先大降价。”\n8–12s：真实取杯场景，字幕“看销量，也看最后留下多少钱”\n12–15s：产品定格，字幕“{p['name']}｜测试中”'''
    return {'time':int(time.time()*1000),'product':p['name'],'goal':goal,'channel':channel,'strategy':strategy,'copy':copy,'visual':visual,'video':video}

@app.on_event('startup')
def startup(): init_runtime()

@app.get('/')
def root(): return FileResponse(STATIC/'index.html')

@app.get('/api/health')
def health():
    db = getattr(app.state, 'database', None)
    backend = getattr(db, 'backend', 'sqlite')
    return {
        'status': 'ok',
        'version': APP_VERSION,
        'schema_version': 3,
        'cloud_schema_version': 1 if backend == 'postgresql' else 0,
        'database_backend': backend,
        'engine': f'{backend}-data-foundation-v3',
        'mini_api_version': 'v1',
        'server_time': int(time.time()*1000),
    }

@app.get('/api/session')
def session():
    with conn() as c:
        u=c.execute('SELECT * FROM users WHERE id=1').fetchone(); s=c.execute('SELECT * FROM stores WHERE id=1').fetchone()
    return {'user':dict(u),'store':dict(s),'mode':'demo-account'}

@app.get('/api/state')
def api_get_state(): return {'state':get_state()}

@app.put('/api/state')
def api_put_state(payload: StatePayload): return {'state':save_state(payload.state)}

@app.post('/api/reset')
def api_reset():
    s=json.loads(json.dumps(EMPTY_STATE, ensure_ascii=False)); save_state(s)
    reset_store_data(DB_PATH, 1)
    with conn() as c: c.execute('DELETE FROM uploads WHERE store_id=1'); c.commit()
    return {'state':s}

@app.post('/api/sample/{target}')
def sample(target: str):
    state=get_state(); now=int(time.time()*1000)
    if target in ('business','all'): state['business']=DEMO_BUSINESS.copy(); state['lastUpdated']['business']=now
    if target in ('products','all'): state['products']=json.loads(json.dumps(DEMO_PRODUCTS,ensure_ascii=False)); state['lastUpdated']['products']=now
    if target in ('platform','all'): state['platform']=json.loads(json.dumps(DEMO_PLATFORM,ensure_ascii=False)); state['lastUpdated']['platform']=now
    if target in ('workforce','all'): state['workforce']=json.loads(json.dumps(DEMO_WORKFORCE,ensure_ascii=False)); state['lastUpdated']['workforce']=now
    if target not in ('business','products','platform','workforce','all'): raise HTTPException(404,'未知数据类型')
    return {'state':save_state(state)}

@app.post('/api/upload/{target}')
async def upload(target: str, file: UploadFile = File(...)):
    if target not in ('business','validation','products','platform','workforce'): raise HTTPException(404,'未知数据类型')
    data=await file.read()
    try:
        if file.filename.lower().endswith('.json'):
            raw=json.loads(decode_text_bytes(data))
            rows=raw if isinstance(raw,list) else [raw]
        else:
            rows=parse_csv_bytes(data)
        state=get_state(); settings=state['settings']
        if target=='business': state['business']=normalize_business(rows,settings)
        elif target=='validation': state['validationBusiness']=normalize_business(rows,settings)
        elif target=='products': state['products']=normalize_products(rows)
        elif target=='platform': state['platform']=normalize_platform(rows)
        elif target=='workforce': state['workforce']=normalize_workforce(rows)
        state['lastUpdated'][target]=int(time.time()*1000)
        save_state(state)
        with conn() as c:
            c.execute('INSERT INTO uploads(store_id,target,filename,row_count,created_at) VALUES(1,?,?,?,?)',(target,file.filename,len(rows),int(time.time()*1000))); c.commit()
        return {'state':state,'target':target,'filename':file.filename,'rows':len(rows)}
    except Exception as e:
        raise HTTPException(400,str(e))

@app.post('/api/ask')
def ask(payload: AskPayload):
    state=get_state(); q=payload.question.strip()
    if not q: raise HTTPException(400,'问题不能为空')
    a=answer_question(state,q)
    state['chatHistory']=(state.get('chatHistory') or [])+ [{'q':q,'a':a,'ts':int(time.time()*1000),'engine':'server-rule'}]
    state['chatHistory']=state['chatHistory'][-20:]; save_state(state)
    return {'answer':a,'state':state,'engine':'server-rule'}

@app.post('/api/marketing')
def marketing(payload: MarketingPayload):
    state=get_state()
    try: result=marketing_generate(state,payload.product,payload.goal,payload.channel)
    except ValueError as e: raise HTTPException(400,str(e))
    state['marketingHistory']=[result]+(state.get('marketingHistory') or [])
    state['marketingHistory']=state['marketingHistory'][:10]
    state['selectedProduct']=result['product']; state['actions']['product']='planned'; save_state(state)
    return {'result':result,'state':state,'engine':'server-rule'}

@app.get('/api/uploads')
def uploads():
    with conn() as c: rows=c.execute('SELECT target,filename,row_count,created_at FROM uploads WHERE store_id=1 ORDER BY id DESC LIMIT 50').fetchall()
    return {'uploads':[dict(r) for r in rows]}


@app.get('/api/v2/channels')
def api_v2_channels():
    return {'channels': list_channels(DB_PATH)}

@app.get('/api/v2/sales/daily')
def api_v2_sales_daily(store_id: int = 1, start: str | None = None, end: str | None = None, channel_code: str | None = None):
    return {'sales': list_daily_sales(DB_PATH, store_id, start=start, end=end, channel_code=channel_code)}

@app.post('/api/v2/import/sales')
async def api_v2_import_sales(file: UploadFile = File(...), store_id: int = 1, source_type: str = 'MANUAL', channel_code: str | None = None):
    data = await file.read()
    try:
        result = import_daily_sales(DB_PATH, store_id, file.filename or 'sales.csv', data, source_type, channel_code, file.content_type)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    if result.status == 'FAILED':
        raise HTTPException(400, result.error_message or '销售数据导入失败，请检查字段')
    return {'import': result.to_dict()}

@app.get('/api/v2/settlements')
def api_v2_settlements(store_id: int = 1):
    return {'settlements': list_settlements(DB_PATH, store_id)}

@app.post('/api/v2/import/settlements')
async def api_v2_import_settlements(file: UploadFile = File(...), store_id: int = 1, source_type: str = 'MANUAL', channel_code: str | None = None):
    data = await file.read()
    try:
        result = import_settlements(DB_PATH, store_id, file.filename or 'settlements.csv', data, source_type, channel_code, file.content_type)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    if result.status == 'FAILED':
        raise HTTPException(400, result.error_message or '结算数据导入失败，请检查字段')
    return {'import': result.to_dict()}

@app.get('/api/v2/payments')
def api_v2_payments(store_id: int = 1):
    return {'payments': list_payments(DB_PATH, store_id)}

@app.post('/api/v2/import/payments')
async def api_v2_import_payments(file: UploadFile = File(...), store_id: int = 1, source_type: str = 'MANUAL', channel_code: str | None = None):
    data = await file.read()
    try:
        result = import_payments(DB_PATH, store_id, file.filename or 'payments.csv', data, source_type, channel_code, file.content_type)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    if result.status == 'FAILED':
        raise HTTPException(400, result.error_message or '到账数据导入失败，请检查字段')
    return {'import': result.to_dict()}

@app.get('/api/v2/reconciliation')
def api_v2_reconciliation(store_id: int = 1, tolerance: str = '10.00'):
    try:
        return get_reconciliation(DB_PATH, store_id, tolerance=tolerance)
    except Exception as exc:
        raise HTTPException(400, str(exc))

@app.get('/api/v2/import-batches')
def api_v2_import_batches(store_id: int = 1):
    return {'import_batches': list_import_batches(DB_PATH, store_id)}

@app.get('/api/backup')
def backup():
    payload = export_full_backup(DB_PATH, 1, get_state())
    return JSONResponse(
        payload,
        headers={'Content-Disposition':'attachment; filename="shice-ai-v050-r1-full-backup.json"'},
    )


@app.post('/api/restore')
def restore(payload: dict[str, Any]):
    try:
        restore_full_backup(DB_PATH, payload)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {'state': get_state(), 'restored': True}

if not _production_environment():
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
