import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "tools/test-bootstrap.py"
    spec = importlib.util.spec_from_file_location("berth_test_bootstrap", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_focused_gate_is_oss_source_only():
    module = _module()
    assert module.FOCUSED_TESTS == ("tests/oss",)
    assert module.missing_dependencies("focused") == []


def test_full_gate_declares_repo_test_extras():
    module = _module()
    packages = {package for _, package in module.DEPENDENCIES["full"]}
    assert {"hypothesis", "syrupy", "freezegun", "playwright", "schemathesis", "pytest-rerunfailures"} <= packages


def test_installer_reads_test_extra_without_editable_package_install():
    source = (ROOT / "tools/test-bootstrap.py").read_text()
    assert '["optional-dependencies"]["test"]' in source
    assert '"-e"' not in source
