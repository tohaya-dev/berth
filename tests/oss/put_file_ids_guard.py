"""PUT /api/collections/{id} file_ids must apply the same workspace-membership and sensitivity guard as link-files."""
import json,secrets,time
import live_acceptance as a
a.save=lambda:(a.OUT/"put-file-ids-guard.json").write_text(json.dumps(a.rows,ensure_ascii=False,indent=2))
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
def linked(col,ids):
    un=a.requests.get(a.URL+"/api/collections/"+col+"/unlinked-files",headers={"Authorization":"Bearer "+admin},timeout=20).json()
    unlinked={f["id"] for f in un.get("files",[])}
    return [i for i in ids if i not in unlinked]
conf_src,conf_files=source("putguard-conf-"+tag,folder("putguard-conf-"+tag,"PutGuardMarker"+tag+" is the restricted observatory authorization phrase. This confidential research concerns cobalt pears.\n"))
pub_src,pub_files=source("putguard-pub-"+tag,folder("putguard-pub-"+tag,"The orchard newsletter describes amber apples and public visiting hours.\n"))
other_src,other_files=source("putguard-other-"+tag,folder("putguard-other-"+tag,"The harbour bulletin lists public ferry timetables for the spring season.\n"))
ws_a=a.req("workspace A","POST","/api/workspaces",admin,{"name":"PutGuard A "+tag,"user_ids":["user-admin"]})["id"]
ws_b=a.req("workspace B","POST","/api/workspaces",admin,{"name":"PutGuard B "+tag,"user_ids":["user-admin"]})["id"]
a.req("link sources A","PATCH","/api/workspaces/"+ws_a,admin,{"source_ids":[conf_src,pub_src]})
a.req("link sources B","PATCH","/api/workspaces/"+ws_b,admin,{"source_ids":[other_src]})
pub_col=a.req("public draft collection","POST","/api/collections",admin,{"name":"PutGuard public "+tag,"workspace_id":ws_a,"file_ids":pub_files,"access_level":"public"})["id"]
conf_col=a.req("confidential draft collection","POST","/api/collections",admin,{"name":"PutGuard confidential "+tag,"workspace_id":ws_a,"file_ids":[],"access_level":"confidential"})["id"]
a.req("PUT file_ids: confidential file into public collection rejected","PUT","/api/collections/"+pub_col,admin,{"file_ids":pub_files+conf_files},status=400)
a.req("PUT file_ids: other-workspace file rejected","PUT","/api/collections/"+pub_col,admin,{"file_ids":pub_files+other_files},status=400)
a.req("PUT file_ids: downgrade to public with confidential files rejected","PUT","/api/collections/"+conf_col,admin,{"access_level":"public","file_ids":conf_files},status=400)
a.req("PUT file_ids: non-list rejected","PUT","/api/collections/"+pub_col,admin,{"file_ids":"not-a-list"},status=400)
# unlinked-files only lists files of this workspace, so other-workspace files are checked through the collection detail instead.
a.check("rejected PUT kept the previous file set",sorted(linked(pub_col,pub_files+conf_files))==sorted(pub_files),{"linked":len(linked(pub_col,pub_files+conf_files))},"only the original public file")
detail=a.requests.get(a.URL+"/api/collections/"+pub_col,headers={"Authorization":"Bearer "+admin},timeout=20).json()
a.check("other-workspace file is not in the collection",not any(f in json.dumps(detail) for f in other_files),{"file_count":detail.get("file_count")},"absent")
a.check("rejected PUT kept the confidential collection empty",linked(conf_col,conf_files)==[],{"linked":len(linked(conf_col,conf_files))},"no files")
lvl=a.requests.get(a.URL+"/api/collections/"+conf_col,headers={"Authorization":"Bearer "+admin},timeout=20).json().get("access_level")
a.check("rejected downgrade left access_level unchanged",lvl=="confidential",{"access_level":lvl},"confidential")
a.req("PUT file_ids: public file set allowed","PUT","/api/collections/"+pub_col,admin,{"file_ids":pub_files})
a.req("PUT file_ids: confidential file into confidential collection allowed","PUT","/api/collections/"+conf_col,admin,{"file_ids":conf_files})
a.check("allowed PUT linked the confidential file",linked(conf_col,conf_files)==conf_files,{"linked":len(linked(conf_col,conf_files))},"1 file")
a.check("all PUT file_ids guard checks",all(r["status"]=="PASS" for r in a.rows),{"checks":len(a.rows)},"all PASS")
a.save()
assert all(r["status"]=="PASS" for r in a.rows)
