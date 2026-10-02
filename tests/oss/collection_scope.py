import json
import live_acceptance as a
a.save=lambda:(a.OUT/"collection-scope.json").write_text(json.dumps(a.rows,indent=2))
s=json.loads(a.STATE.read_text());token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
col=a.req("second public collection","POST","/api/collections",token,{"name":"OSS outside key scope","workspace_id":s["workspace"],"file_ids":[]})["id"]
key=a.req("collection-scoped viewer key","POST","/api/keys",token,{"name":"OSS collection gate","role":"viewer","user_id":s["viewer"],"scope":{"workspaces":[s["workspace"]],"collections":[s["collection"]]}})
a.known.append(key["key"])
try:
    a.req("HTTP collection scope denial","POST","/api/rag/query",key["key"],{"workspace_id":s["workspace"],"collection_ids":[col],"query":"test"},status=403)
    d=a.req("MCP collection scope denial","POST","/api/mcp/rpc",key["key"],{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_collection","arguments":{"workspace_id":s["workspace"],"collection_id":col,"query":"test"}}})
    a.check("MCP collection denial contract","error" in d or d.get("result",{}).get("isError",False),d,"denied")
finally:a.req("scope test key revoked","DELETE","/api/keys/"+key["id"],token)
assert all(r["status"]=="PASS" for r in a.rows)
