import json,os,subprocess,sys,tempfile
import live_acceptance as a
a.save=lambda:(a.OUT/"cli-keys.json").write_text(json.dumps(a.rows,indent=2))
token=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
s=json.loads(a.STATE.read_text())
with tempfile.TemporaryDirectory() as home:
    env=dict(os.environ,HOME=home,CYNOVELA_URL=a.URL,CYNOVELA_TOKEN=token)
    command=[sys.executable,str(a.ROOT/"cynovela_cli.py"),"--json"]
    r=subprocess.run(command+["key","issue","--name","OSS CLI key","--role","viewer","--scope-workspace",s["workspace"],"--scope-collection",s["collection"]],env=env,capture_output=True,text=True,timeout=30)
    d=json.loads(r.stdout);a.known.append(d.get("key",""))
    a.check("CLI issue key",r.returncode==0 and bool(d.get("key")),d,"issued")
    a.req("CLI key usable","GET","/api/auth/me",d["key"])
    r=subprocess.run(command+["key","revoke","--id",d["id"],"--yes"],env=env,capture_output=True,text=True,timeout=30)
    a.check("CLI revoke key",r.returncode==0,{"exit":r.returncode},"revoked")
    a.req("CLI revoked key denied","GET","/api/auth/me",d["key"],status=401)
    r=subprocess.run(command+["login","--username","cynovela","--password","synthetic-invalid-password"],env=env,capture_output=True,text=True,timeout=30)
    a.check("CLI invalid credentials",r.returncode!=0,{"exit":r.returncode},"nonzero")
assert all(r["status"]=="PASS" for r in a.rows)
