import json,sys,ast,urllib.request,urllib.error
U="http://127.0.0.1:9981/api/td/server/exec"
APPLY=r"""
def _n(v):
    try: return float(v or 0)
    except: return 0.0
_o=op('/').findChildren(maxDepth=100)
_tops=[x for x in _o if getattr(x,'family','')=='TOP']
_tops.sort(key=lambda x:_n(getattr(x,'cookTime',0)),reverse=True)
_bk={}; _ch=[]; _sk=[]
for x in _tops:
    try: w=int(x.width); h=int(x.height)
    except: continue
    ck=_n(getattr(x,'cookTime',0))
    if w*h<=1920*1080 or ck<1.0: continue
    m=getattr(x.par,'resolution',None); rw=getattr(x.par,'resolutionw',None); rh=getattr(x.par,'resolutionh',None)
    if m is None or rw is None or rh is None:
        _sk.append({'p':x.path,'why':'해상도 파라미터 없음','wh':[w,h],'ck':round(ck,2)}); continue
    mv=str(m.eval()).lower()
    if ('custom' not in mv) and ('fixed' not in mv):
        _sk.append({'p':x.path,'why':'입력에서 크기 받음('+mv+') - 건드리지 않음','wh':[w,h],'ck':round(ck,2)}); continue
    f=min(1920.0/w,1080.0/h); nw=max(2,int(w*f)); nh=max(2,int(h*f))
    _bk[x.path]={'rw':rw.eval(),'rh':rh.eval()}
    try:
        rw.val=nw; rh.val=nh
        _ch.append({'p':x.path,'frm':[w,h],'to':[nw,nh],'ck':round(ck,2)})
    except Exception:
        _sk.append({'p':x.path,'why':'변경 실패','wh':[w,h],'ck':round(ck,2)})
op('/').store('tdfast_backup',_bk)
result={'changed':_ch,'skipped':_sk,'n':len(_ch)}
"""
UNDO=r"""
_bk=op('/').fetch('tdfast_backup',{})
_c=0
for p,b in dict(_bk).items():
    o=op(p)
    if not o: continue
    try:
        o.par.resolutionw.val=b['rw']; o.par.resolutionh.val=b['rh']; _c+=1
    except Exception: pass
result={'restored':_c}
"""
def C(x):
    if isinstance(x,str):
        for p in (json.loads,ast.literal_eval):
            try: return p(x)
            except Exception: pass
    return x
def call(s):
    b=json.dumps({"script":s}).encode()
    q=urllib.request.Request(U,data=b,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(q,timeout=30) as r: return json.loads(r.read().decode("utf-8","replace"))
    except urllib.error.URLError as e: raise SystemExit("9981 연결 실패: %s"%e)
def find(res,key):
    d=res.get("data"); cands=[d]
    if isinstance(d,dict):
        cands.append(d.get("result"))
        if isinstance(d.get("result"),dict): cands.append(d["result"].get("value"))
    for c in cands:
        c=C(c)
        if isinstance(c,dict) and key in c: return c
    return None
mode=sys.argv[1] if len(sys.argv)>1 else "apply"
if mode=="undo":
    v=find(call(UNDO),"restored") or {}
    print("되돌리기 완료: %s개 노드 해상도 원복"%v.get("restored")); sys.exit(0)
v=find(call(APPLY),"changed")
if v is None: print("결과 구조 못 찾음"); sys.exit(1)
ch=C(v.get("changed")) or []; sk=C(v.get("skipped")) or []
print("=== 자동 최적화 완료: %s개 TOP 해상도 축소 ==="%v.get("n"))
for x in ch:
    x=C(x)
    if isinstance(x,dict): print("  %s  %sx%s -> %sx%s  (%.1fms)"%(x["p"],x["frm"][0],x["frm"][1],x["to"][0],x["to"][1],x.get("ck",0)))
if sk:
    print("--- 안전상 건드리지 않은 무거운 노드 (수동 확인 필요) ---")
    for x in sk[:10]:
        x=C(x)
        if isinstance(x,dict): print("  %s  %sx%s  %.1fms  [%s]"%(x["p"],x["wh"][0],x["wh"][1],x.get("ck",0),x.get("why")))
print("되돌리려면:  python tdfast.py undo")
