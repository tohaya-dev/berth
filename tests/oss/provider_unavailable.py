import json
import live_acceptance as a
a.save=lambda:(a.OUT/"provider-unavailable.json").write_text(json.dumps(a.rows,indent=2))
token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
s=json.loads(a.STATE.read_text())
try:
    a.req("unavailable provider configured","POST","/api/settings/llm",token,
        {"provider":"lmstudio","base_url":"http://127.0.0.1:9/v1","model":"synthetic-contract"})
    a.req("unavailable provider query fails","POST","/api/rag/query",token,
        {"workspace_id":s["workspace"],"query":"NebulaGlass launch"},status=(400,502,503),
        assertion=lambda d:not d.get("sources") and not d.get("answer"))
finally:
    a.req("provider restored","POST","/api/settings/llm",token,
        {"provider":"lmstudio","base_url":"http://hansolo-test-provider:8000/v1","model":"synthetic-contract"})
a.req("provider recovery RAG","POST","/api/rag/query",token,{"workspace_id":s["workspace"],"query":"NebulaGlass launch"},
    assertion=lambda d:bool(d.get("sources")))
assert all(r["status"]=="PASS" for r in a.rows)
