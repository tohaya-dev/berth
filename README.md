# Berth

Berth is the Kubernetes-native Cynovela runtime.
It provides a local Kubernetes-based runtime for RAG, guardrails, provider switching, API workers, PostgreSQL + pgvector, Redis, CLI, and HTTP MCP.

Berth does not bundle the answer-generation LLM.
Use LM Studio, Ollama, or another OpenAI-compatible endpoint as an external provider.

Berth `v1.0.0-ga` is the current public GA line.
**Reference path:** macOS Apple Silicon + Podman + k3d.
**Documented Windows/Linux path:** Windows WSL2 + Ubuntu 24.04.
**Runtime contract:** Kubernetes / Linux workloads.

## Start here

New users should follow this path.

### 1. Install prerequisites

**macOS Apple Silicon:** Git, Podman, k3d, and kubectl.
**Windows:** Windows 11, WSL2, Ubuntu 24.04, and Git. Follow the [Linux / WSL2 guide](docs/oss/linux-wsl.md) before starting.

### 2. Download Berth

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
```

If you do not use Git, open the GitHub page, press **Code**, then **Download ZIP**. After extracting the ZIP, open a terminal in the extracted `berth` folder.

### 3. Start Berth
```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

### 4. Open Berth
Open this URL in your browser: `http://127.0.0.1:18765`

`status.sh` shows the current state, `start.sh` starts Berth, and `verify.sh` checks it.

### 5. Confirm that it works

`./ops/verify.sh` should finish successfully. You should also be able to open `http://127.0.0.1:18765` in a browser. If either check fails, see [Troubleshooting](docs/oss/troubleshooting.md).

### 6. Stop Berth

```bash
./ops/stop.sh
```

### 7. More guides

- [Japanese README](README.ja.md)
- [Start Here](START-HERE.en.md)
- [日本語の Start Here](START-HERE.md)
- [Linux / WSL2 guide](docs/oss/linux-wsl.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## What Berth runs

Berth keeps the runtime services in Kubernetes:

- API and worker replicas
- PostgreSQL with pgvector
- Redis
- RAG and guardrail processing
- provider switching
- CLI and HTTP MCP access

The answer-generation model stays outside Berth. Configure LM Studio, Ollama, or another OpenAI-compatible endpoint after the runtime is available.

## Supported entry points

Run the supported scripts from the repository root:

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
./ops/restart.sh
./ops/stop.sh
```

The commands above are the reference macOS local path. Windows WSL2 / Ubuntu 24.04 users should use the environment and commands documented in the [Linux / WSL2 guide](docs/oss/linux-wsl.md).

## Documentation

- [Startup and recovery](STARTUP.md)
- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)
- [CLI and MCP](docs/CLI-MCP.md)
- [System requirements](docs/oss/requirements.md)
- [Troubleshooting](docs/oss/troubleshooting.md)
- [Update and uninstall](docs/oss/uninstall-upgrade.md)
- [GA release notes](docs/release-notes-v1.0.0-ga.md)

## License

Berth source code is released under the MIT License. Dependencies, models, and container base images have their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
