"""Long Unicode input, duplicate ingest, invalid PDF, and reindex."""
import json,time,secrets
import live_acceptance as a
a.save=lambda:(a.OUT/"rag-edges.json").write_text(json.dumps(a.rows,indent=2))
token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
name="edge-"+secrets.token_hex(4)
path=a.DATA/"ingest"/name;path.mkdir()
(path/"long-unicode.txt").write_text(("AsterLemon研究計画 🌌 studies violet citrus in October 2044. Unicode evidence: café, 東京, αβγ.\n")*250)
(path/"invalid.pdf").write_bytes(b"This is not a PDF file.")
ws=a.req("edge workspace","POST","/api/workspaces",token,{"name":name})["id"]
src=a.req("edge source","POST","/api/sources",token,{"name":name,"path":"/app/ingest/"+name,"auto_scan":False})["id"]
a.req("edge linkage","PATCH","/api/workspaces/"+ws,token,{"source_ids":[src]})
def scan():
    a.req("edge scan","POST","/api/sources/"+src+"/scan",token)
    for _ in range(120):
        d=a.requests.get(a.URL+"/api/sources/"+src,headers={"Authorization":"Bearer "+token},timeout=20).json()
        if d.get("status") in ("completed","failed"):break
        time.sleep(2)
    files=a.req("edge files","GET","/api/sources/"+src+"/files",token)
    return files if isinstance(files,list) else files.get("files",files.get("items",[]))
first=scan();second=scan()
a.check("duplicate scan stable file identities",{f["id"] for f in first}=={f["id"] for f in second},
    {"first":len(first),"second":len(second)},"no duplicate file rows")
valid=[f["id"] for f in second if f["name"].endswith(".txt")]
col=a.req("edge collection","POST","/api/collections",token,{"name":name,"workspace_id":ws,"file_ids":valid})["id"]
def publish(cid):
    a.req("edge publish","POST","/api/collections/"+cid+"/publish",token)
    for _ in range(180):
        d=a.requests.get(a.URL+"/api/collections/"+cid,headers={"Authorization":"Bearer "+token},timeout=20).json()
        if d.get("status") in ("ready","failed"):return d
        time.sleep(2)
    return d
before=publish(col);after=publish(col)
a.check("long Unicode and reindex",before.get("status")=="ready" and after.get("status")=="ready" and
        before.get("chunk_count",0)>1 and before.get("chunk_count")==after.get("chunk_count"),
        {"before":before.get("chunk_count"),"after":after.get("chunk_count")},"multiple chunks, stable reindex")
a.req("long Unicode retrieval","POST","/api/rag/query",token,{"workspace_id":ws,"query":"AsterLemon研究計画の開始時期は？"},
    assertion=lambda d:bool(d.get("sources")) and bool(d.get("provenance")))
invalid=[f["id"] for f in second if f["name"].endswith(".pdf")]
if invalid:
    bad=a.req("invalid document collection","POST","/api/collections",token,{"name":name+" invalid","workspace_id":ws,"file_ids":invalid})["id"]
    result=publish(bad)
    a.check("invalid PDF produces no vectors",result.get("status")=="failed" and result.get("chunk_count",0)==0,{"status":result.get("status"),"chunks":result.get("chunk_count")},"failed with zero chunks")
else:a.check("invalid PDF excluded at scan",True,{"invalid_file_rows":0},"excluded")
assert all(r["status"]=="PASS" for r in a.rows)
