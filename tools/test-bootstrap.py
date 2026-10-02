#!/usr/bin/env python3
"""Preflight and run Berth tests without managing a production runtime."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FOCUSED_TESTS = ("tests/oss",)
DEPENDENCIES = {
    "focused": (("pytest", "pytest"),),
    "full": (
        ("pytest", "pytest"),
        ("yaml", "PyYAML"),
        ("freezegun", "freezegun"),
        ("hypothesis", "hypothesis"),
        ("opentelemetry.sdk", "opentelemetry-sdk"),
        ("playwright", "playwright"),
        ("schemathesis", "schemathesis"),
        ("syrupy", "syrupy"),
        ("pytest_rerunfailures", "pytest-rerunfailures"),
    ),
}


def missing_dependencies(mode: str) -> list[str]:
    missing: list[str] = []
    for module, package in DEPENDENCIES[mode]:
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            available = False
        if not available:
            missing.append(package)
    return missing


def candidate_service_ready(base_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    health_url = urljoin(base_url.rstrip("/") + "/", "api/health")
    try:
        request = Request(health_url, headers={"User-Agent": "berth-test-bootstrap/2"})
        with urlopen(request, timeout=timeout) as response:
            code = response.getcode()
        return (200 <= code < 300, f"{health_url} returned HTTP {code}")
    except HTTPError as exc:
        return (False, f"{health_url} returned HTTP {exc.code}")
    except (OSError, URLError) as exc:
        return (False, f"{health_url} is unavailable: {exc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("focused", "full"))
    parser.add_argument("--base-url", help="isolated candidate URL (required explicitly by full mode)")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--install",
        action="store_true",
        help="install requirements.txt and the pyproject test extra into the current isolated Python environment",
    )
    parser.add_argument("--pytest-arg", action="append", default=[])
    return parser.parse_args(argv)


def install_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    test_dependencies = project["project"]["optional-dependencies"]["test"]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(ROOT / "requirements.txt"),
        *test_dependencies,
    ]
    subprocess.check_call(command, cwd=ROOT)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.install:
        install_dependencies()
    failures: list[str] = []
    if sys.version_info < (3, 12):
        failures.append(f"TEST_ENV_MISSING_DEPENDENCY: Python 3.12+ required; found {sys.version.split()[0]}")
    missing = missing_dependencies(args.mode)
    if missing:
        failures.append("TEST_ENV_MISSING_DEPENDENCY: " + ", ".join(missing))
    if args.mode == "full":
        if not args.base_url:
            failures.append("TEST_SERVICE_NOT_RUNNING: full mode requires explicit --base-url for an isolated candidate")
        else:
            ready, detail = candidate_service_ready(args.base_url)
            if ready:
                print("TEST_SERVICE_READY: " + detail)
            else:
                failures.append("TEST_SERVICE_NOT_RUNNING: " + detail)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        print("Install into an isolated venv with: python tools/test-bootstrap.py focused --install", file=sys.stderr)
        return 2
    print(f"TEST_ENV_READY: mode={args.mode} python={sys.version.split()[0]}")
    if args.check_only:
        return 0
    targets = list(FOCUSED_TESTS) if args.mode == "focused" else ["tests"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if args.mode == "full":
        assert args.base_url is not None
        env["CYNOVELA_BASE"] = args.base_url.rstrip("/")
        env["CYNOVELA_BASE_URL"] = args.base_url.rstrip("/")
    command = [sys.executable, "-m", "pytest", "-q", *targets, *args.pytest_arg]
    print("RUN: " + " ".join(command))
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
