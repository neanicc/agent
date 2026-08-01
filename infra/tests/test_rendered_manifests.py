from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra" / "helm" / "loopguard"
TEMPLATES = CHART / "templates"
MODULE = ROOT / "infra" / "terraform" / "modules" / "control-plane"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _template(name: str) -> str:
    return _text(TEMPLATES / name)


def _workload_templates() -> list[str]:
    return [
        _template("control-api.yaml"),
        _template("web.yaml"),
        _template("worker.yaml"),
        _template("migration-job.yaml"),
        _template("canary.yaml"),
    ]


def test_workloads_are_non_root_and_resource_bounded() -> None:
    helpers = _template("_helpers.tpl")
    assert "runAsNonRoot: true" in helpers
    assert "allowPrivilegeEscalation: false" in helpers
    assert "readOnlyRootFilesystem: true" in helpers
    assert 'drop: ["ALL"]' in helpers
    assert "type: RuntimeDefault" in helpers
    for source in _workload_templates():
        assert 'include "loopguard.podSecurityContext"' in source
        assert 'include "loopguard.containerSecurityContext"' in source
        assert "resources:" in source
        assert "requests:" in _text(CHART / "values.yaml")
        assert "limits:" in _text(CHART / "values.yaml")
        assert "hostPath:" not in source
        assert "docker.sock" not in source


def test_database_is_private_and_backups_enabled() -> None:
    source = _text(MODULE / "main.tf")
    assert re.search(r"publicly_accessible\s*=\s*false", source)
    assert re.search(r"storage_encrypted\s*=\s*true", source)
    assert "backup_retention_period       = var.database_backup_retention_days" in source
    variables = _text(MODULE / "variables.tf")
    assert "var.database_backup_retention_days >= 7" in variables
    assert "source_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id" in source


def test_production_uses_signed_digest_not_latest() -> None:
    values = yaml.safe_load(_text(CHART / "values.yaml"))
    for image in values["images"].values():
        assert DIGEST.fullmatch(image["digest"])
        assert ":latest" not in image["repository"]
    helper = _template("_helpers.tpl")
    assert 'printf "%s@%s"' in helper
    assert 'fail "all LoopGuard images must use a sha256 digest"' in helper


def test_repair_worker_has_a_strong_isolation_boundary() -> None:
    source = _template("worker.yaml")
    assert "runtimeClassName:" in source
    assert "automountServiceAccountToken: false" in source
    assert "workload: repair" in source
    assert "loopguard.dev/repair" in source
    assert "pod-security.kubernetes.io/enforce: restricted" in source
    network = _template("network-policy.yaml")
    assert "repair-default-deny" in network
    assert "podSelector: {}" in network
    assert "169.254.169.254" not in network


def test_chart_has_availability_and_safe_rollout_controls() -> None:
    joined = "\n".join(_workload_templates())
    assert "kind: PodDisruptionBudget" in joined
    assert "kind: HorizontalPodAutoscaler" in joined
    assert "topologySpreadConstraints:" in joined
    assert "readinessProbe:" in joined
    assert "livenessProbe:" in joined
    migration = _template("migration-job.yaml")
    assert "pre-install,pre-upgrade" in migration
    assert "activeDeadlineSeconds:" in migration
    assert "backoffLimit: 1" in migration


def test_cloud_resources_are_encrypted_private_and_recoverable() -> None:
    source = _text(MODULE / "main.tf")
    required = (
        'endpoint_public_access  = false',
        'block_public_acls       = true',
        'versioning_configuration',
        'enable_key_rotation     = true',
        'image_tag_mutability = "IMMUTABLE"',
        "aws_s3_bucket_replication_configuration",
        "aws_ecr_replication_configuration",
        "aws_route53_record\" \"failover_api",
        "database_replica_source_arn",
        "aws_eks_pod_identity_association",
    )
    for fragment in required:
        assert fragment in source


def test_deploy_workflow_is_oidc_digest_only_canary_and_rollback() -> None:
    source = _text(ROOT / ".github" / "workflows" / "deploy.yml")
    assert "id-token: write" in source
    assert "AWS_ACCESS_KEY_ID" not in source
    assert "@sha256:[0-9a-f]{64}" in source
    assert "cosign verify" in source
    assert "canary.enabled=true" in source
    assert "helm rollback" in source
    assert "MIGRATION_REHEARSAL_DATABASE_URL" in source
    assert "StateValue" in source
    for line in source.splitlines():
        if re.match(r"\s*- uses:", line):
            assert re.search(r"@[0-9a-f]{40}(?:\s|$)", line), line


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm CLI is not installed")
@pytest.mark.parametrize("values_name", ["values-staging.yaml", "values-production.yaml"])
def test_chart_lints_and_renders_when_helm_is_available(values_name: str) -> None:
    subprocess.run(["helm", "lint", str(CHART)], check=True, timeout=60)
    result = subprocess.run(
        [
            "helm",
            "template",
            "loopguard",
            str(CHART),
            "-f",
            str(CHART / values_name),
            "--set-string",
            "global.buildSha=" + "d" * 40,
            "--set-string",
            "global.certificateArn=arn:aws:acm:us-east-1:000000000000:certificate/test",
        ],
        check=True,
        timeout=60,
        stdout=subprocess.PIPE,
        text=True,
    )
    documents = [value for value in yaml.safe_load_all(result.stdout) if value]
    assert documents
    images = [
        container["image"]
        for document in documents
        if document.get("kind") in {"Deployment", "Job"}
        for container in document["spec"]["template"]["spec"]["containers"]
    ]
    assert images and all("@sha256:" in image and ":latest" not in image for image in images)
