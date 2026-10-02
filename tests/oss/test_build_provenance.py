from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_containerfile_declares_oci_provenance_labels():
    text = (ROOT / "deploy/container/Containerfile").read_text()
    for label in (
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
        "org.opencontainers.image.created",
        "org.opencontainers.image.version",
    ):
        assert label in text


def test_run_container_supplies_provenance_build_args():
    text = (ROOT / "deploy/container/run-container.sh").read_text()
    for build_arg in (
        "BERTH_SOURCE_REVISION",
        "BERTH_SOURCE_REPOSITORY",
        "BERTH_BUILD_TIME",
        "BERTH_VERSION",
    ):
        assert f'--build-arg "{build_arg}=' in text


def test_standalone_volume_names_are_overridable_for_isolation():
    text = (ROOT / "deploy/container/run-container.sh").read_text()
    for variable in ("DB_VOLUME", "VECTOR_VOLUME", "BACKUP_VOLUME"):
        assert f'{variable}="${{{variable}:-' in text
        assert f'"${variable}:' in text
