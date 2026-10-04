# START HERE — Berth

This is the shortest path to a running Berth. The regular commands below are for the reference macOS Apple Silicon path. For Windows WSL2 / Ubuntu 24.04, use the [Linux / WSL2 guide](docs/oss/linux-wsl.md).

## 1. Install prerequisites

On macOS Apple Silicon, install Git, Podman, k3d, and kubectl. On Windows, prepare Windows 11, WSL2, Ubuntu 24.04, and Git, then follow the [Linux / WSL2 guide](docs/oss/linux-wsl.md).

## 2. Download

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
```

Without Git, choose **Code** → **Download ZIP** on GitHub, extract the ZIP, and open a terminal in the extracted `berth` folder.

## 3. Start

```bash
./ops/status.sh
./ops/start.sh
```

## 4. Open

Open `http://127.0.0.1:18765` in your browser.

## 5. Verify

```bash
./ops/verify.sh
```

Berth is ready when the command succeeds and the URL above opens in your browser.

## 6. Stop

```bash
./ops/stop.sh
```

## 7. Troubleshooting

- macOS: [Troubleshooting](docs/oss/troubleshooting.md)
- Windows / WSL2: [Linux / WSL2 guide](docs/oss/linux-wsl.md)
- Detailed startup and recovery: [STARTUP.md](STARTUP.md)
