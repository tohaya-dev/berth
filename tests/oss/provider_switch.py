"""External provider selection contracts using only the isolated HTTP fixture."""
import json
import live_acceptance as a
a.save=lambda:(a.OUT/"provider-switch.json").write_text(json.dumps(a.rows,indent=2))
s=json.loads(a.STATE.read_text());token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
try:
    for provider in ("ollama","openai_compat","lmstudio"):
        a.req("switch "+provider,"POST","/api/settings/llm",token,
            {"provider":provider,"base_url":"http://hansolo-test-provider:8000/v1","model":"synthetic-contract","api_key":"synthetic-test-only"})
        a.req("RAG "+provider,"POST","/api/rag/query",token,{"workspace_id":s["workspace"],"query":"NebulaGlass launch"},
            assertion=lambda d:bool(d.get("sources")) and bool(d.get("provenance")))
finally:
    a.req("restore local lab provider","POST","/api/settings/llm",token,
        {"provider":"lmstudio","base_url":"http://hansolo-test-provider:8000/v1","model":"synthetic-contract"})
assert all(r["status"]=="PASS" for r in a.rows)
