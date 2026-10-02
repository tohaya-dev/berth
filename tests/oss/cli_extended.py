"""Additional command families against synthetic lab data."""
import json,os,subprocess,sys,tempfile
import live_acceptance as a
s=json.loads(a.STATE.read_text())
a.save=lambda:(a.OUT/"cli-extended.json").write_text(json.dumps(a.rows,indent=2))
admin=a.login("cynovela",(a.DATA/"secrets/admin_password").read_text().strip())
with tempfile.TemporaryDirectory(prefix="hansolo-cli-") as home:
    env=dict(os.environ,HOME=home,CYNOVELA_URL=a.URL,CYNOVELA_TOKEN=admin)
    cli=[sys.executable,str(a.ROOT/"cynovela_cli.py"),"--json"]
    def run(args,expected=0,extra=None):
        p=subprocess.run(cli+args,env=extra or env,capture_output=True,text=True,timeout=120)
        a.check("CLI "+" ".join(args[:2]),p.returncode==expected,{"exit":p.returncode,"output":p.stdout[:1000],"error":p.stderr[:500]},expected)
        return p
    for args in (
        ["ingest-status","--source",s["source"]],["index-status","--workspace",s["workspace"]],["key","list"],["overview","--section","all"],
        ["users","list"],["backup","list"],["cleanup","archived-list"],["audit","logs"],["sessions","list"],
        ["policy","list"],["report","list"],["doctor"],["scan","status","--source",s["source"]],
        ["publish","status","--collection",s["collection"]],["link-files","--collection",s["collection"]]):
        run(args)
    password=(a.DATA/"secrets/admin_password").read_text().strip();a.known.append(password)
    loginenv=dict(env);loginenv.pop("CYNOVELA_TOKEN",None)
    p=run(["login","--username","cynovela","--password",password],extra=loginenv)
    credentials=__import__("pathlib").Path(home)/".cynovela_cli.env"
    a.check("CLI token file private",credentials.exists() and credentials.stat().st_mode & 0o777==0o600,
            {"exists":credentials.exists()},"mode600")
    run(["logout"],extra=loginenv)
    a.check("CLI logout removes token","CYNOVELA_TOKEN=" not in credentials.read_text(),{"token_line": "CYNOVELA_TOKEN=" in credentials.read_text()},"token removed; URL retained")
    run(["restart"],expected=2)
    run(["restart","--yes","--times","1"])
assert all(r["status"]=="PASS" for r in a.rows)
