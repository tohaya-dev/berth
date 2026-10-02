"""Live scope, masking, CLI and HTTP MCP checks against the isolated lab."""
import json,os,re,secrets,subprocess,sys,tempfile
from pathlib import Path
import live_acceptance as a

s=json.loads(a.STATE.read_text())
a.save=lambda:(a.OUT/"security-mcp-cli.json").write_text(json.dumps(a.rows,ensure_ascii=False,indent=2))
admin=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
viewer=a.login("oss_viewer",s["viewer_password"])
ws,col=s["workspace"],s["collection"]
def rpc(name,method,params,token=admin,ok=True):
    d=a.req(name,"POST","/api/mcp/rpc",token,{"jsonrpc":"2.0","id":1,"method":method,"params":params})
    error="error" in d or d.get("result",{}).get("isError",False)
    a.check(name+" contract",not error if ok else error,d,"success" if ok else "denial/error")
    return d
def call(name,arguments,token=admin,ok=True):
    return rpc("MCP "+name,"tools/call",{"name":name,"arguments":arguments},token,ok)

a.req("viewer allowed workspace list","GET","/api/workspaces",viewer,assertion=lambda d:ws in json.dumps(d) and s["denied"] not in json.dumps(d))
a.req("viewer collection list","GET","/api/collections?workspace_id="+ws,viewer,assertion=lambda d:col in json.dumps(d))
a.req("viewer cross-workspace RAG denial","POST","/api/rag/query",viewer,{"workspace_id":s["denied"],"query":"NebulaGlass"},status=403)
a.req("privilege escalation rejected","POST","/api/admin/users",viewer,{"username":"illegal","role":"admin","password":"synthetic-only"},status=403)
a.req("viewer self role escalation ignored","PATCH","/api/users/"+s["viewer"],viewer,{"role":"admin"},assertion=lambda d:d.get("status")=="no_change")
a.req("viewer still viewer","GET","/api/auth/me",viewer,assertion=lambda d: d.get("role",d.get("user",{}).get("role"))=="viewer")
a.req("invalid query","POST","/api/rag/query",admin,{"workspace_id":ws,"query":""},status=400)
a.req("oversize query","POST","/api/rag/query",admin,{"workspace_id":ws,"query":"x"*4001},status=413)
empty=a.req("create empty workspace","POST","/api/workspaces",admin,{"name":"OSS empty "+secrets.token_hex(4)})["id"]
a.req("no-result workspace","POST","/api/rag/query",admin,{"workspace_id":empty,"query":"Never indexed object"},assertion=lambda d:not d.get("sources"))

key=a.req("scoped key issuance","POST","/api/keys",admin,{"name":"OSS scoped","role":"viewer","user_id":s["viewer"],"scope":{"workspaces":[ws],"collections":[col]}})
token=key["key"];a.known.append(token)
# Re-save after learning response-only key so earlier evidence is scrubbed too.
for row in a.rows:row["actual"]=a.redact(row["actual"])
a.save()
a.req("scoped key identity","GET","/api/auth/me",token)
a.req("scoped key cross workspace","POST","/api/rag/query",token,{"workspace_id":empty,"query":"test"},status=403)
a.req("MCP fingerprint","GET","/api/mcp/fingerprint",viewer)
rpc("MCP tools admin","tools/list",{})
rpc("MCP tools scoped viewer","tools/list",{},token)
call("whoami",{},token)
call("list_workspaces",{},token)
call("list_collections",{"workspace_id":ws},token)
call("list_chunks",{"workspace_id":empty},token,False)
call("no_such_tool",{},admin,False)
call("search_collection",{},token,False)
rpc("MCP unauthenticated","tools/list",{},None,False)
call("search_collection",{"query":"When does NebulaGlass launch?","workspace_id":ws,"collection_id":col},token)
# manage_keys revoke is a documented operation protected by the double gate.
call("manage_keys",{"action":"revoke","key_id":key["id"],"confirm":True},admin,False)
a.req("key revocation","DELETE","/api/keys/"+key["id"],admin)
a.req("revoked key denied","GET","/api/auth/me",token,status=401)
rpc("MCP revoked key denied","tools/list",{},token,False)
rag=a.req("masked retrieval","POST","/api/rag/query",viewer,{"workspace_id":ws,"query":"Contact and launch date of NebulaGlass","collection_ids":[col]})
text=json.dumps(rag,ensure_ascii=False)
a.check("email and Japanese name non-leakage",all(v not in text for v in ("alice.example@example.org","taro@example.org","山田太郎")) and "MASKED" in text,{"leak":any(v in text for v in ("alice.example@example.org","taro@example.org","山田太郎"))},"no raw PII; masked marker present")
hits=rag.get("retrieval_detail",{}).get("hits",[])
a.check("real vector and rerank evidence",any(float(h.get("vector_score",0))>0 for h in hits) and any(float(h.get("rerank_score",0))>0 for h in hits),{"hits":hits},"nonzero vector and reranker scores")
a.check("provenance retained",bool(rag.get("provenance")) and bool(rag.get("sources")),rag.get("provenance"),"source-backed provenance")

with tempfile.TemporaryDirectory(prefix="hansolo-cli-") as tmp:
    env=dict(os.environ,HOME=tmp,CYNOVELA_URL=a.URL,CYNOVELA_TOKEN=admin)
    cli=[sys.executable,str(a.ROOT/"cynovela_cli.py")]
    helptext=subprocess.check_output(cli+["--help"],text=True,env=env)
    commands=re.search(r"\{([^}]+)\}",helptext).group(1).split(",")
    for command in commands:
        for option,expected in (("--help",0),("--invalid-oss-option",2)):
            p=subprocess.run(cli+[command,option],capture_output=True,text=True,env=env,timeout=20)
            a.check("CLI parser "+command+" "+option,p.returncode==expected,{"exit":p.returncode},expected)
    for args in (["status"],["health"],["workspaces","list"],["collections","list","--workspace",ws],
                 ["sources","list"],["ingest-roots","list"],["jobs"],["settings","show"],["mcp","fingerprint"],
                 ["search","--workspace",ws,"--query","NebulaGlass launch"],["chat","--workspace",ws,"--query","NebulaGlass launch"]):
        p=subprocess.run(cli+["--json"]+args,capture_output=True,text=True,env=env,timeout=240)
        a.check("CLI functional "+" ".join(args[:2]),p.returncode==0,{"exit":p.returncode,"output":p.stdout[:2000],"error":p.stderr[:500]},0)
    p=subprocess.run(cli+["--url","http://127.0.0.1:1","health"],capture_output=True,text=True,env=env,timeout=20)
    a.check("CLI bad endpoint",p.returncode!=0,{"exit":p.returncode},"nonzero")
    p=subprocess.run(cli+["workspaces","register","--name","denied-cli"],capture_output=True,text=True,env=dict(env,CYNOVELA_TOKEN=viewer),timeout=20)
    a.check("CLI permission denial",p.returncode!=0,{"exit":p.returncode},"nonzero")
a.check("all security MCP CLI checks",all(r["status"]=="PASS" for r in a.rows),{"checks":len(a.rows)},"all PASS")

raise SystemExit(0 if all(r["status"]=="PASS" for r in a.rows) else 1)
