import json,sys,ast,urllib.request,urllib.error
U="http://127.0.0.1:9981/api/td/server/exec"
S=r"""
def _n(v):
    try: return float(v or 0)
    except: return 0.0
_o=op('/').findChildren(maxDepth=100)
# 1) 화면 복구: 모든 TOP 미리보기 다시 켜기
_on=0
for x in _o:
    try:
        if x.family=='TOP' and not x.viewer: x.viewer=True; _on+=1
    except: pass
# 2) 속도 프로파일
_r=[]
for x in _o:
    d={'p':x.path,'f':getattr(x,'family',''),'t':getattr(x,'type',''),'c':round(_n(getattr(x,'cookTime',0)),3)}
    try:
        if x.family=='TOP': d['w']=int(x.width); d['h']=int(x.height)
    except: pass
    _r.append(d)
_r.sort(key=lambda z:z['c'],reverse=True)
# 3) 영상(Movie In) 노드 상태
_mov=[]
for x in _o:
    try:
        if 'moviefilein' in x.type.lower():
            _p=getattr(x.par,'file',None)
            _mov.append({'p':x.path,'file':(_p.eval() if _p else ''),'w':int(x.width),'h':int(x.height),'c':round(_n(getattr(x,'cookTime',0)),3)})
    except: pass
try: _fp=_n(getattr(project,'cookRate',0))
except: _fp=0.0
result={'restored':_on,'fps':_fp,'n':len(_r),'tot':round(sum(z['c'] for z in _r),3),'top':_r[:15],'movies':_mov}
"""
def C(x):
    if isinstance(x,str):
        for p in (json.loads,ast.literal_eval):
            try: return p(x)
            except Exception: pass
    return x
def N(x,d=0.0):
    try: return float(x)
    except Exception: return d
def call(s):
    b=json.dumps({"script":s}).encode()
    q=urllib.request.Request(U,data=b,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(q,timeout=30) as r: return json.loads(r.read().decode("utf-8","replace"))
    except urllib.error.URLError as e: raise SystemExit("9981 연결 실패: %s"%e)
def find(res):
    d=res.get("data"); cands=[d]
    if isinstance(d,dict):
        cands.append(d.get("result"))
        if isinstance(d.get("result"),dict): cands.append(d["result"].get("value"))
    for c in cands:
        c=C(c)
        if isinstance(c,dict) and "fps" in c: return c
    return None
r=call(S); v=find(r)
if v is None:
    print("결과 구조를 못 찾음. 원본:"); print(json.dumps(r)[:2000]); sys.exit(1)
print("화면 복구: TOP 미리보기 %s개 다시 켬"%v.get("restored"))
fps=N(v.get("fps")); bud=1000.0/fps if fps else 0
print("fps=%g ops=%s cook합=%.1fms (예산~%.1fms)"%(fps,v.get("n"),N(v.get("tot")),bud))
print("[가장 느린 노드]")
for x in C(v.get("top")) or []:
    x=C(x)
    if not isinstance(x,dict): continue
    rs=("  %sx%s"%(x.get("w"),x.get("h"))) if "w" in x else ""
    print("  %8.3f ms  %-38s %-4s%s"%(N(x.get("c")),x.get("p"),x.get("f"),rs))
print("[영상 노드]")
for m in C(v.get("movies")) or []:
    m=C(m)
    if isinstance(m,dict): print("  %s  %sx%s  %.1fms  file=%s"%(m.get("p"),m.get("w"),m.get("h"),N(m.get("c")),m.get("file")))
