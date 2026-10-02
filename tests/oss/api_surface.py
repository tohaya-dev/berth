"""Record every OpenAPI operation and its unauthenticated contract."""
import json,re
import live_acceptance as a
response=a.requests.get(a.URL+"/openapi.json",timeout=30);response.raise_for_status()
schema=response.json();rows=[]
for path,item in schema["paths"].items():
    for method,operation in item.items():
        if method not in ("get","post","put","patch","delete"):continue
        url=re.sub(r"\{[^}]+\}","oss-nonexistent",path)
        try:
            result=a.requests.request(method,a.URL+url,json={} if method not in ("get","delete") else None,timeout=10)
            rows.append({"method":method.upper(),"path":path,"operation":operation.get("operationId"),
                         "unauthenticated_status":result.status_code,"declared_responses":list(operation.get("responses",{})),
                         "valid_authenticated_case":"NOT_EXERCISED_BY_THIS_SWEEP"})
        except a.requests.RequestException as exc:
            rows.append({"method":method.upper(),"path":path,"transport_error":type(exc).__name__})
(a.OUT/"api-surface.json").write_text(json.dumps(rows,indent=2))
print("Inventoried",len(rows),"operations")
print("5xx:",[(r["method"],r["path"],r.get("unauthenticated_status")) for r in rows if r.get("unauthenticated_status",0)>=500])
# A sweep is not full semantic coverage. Never label unexercised valid inputs PASS.
