import pathlib,subprocess,sys,tempfile,unittest
try:import yaml
except ImportError:yaml=None
ROOT=pathlib.Path(__file__).resolve().parents[2]
@unittest.skipUnless(yaml,"PyYAML required for rendering tests")
class Renderer(unittest.TestCase):
    def test_fresh_contract_and_secret_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            data=pathlib.Path(d)/"data";out=pathlib.Path(d)/"render"
            command=[sys.executable,str(ROOT/"deploy/k8s/linux/render.py"),"--namespace","hansolo-test","--data",str(data),"--output",str(out),"--image","test:amd64"]
            subprocess.run(command,check=True,capture_output=True)
            secrets={p.name:p.read_bytes() for p in (data/"secrets").iterdir()}
            subprocess.run(command,check=True,capture_output=True)
            self.assertEqual(secrets,{p.name:p.read_bytes() for p in (data/"secrets").iterdir()})
            items=list(yaml.safe_load_all((out/"workloads.yaml").read_text()))
            deployments=[x for x in items if x["kind"]=="Deployment"]
            self.assertEqual([x["spec"]["replicas"] for x in deployments],[2,2])
            for item in deployments:
                pod=item["spec"]["template"]["spec"]
                self.assertFalse(pod["automountServiceAccountToken"])
                self.assertEqual(pod["containers"][0]["image"],"test:amd64")
            infra=list(yaml.safe_load_all((out/"infrastructure.yaml").read_text()))
            config=yaml.safe_load(next(x for x in infra if x["kind"]=="ConfigMap")["data"]["cynovela.yaml"])
            self.assertEqual(config["database"]["backend"],"postgres")
            self.assertEqual(config["vector_store"]["provider"],"pgvector")
            self.assertTrue(config["queue"]["enabled"])
            self.assertEqual(config["auth"]["admin_initial_password"],"")
