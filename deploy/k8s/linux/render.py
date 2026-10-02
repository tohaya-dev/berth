"""Render the existing Berth Kubernetes contract for an explicit Linux node."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import re
import secrets
import yaml

REPO = Path(__file__).resolve().parents[3]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--namespace",required=True)
    p.add_argument("--data",type=Path,required=True)
    p.add_argument("--image",required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?",a.namespace):
        p.error("invalid namespace")
    data=a.data.expanduser().resolve()
    if str(data).startswith("/mnt/"):
        p.error("runtime data must be on the Linux filesystem")
    os.umask(0o077)
    a.output.mkdir(parents=True,exist_ok=True)
    for name in ("models","ingest","secrets"):
        (data/name).mkdir(parents=True,exist_ok=True)
    # The pods run as a fixed non-root uid (10001, see deploy/container/Containerfile) and mount models/ and
    # ingest/ as read-only hostPath volumes, which fsGroup cannot re-own. The directories themselves must be
    # traversable by that uid; files placed inside must be world-readable (o+r) by whoever adds them.
    for name in ("models","ingest"):
        (data/name).chmod(0o755)
    values={}
    for name in ("pg_password","admin_password","secret_key"):
        file=data/"secrets"/name
        if not file.exists():
            if name=="secret_key":
                from cryptography.fernet import Fernet
                value=Fernet.generate_key().decode()
            else: value=secrets.token_urlsafe(32)
            file.write_text(value);file.chmod(0o600)
        values[name]=file.read_text().strip()
    config=yaml.safe_load((REPO/"cynovela.yaml").read_text())
    config["database"]={"backend":"postgres","postgres":{"host":"cynovela-pgvector","port":5432,
        "dbname":"cynovela","user":"cynovela","password_file":"/run/hansolo-secrets/pg_password","pool_max":10}}
    config["vector_store"]["provider"]="pgvector"
    config["queue"].update(enabled=True,url="redis://cynovela-redis:6379/0")
    config["worker"]={"embedding_mode":"minimal"}
    config["masking"]["parallelism"]=1
    config["auth"]={"admin_initial_password":"","viewer_initial_password":""}
    config["llm"].update(provider="lmstudio",base_url="http://127.0.0.1:9/v1",model="")
    config["execution"]["llm_base_url"]="http://127.0.0.1:9/v1"
    objects=[
      {"apiVersion":"v1","kind":"Namespace","metadata":{"name":a.namespace}},
      {"apiVersion":"v1","kind":"Secret","metadata":{"name":"cynovela-secret","namespace":a.namespace},
       "type":"Opaque","stringData":values},
      {"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"cynovela-config","namespace":a.namespace},
       "data":{"cynovela.yaml":yaml.safe_dump(config,allow_unicode=True,sort_keys=False)}}
    ]
    for name in ("50-pgvector.yaml","40-redis.yaml"):
        for obj in yaml.safe_load_all((REPO/"deploy/k8s/phase2"/name).read_text()):
            obj["metadata"]["namespace"]=a.namespace
            if obj["kind"]=="Deployment":
                c=obj["spec"]["template"]["spec"]["containers"][0]
                if name.startswith("50"):
                    for env in c["env"]:
                        if env["name"]=="POSTGRES_PASSWORD":
                            env.pop("value",None)
                            env["valueFrom"]={"secretKeyRef":{"name":"cynovela-secret","key":"pg_password"}}
                else:
                    c["image"]="docker.io/library/redis:7.2-alpine"
            objects.append(obj)
    (a.output/"infrastructure.yaml").write_text(yaml.safe_dump_all(objects,sort_keys=False))
    workloads=[]
    for filename in ("20-api-deployment.yaml","70-worker-deployment.yaml"):
        obj=yaml.safe_load((REPO/"deploy/k8s/phase4a"/filename).read_text())
        obj["metadata"]["namespace"]=a.namespace
        pod=obj["spec"]["template"]["spec"]
        pod["automountServiceAccountToken"]=False
        container=pod["containers"][0]
        container["image"]=a.image
        container["imagePullPolicy"]="IfNotPresent"
        if filename.startswith("20"):
            container["command"][-1]=container["command"][-1].replace(" --demo","")
            container["readinessProbe"]["httpGet"]["path"]="/api/ready"
            container["livenessProbe"]["httpGet"]["path"]="/api/health"
            container["startupProbe"]={"httpGet":{"path":"/api/health","port":8765},"periodSeconds":10,"failureThreshold":90}
        container["env"].append({"name":"CYNOVELA_IMAGE_TAG","value":a.image})
        container["volumeMounts"].append({"name":"db-secret","mountPath":"/run/hansolo-secrets","readOnly":True})
        pod["volumes"].append({"name":"db-secret","secret":{"secretName":"cynovela-secret","items":[{"key":"pg_password","path":"pg_password"}]}})
        for volume in pod["volumes"]:
            if volume["name"] in ("models","ingest"):
                volume["hostPath"]["path"]=str(data/volume["name"])
        workloads.append(obj)
    service=yaml.safe_load((REPO/"deploy/k8s/30-service.yaml").read_text())
    service["metadata"]["namespace"]=a.namespace
    service["spec"]["type"]="ClusterIP"
    for port in service["spec"]["ports"]:port.pop("nodePort",None)
    workloads.append(service)
    (a.output/"workloads.yaml").write_text(yaml.safe_dump_all(workloads,sort_keys=False))
    base=copy.deepcopy(workloads[0]["spec"]["template"]["spec"])
    base.pop("topologySpreadConstraints",None)
    c=base["containers"][0]
    for field in ("ports","readinessProbe","livenessProbe","startupProbe"):c.pop(field,None)
    c["command"]=["python","tools/bootstrap_postgres.py"]
    c["env"].append({"name":"CYNOVELA_ADMIN_INITIAL_PASSWORD","valueFrom":{"secretKeyRef":{"name":"cynovela-secret","key":"admin_password"}}})
    base["restartPolicy"]="Never"
    job={"apiVersion":"batch/v1","kind":"Job","metadata":{"name":"hansolo-bootstrap","namespace":a.namespace},
         "spec":{"backoffLimit":0,"template":{"spec":base}}}
    (a.output/"bootstrap.yaml").write_text(yaml.safe_dump(job,sort_keys=False))
    print("Rendered infrastructure, bootstrap and two-replica workloads; secrets kept private")

if __name__=="__main__":main()
