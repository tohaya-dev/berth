# Berth

Berth is the Kubernetes-based runtime for Cynovela. It runs RAG, guardrails, provider switching, API workers, PostgreSQL + pgvector, Redis, CLI, and HTTP MCP on a local Kubernetes environment.

Berth does not bundle the answer-generation LLM. Connect LM Studio, Ollama, or another OpenAI-compatible API as an external provider.

Berth `v1.0.0-ga` is the current public GA line. Its runtime contract is Kubernetes / Linux workloads.

## Quick start

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

Open `http://127.0.0.1:18765` in your browser. Berth is ready when `./ops/verify.sh` succeeds and the URL opens.

## System requirements

- macOS Apple Silicon + Podman + k3d
- or Windows / WSL2 Ubuntu 24.04
- or Windows / WSL2 Rocky 9
- kubectl
- local Kubernetes environment

The macOS path above is the reference local path. Windows users should follow the [Linux / WSL2 guide](docs/oss/linux-wsl.md).

If you do not use Git, choose **Code** → **Download ZIP** on GitHub, extract the ZIP, and open a terminal in the extracted `berth` folder.

## Stop Berth

```bash
./ops/stop.sh
```

## What Berth runs

- API and worker replicas
- PostgreSQL with pgvector
- Redis
- RAG and guardrail processing
- provider switching
- CLI and HTTP MCP access

The answer-generation model stays outside Berth. Configure LM Studio, Ollama, or another OpenAI-compatible API after the runtime starts.

## More guides

- [Japanese README](README.ja.md)
- [Start Here](START-HERE.en.md)
- [日本語の Start Here](START-HERE.md)
- [Linux / WSL2 guide](docs/oss/linux-wsl.md)
- [Startup and recovery](STARTUP.md)
- [Operations](docs/operations.md)
- [CLI and MCP](docs/CLI-MCP.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Berth source code is released under the MIT License. Dependencies, models, and container base images have their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
