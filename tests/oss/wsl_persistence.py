"""Capture/compare lab state around an externally controlled dedicated WSL restart."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/"_oss-rc-artifacts"
DATA=Path(os.environ["HAN_SOLO_DATA_DIR"])
assert os.environ["HAN_SOLO_CONTEXT"]=="hansolo-wsl-oss-rc"
def capture():
    command=["kubectl","--context","hansolo-wsl-oss-rc","-n","hansolo-oss-rc","exec","-i","deploy/cynovela-pgvector","--","psql","-U","cynovela","-d","cynovela","-At"]
    db=json.loads(subprocess.check_output(command,input=(OUT/"snapshot.sql").read_text(),text=True,timeout=30))
    invocation=subprocess.check_output(["systemctl","show","k3s-hansolo-wsl-oss-rc","-p","InvocationID","--value"],text=True).strip()
    return {"database":db,"service_invocation":invocation,
            "local_config":hashlib.sha256((DATA/"rendered/infrastructure.yaml").read_bytes()).hexdigest(),
            "encryption_key":hashlib.sha256((DATA/"secrets/secret_key").read_bytes()).hexdigest()}
if sys.argv[1]=="before":
    (OUT/"wsl-before.json").write_text(json.dumps(capture(),indent=2))
else:
    before=json.loads((OUT/"wsl-before.json").read_text());after=capture()
    checks={k:before[k]==after[k] for k in ("database","local_config","encryption_key")}
    checks["service_restarted"]=bool(after["service_invocation"]) and before["service_invocation"]!=after["service_invocation"]
    (OUT/"wsl-persistence.json").write_text(json.dumps({"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"database":after["database"]},indent=2))
    print(checks);assert all(checks.values())
