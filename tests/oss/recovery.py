"""Bounded single-component failure drills in the explicitly selected lab context."""
import json,os,subprocess,time
import live_acceptance as a
context=os.environ["HAN_SOLO_CONTEXT"];ns=os.environ["HAN_SOLO_NAMESPACE"]
if context!="hansolo-wsl-oss-rc" or ns!="hansolo-oss-rc":raise SystemExit("dedicated RC lab required")
a.save=lambda:(a.OUT/"recovery.json").write_text(json.dumps(a.rows,indent=2))
def k(*args):
    return subprocess.check_output(["kubectl","--context",context,"-n",ns,*args],text=True,timeout=300)
state=json.loads(a.STATE.read_text())
token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
viewer=a.login("oss_viewer",state["viewer_password"])
def validate(label):
    a.req(label+" preserved JWT","GET","/api/auth/me",token)
    a.req(label+" RAG","POST","/api/rag/query",token,{"workspace_id":state["workspace"],"query":"NebulaGlass launch"},assertion=lambda d:bool(d.get("sources")) and bool(d.get("provenance")))
    a.req(label+" RBAC","POST","/api/rag/query",viewer,{"workspace_id":state["denied"],"query":"NebulaGlass"},status=403)
for name in ("cynovela","cynovela-worker","cynovela-redis","cynovela-pgvector"):
    before=json.loads(k("get","pods","-l","app="+name,"-o","json"))["items"][0]
    old=before["metadata"]["uid"]
    k("delete","pod",before["metadata"]["name"],"--wait=true")
    k("rollout","status","deploy/"+name,"--timeout=240s")
    pods=json.loads(k("get","pods","-l","app="+name,"-o","json"))["items"]
    a.check(name+" pod replacement",all(p["metadata"]["uid"]!=old for p in pods),{"pods":len(pods)},"new pod identity")
    # Port-forward supervisor reconnects after an API pod disappears.
    for _ in range(60):
        try:
            if a.requests.get(a.URL+"/api/ready",timeout=5).status_code==200:break
        except a.requests.RequestException:pass
        time.sleep(2)
    validate(name)
subprocess.run(["sudo","systemctl","restart","k3s-hansolo-wsl-oss-rc"],check=True,timeout=120)
for _ in range(90):
    try:
        if a.requests.get(a.URL+"/api/ready",timeout=5).status_code==200:break
    except a.requests.RequestException:pass
    time.sleep(2)
validate("K3s service restart")
assert all(r["status"]=="PASS" for r in a.rows)
