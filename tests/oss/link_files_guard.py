"""link-files must apply the same workspace-membership and sensitivity guard as collection creation."""
import json,secrets,time
import live_acceptance as a
a.save=lambda:(a.OUT/"link-files-guard.json").write_text(json.dumps(a.rows,ensure_ascii=False,indent=2))
admin=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
tag=secrets.token_hex(4)
def folder(name,text):
    d=a.DATA/"ingest"/name;d.mkdir(parents=True,exist_ok=True)
    (d/(name+".txt")).write_text(text*12);return "/app/ingest/"+name
def source(name,path):
    sid=a.req("source "+name,"POST","/api/sources",admin,{"name":name,"path":path,"auto_scan":False})["id"]
    a.req("scan "+name,"POST","/api/sources/"+sid+"/scan",admin)
    for _ in range(120):
        d=a.requests.get(a.URL+"/api/sources/"+sid,headers={"Authorization":"Bearer "+admin},timeout=20).json()
        if d.get("status") in ("completed","failed"):break
        time.sleep(2)
    a.check("ingest "+name,d.get("status")=="completed",{"status":d.get("status")},"completed")
    files=a.req("files "+name,"GET","/api/sources/"+sid+"/files",admin)
    items=files if isinstance(files,list) else files.get("items",files.get("files",[]))
    return sid,[x["id"] for x in items]
conf_src,conf_files=source("guard-conf-"+tag,folder("guard-conf-"+tag,"GuardMarker"+tag+" is the restricted observatory authorization phrase. This confidential research concerns cobalt pears.\n"))
pub_src,pub_files=source("guard-pub-"+tag,folder("guard-pub-"+tag,"The orchard newsletter describes amber apples and public visiting hours.\n"))
other_src,other_files=source("guard-other-"+tag,folder("guard-other-"+tag,"The harbour bulletin lists public ferry timetables for the spring season.\n"))
ws_a=a.req("workspace A","POST","/api/workspaces",admin,{"name":"Guard A "+tag,"user_ids":["user-admin"]})["id"]
ws_b=a.req("workspace B","POST","/api/workspaces",admin,{"name":"Guard B "+tag,"user_ids":["user-admin"]})["id"]
a.req("link sources A","PATCH","/api/workspaces/"+ws_a,admin,{"source_ids":[conf_src,pub_src]})
a.req("link sources B","PATCH","/api/workspaces/"+ws_b,admin,{"source_ids":[other_src]})
a.req("create-time guard still rejects confidential+public","POST","/api/collections",admin,
    {"name":"Guard create "+tag,"workspace_id":ws_a,"file_ids":conf_files,"access_level":"public"},status=400)
pub_col=a.req("public collection","POST","/api/collections",admin,{"name":"Guard public "+tag,"workspace_id":ws_a,"file_ids":[],"access_level":"public"})["id"]
conf_col=a.req("confidential collection","POST","/api/collections",admin,{"name":"Guard confidential "+tag,"workspace_id":ws_a,"file_ids":[],"access_level":"confidential"})["id"]
a.req("link-files: confidential file into public collection rejected","POST","/api/collections/"+pub_col+"/link-files",admin,{"file_ids":conf_files},status=400)
a.req("link-files: other-workspace file rejected","POST","/api/collections/"+pub_col+"/link-files",admin,{"file_ids":other_files},status=400)
a.req("link-files: mixed request rejected as a whole","POST","/api/collections/"+pub_col+"/link-files",admin,{"file_ids":pub_files+conf_files},status=400)
un=a.req("public collection still has no files","GET","/api/collections/"+pub_col+"/unlinked-files",admin)
a.check("rejected requests linked nothing",set(pub_files+conf_files)<= {f["id"] for f in un.get("files",[])},{"unlinked":un.get("count")},"all files still unlinked")
a.req("link-files: public file into public collection allowed","POST","/api/collections/"+pub_col+"/link-files",admin,{"file_ids":pub_files})
a.req("link-files: confidential file into confidential collection allowed","POST","/api/collections/"+conf_col+"/link-files",admin,{"file_ids":conf_files})
a.check("all link-files guard checks",all(r["status"]=="PASS" for r in a.rows),{"checks":len(a.rows)},"all PASS")
a.save()
assert all(r["status"]=="PASS" for r in a.rows)
