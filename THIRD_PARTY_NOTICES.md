# Third-party notices and RC audit

Berth source retains its MIT LICENSE. This does not relicense bundled dependencies, operating-system packages, models, fonts or data.

| Component | Source | License/obligation |
|---|---|---|
| Python 3.12 / Debian slim | python.org / Debian | PSF and per-package Debian copyright files |
| PostgreSQL 16 | postgresql.org | PostgreSQL License |
| pgvector | github.com/pgvector/pgvector | PostgreSQL License |
| Redis server 7.2 | github.com/redis/redis | BSD-3-Clause; renderer selects 7.2 instead of floating 7 |
| redis Python client 8.1.0 | github.com/redis/redis-py | MIT |
| psycopg 3.3.5 | psycopg.org | LGPL-3.0; retain license/source availability and replacement rights |
| PyTorch CPU 2.14.0 | pytorch.org | BSD/Apache/MIT and bundled component notices |
| spaCy 3.8.16 | spacy.io | MIT |
| ChromaDB 1.5.9 | github.com/chroma-core/chroma | Apache-2.0; vulnerability triage below |
| BGE and spaCy model payloads | See docs/oss/model-policy.md | Separate model terms apply |
| K3s v1.35.5+k3s1 | github.com/k3s-io/k3s | Apache-2.0 and transitive notices |

The CycloneDX inventory and Trivy vulnerability report are generated from the actual locally built amd64 image under `_oss-rc-artifacts`. Package freeze is in locks/requirements-linux-amd64.txt. These are inventory evidence, not a blanket legal clearance. Missing/ambiguous license metadata, copied fixture content and transitive assets require review before public binary redistribution. The RC image is retained locally until that review is complete.

## Vendored browser libraries

The source artifact includes Chart.js 4.5.1, chartjs-plugin-annotation 3.1.0, chartjs-plugin-datalabels 2.2.0 and marked 9.1.6. Their retained source headers identify MIT licensing. Full license texts from those exact upstream tags are in docs/oss/licenses. No font or generative model payload is added.

## Critical vulnerability triage

The initial image scan found 3 Critical findings. CVE-2026-43185 refers to kernel ksmbd code via linux-libc-dev metadata; image headers are not the host kernel. Unneeded development headers were removed in the locked rebuild. This does not certify the Windows/WSL host kernel.

CVE-2026-45829 and CVE-2026-45833 affect ChromaDB server collection endpoints accepting model code. This deployment runs Berth with PostgreSQL/pgvector and does not launch a Chroma HTTP server. The Chroma Python dependency remains for existing embedding/legacy integration. No upstream fixed version was reported by the audit database. Do not expose a Chroma server from this image or interpret absence of that route as removal of the vulnerable dependency.

References: [CVE-2026-45829 advisory](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c), [CVE-2026-45833 advisory](https://github.com/advisories/GHSA-36p7-vc44-83pf). High/Medium findings also remain in the full report; public binary release requires further triage and remediation.

The Linux lock was subsequently updated to FastAPI 0.141.1, Starlette 1.3.1, prometheus-fastapi-instrumentator 8.1.0, MCP 1.28.1, setuptools 84.0.0 and wheel 0.48.0. The upgrade probe passed the scratch RAG/RBAC/CLI/MCP acceptance, including the metrics middleware that had blocked older unpinned combinations. The Mac requirements remain unchanged.

A clean resolver rejected cryptography 50.0.0 because presidio-anonymizer 2.2.364 requires cryptography <49. Linux therefore retains compatible 48.0.1 and records its outstanding advisories. The runtime probe alone was insufficient; clean installation and pip check are required gates. No masking dependency was removed.

The installed Python metadata audit found 179 distributions, all with a license declaration, classifier or bundled license file. docs/oss/dependency-licenses.csv records this inventory, including the compatible cryptography version. This resolves missing top-level Syft license fields as metadata gaps; it does not suppress vulnerability findings or replace bundled notices.
