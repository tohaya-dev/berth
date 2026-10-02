"""Unique restricted evidence must never reach a viewer's RAG context."""
import json,secrets,time
import live_acceptance as a
a.save=lambda:(a.OUT/"restricted-corpus.json").write_text(json.dumps(a.rows,indent=2))
s=json.loads(a.STATE.read_text())
admin=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
viewer=a.login("oss_viewer",s["viewer_password"])
marker="ObsidianMarigoldVault"+secrets.token_hex(6)
path=a.DATA/"ingest"/"restricted";path.mkdir(exist_ok=True)
(path/"restricted.txt").write_text((marker+" is the restricted observatory authorization phrase. This confidential research concerns cobalt pears.\n")*12)
source=a.req("restricted source","POST","/api/sources",admin,{"name":"Restricted synthetic","path":"/app/ingest/restricted","auto_scan":False})["id"]
a.req("link restricted source","PATCH","/api/workspaces/"+s["workspace"],admin,{"source_ids":[s["source"],source]})
a.req("scan restricted","POST","/api/sources/"+source+"/scan",admin)
for _ in range(120):
    d=a.requests.get(a.URL+"/api/sources/"+source,headers={"Authorization":"Bearer "+admin},timeout=20).json()
    if d.get("status") in ("completed","failed"):break
    time.sleep(2)
a.check("restricted ingest complete",d.get("status")=="completed",{"status":d.get("status")},"completed")
files=a.req("restricted source files","GET","/api/sources/"+source+"/files",admin)
items=files if isinstance(files,list) else files.get("items",files.get("files",[]))
col=a.req("create confidential collection","POST","/api/collections",admin,
    {"name":"Restricted "+marker,"workspace_id":s["workspace"],"file_ids":[x["id"] for x in items],"access_level":"confidential"})["id"]
a.req("publish confidential","POST","/api/collections/"+col+"/publish",admin)
for _ in range(120):
    d=a.requests.get(a.URL+"/api/collections/"+col,headers={"Authorization":"Bearer "+admin},timeout=20).json()
    if d.get("status") in ("ready","failed"):break
    time.sleep(3)
a.check("restricted vectors published",d.get("status")=="ready" and d.get("chunk_count",0)>0,{"status":d.get("status"),"chunks":d.get("chunk_count")},"ready")
a.req("admin restricted evidence","POST","/api/rag/query",admin,{"workspace_id":s["workspace"],"query":"What is the restricted observatory authorization phrase?","collection_ids":[col]},
    assertion=lambda d:marker in json.dumps(d))
a.req("viewer confidential list hidden","GET","/api/collections?workspace_id="+s["workspace"],viewer,
    assertion=lambda d:col not in json.dumps(d) and marker not in json.dumps(d))
d=a.req("viewer explicit confidential query","POST","/api/rag/query",viewer,
    {"workspace_id":s["workspace"],"query":"What is the restricted observatory authorization phrase?","collection_ids":[col]},status=(200,403,404))
a.check("restricted marker non-leakage",marker not in json.dumps(d) and col not in json.dumps(d),{"leak":marker in json.dumps(d)},"no restricted evidence")
s["restricted_collection"]=col;a.STATE.write_text(json.dumps(s))
assert all(r["status"]=="PASS" for r in a.rows)
