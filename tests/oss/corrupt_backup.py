"""Reject corrupt dumps, absent checksums, and mismatched encryption keys."""
import hashlib,json,os,pathlib,shutil,subprocess,tempfile
root=pathlib.Path(__file__).resolve().parents[2]
original=sorted((root/"_oss-rc-backups").glob("*.dump"))[-1]
rows=[]
with tempfile.TemporaryDirectory(prefix="hansolo-corrupt-") as d:
    dump=pathlib.Path(d)/original.name
    side=dump.with_suffix(".sha256")
    key=original.parent/("secret.key-"+original.stem.removeprefix("pgdump-"))
    paired=dump.parent/key.name
    shutil.copy(key,paired)
    env=dict(os.environ,NS="hansolo-oss-rc",HAN_SOLO_SECRET_FILE=str(paired))
    for kind in ("bad-checksum","truncated-valid-checksum","invalid-format","missing-sidecar","wrong-key"):
        shutil.copy(original,dump);shutil.copy(original.with_suffix(".sha256"),side)
        if kind=="bad-checksum":dump.write_bytes(dump.read_bytes()[:50])
        if kind=="truncated-valid-checksum":dump.write_bytes(dump.read_bytes()[:100])
        if kind=="invalid-format":dump.write_bytes(b"not a postgres dump")
        if kind in ("truncated-valid-checksum","invalid-format"):
            side.write_text(hashlib.sha256(dump.read_bytes()).hexdigest()+"  "+dump.name+"\n")
        if kind=="missing-sidecar":side.unlink()
        args=["verify",str(dump)]
        if kind=="wrong-key":
            wrong=dump.parent/"wrong";wrong.write_text("synthetic-wrong-key")
            env["HAN_SOLO_SECRET_FILE"]=str(wrong)
            args=["restore",str(dump),"--yes"]
        result=subprocess.run([str(root/"deploy/k8s/pg-backup.sh"),*args],env=env,capture_output=True,text=True,timeout=90)
        rows.append({"test":kind,"status":"PASS" if result.returncode!=0 else "FAIL","exit":result.returncode})
(root/"_oss-rc-artifacts/corrupt-backup.json").write_text(json.dumps(rows,indent=2))
print(json.dumps(rows))
assert all(r["status"]=="PASS" for r in rows)
