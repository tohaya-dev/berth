"""Run only in the dedicated lab after enabling the administrative MCP gate."""
import json
import live_acceptance as a
a.save=lambda:(a.OUT/"mcp-double-gate.json").write_text(json.dumps(a.rows,indent=2))
token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
key=a.req("create disposable gate key","POST","/api/keys",token,{"name":"OSS gate","role":"viewer"})
a.known.append(key["key"])
for confirm in (False,True):
    result=a.req("gate enabled confirm="+str(confirm),"POST","/api/mcp/rpc",token,
        {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"manage_keys","arguments":{"action":"revoke","key_id":key["id"],"confirm":confirm}}})
    denied="error" in result or result.get("result",{}).get("isError",False)
    a.check("double gate "+str(confirm),denied!=confirm,{"denied":denied},"denied" if not confirm else "allowed")
    a.req("key state after "+str(confirm),"GET","/api/auth/me",key["key"],status=401 if confirm else 200)
assert all(r["status"]=="PASS" for r in a.rows)
