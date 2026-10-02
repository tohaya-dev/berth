"""Actual isolated Kubernetes acceptance. Reports never contain credentials."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[2]
DATA=Path(os.environ.get("HAN_SOLO_DATA_DIR",str(Path.home()/".local/share/hansolo-oss-rc")))
URL=os.environ.get("CYNOVELA_URL","http://127.0.0.1:28765")
OUT=ROOT/"_oss-rc-artifacts"
STATE=DATA/"secrets"/"live-state.json"
rows=[]
known=[]
def redact(value):
    if isinstance(value,dict):
        return {k:("[REDACTED]" if any(s in k.lower() for s in ("password","token","key","authorization")) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(x) for x in value]
    if isinstance(value,str):
        for s in known:
            if s:value=value.replace(s,"[REDACTED]")
    return value
def save():
    (OUT/"live-results.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2))
def check(name,ok,actual,expected):
    rows.append({"test":name,"status":"PASS" if ok else "FAIL","expected":expected,"actual":redact(actual)})
    save();print(rows[-1]["status"],name,flush=True)
def req(name,method,path,token=None,body=None,status=200,assertion=None):
    headers={"Authorization":"Bearer "+token} if token else {}
    response=requests.request(method,URL+path,headers=headers,json=body,timeout=240)
    try:data=response.json()
    except ValueError:data={"text":response.text[:1500]}
    good=response.status_code in (status if isinstance(status,tuple) else (status,))
    if assertion:good=good and assertion(data)
    check(name,good,{"http":response.status_code,"body":data},{"http":status,"request":{"method":method,"path":path,"body":redact(body)}})
    return data
def login(username,password):
    known.append(password)
    for attempt in range(3):
        response=requests.post(URL+"/api/auth/login",json={"username":username,"password":password},timeout=30)
        if response.status_code!=429 or attempt==2:break
        print("Observed login rate limit; waiting for the existing window",flush=True)
        time.sleep(61)
    d=response.json();t=d.get("access_token","");known.append(t)
    check("login "+username,response.status_code==200 and bool(t),{"http":response.status_code},200)
    if not t:raise RuntimeError("login failed")
    return t
def main():
    if STATE.exists():
        state=json.loads(STATE.read_text());known.extend(state.get(k,"") for k in ("viewer_password",))
    else:state={}
    admin=login("cynovela",(DATA/"secrets/admin_password").read_text().strip())
    req("health","GET","/api/health",assertion=lambda d:d["status"]=="ok")
    req("ready","GET","/api/ready",assertion=lambda d:d["status"]=="ready")
    req("whoami","GET","/api/auth/me",admin)
    if "viewer" not in state:
        state["viewer_password"]=secrets.token_urlsafe(24);known.append(state["viewer_password"])
        user=req("create viewer","POST","/api/admin/users",admin,
                 {"username":"oss_viewer","display_name":"OSS Viewer","role":"viewer","password":state["viewer_password"]})
        state["viewer"]=user["id"]
    viewer=login("oss_viewer",state["viewer_password"])
    req("viewer initial password rotation","POST","/api/auth/change-password",viewer,
        {"current_password":state["viewer_password"],"new_password":state["viewer_password"]})
    if "workspace" not in state:
        state["workspace"]=req("create allowed workspace","POST","/api/workspaces",admin,
            {"name":"OSS allowed","user_ids":["user-admin",state["viewer"]]})["id"]
        state["denied"]=req("create denied workspace","POST","/api/workspaces",admin,
            {"name":"OSS restricted","user_ids":["user-admin"]})["id"]
    STATE.write_text(json.dumps(state));STATE.chmod(0o600)
    ws=state["workspace"]
    req("admin workspace","GET","/api/workspaces/"+ws,admin)
    req("viewer workspace","GET","/api/workspaces",viewer,assertion=lambda d:ws in json.dumps(d))
    req("unauthorized workspace denial","GET","/api/workspaces/"+state["denied"],viewer,status=403)
    req("invalid JWT","GET","/api/auth/me","invalid.jwt.signature",status=401)
    req("invalid API key","GET","/api/auth/me","cyn_invalid",status=401)
    req("viewer admin write denied","POST","/api/workspaces",viewer,{"name":"must-not-exist"},status=403)
    req("invalid workspace input","POST","/api/workspaces",admin,{"name":""},status=400)
    req("missing workspace","GET","/api/workspaces/missing-oss-synthetic",admin,status=404)
    req("provider configuration","POST","/api/settings/llm",admin,
        {"provider":"lmstudio","base_url":"http://hansolo-test-provider:8000/v1","model":"synthetic-contract"})
    docs=DATA/"ingest"/"acceptance";docs.mkdir(parents=True,exist_ok=True)
    (docs/"nebula-en.txt").write_text("NebulaGlass project launches the orchard observatory in April 2042. The mission studies amber apples. Contact Alice Example at alice.example@example.org.\n"*8)
    (docs/"nebula-ja.txt").write_text("ネビュラ果樹園計画は2042年4月に開始します。観測対象は琥珀色のリンゴです。問い合わせ先は山田太郎、taro@example.org です。\n"*8)
    if "source" not in state:
        state["source"]=req("create source","POST","/api/sources",admin,{"name":"OSS synthetic docs","path":"/app/ingest/acceptance","auto_scan":False})["id"]
        req("link source workspace","PATCH","/api/workspaces/"+ws,admin,{"source_ids":[state["source"]]})
        STATE.write_text(json.dumps(state))
    req("scan source","POST","/api/sources/"+state["source"]+"/scan",admin)
    for _ in range(90):
        r=requests.get(URL+"/api/sources/"+state["source"],headers={"Authorization":"Bearer "+admin},timeout=20).json()
        if r.get("status") in ("completed","failed"):break
        time.sleep(2)
    check("ingest scan completes",r.get("status")=="completed",r,"completed")
    files=req("source files","GET","/api/sources/"+state["source"]+"/files",admin)
    items=files if isinstance(files,list) else files.get("items",files.get("files",[]))
    if "collection" not in state:
        state["collection"]=req("create collection","POST","/api/collections",admin,
            {"name":"OSS governed corpus","workspace_id":ws,"file_ids":[f["id"] for f in items]})["id"]
        STATE.write_text(json.dumps(state))
    req("publish collection","POST","/api/collections/"+state["collection"]+"/publish",admin)
    for _ in range(120):
        col=requests.get(URL+"/api/collections/"+state["collection"],headers={"Authorization":"Bearer "+admin},timeout=20).json()
        if col.get("status") in ("ready","failed"):break
        time.sleep(3)
    check("publish real vectors",col.get("status")=="ready" and col.get("chunk_count",0)>0,col,"ready with chunks")
    for role,token in (("admin",admin),("viewer",viewer)):
        req(role+" RAG","POST","/api/rag/query",token,{"workspace_id":ws,"query":"When does NebulaGlass launch?","collection_ids":[state["collection"]]},
            assertion=lambda d:bool(d.get("sources") or d.get("citations") or d.get("results")))
    req("Japanese RAG","POST","/api/rag/query",viewer,{"workspace_id":ws,"query":"ネビュラ果樹園計画はいつ開始しますか？","collection_ids":[state["collection"]]},
        assertion=lambda d:bool(d.get("sources") or d.get("citations") or d.get("results")))
    check("all live checks",all(r["status"]=="PASS" for r in rows),{"checks":len(rows)},"all PASS")

if __name__=="__main__":
    try:main()
    finally:save()
    raise SystemExit(0 if all(r["status"]=="PASS" for r in rows) else 1)
