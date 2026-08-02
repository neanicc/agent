# Hosted deployment

LoopGuard's reference hosted topology is a private, multi-AZ EKS control plane with PostgreSQL,
encrypted artifact storage, immutable ECR repositories, and a separately applied warm DR region.
Terraform prepares AWS infrastructure; Helm describes workloads; the deploy workflow accepts only
signed image digests. None of these files applies infrastructure automatically.

## What is and is not managed

Terraform manages the VPC, two public and two private subnets, one NAT gateway per availability
zone, private EKS API endpoint, dedicated system and repair node groups, RDS, S3/KMS, ECR, ACM,
Route 53 records, EKS audit logs, Pod Identity associations, and the controller IAM role.

These remain explicit external dependencies:

- a Temporal Cloud namespace and API key;
- the tenant OIDC/enterprise identity provider;
- APNs and billing-provider credentials;
- an External Secrets operator (or equivalent) that creates `loopguard-runtime` in both
  `loopguard` and `loopguard-repair`;
- the AWS Load Balancer Controller in `kube-system`, using the Terraform-created service account
  identity;
- a `gvisor` RuntimeClass on the isolated repair nodes;
- CloudWatch SLO alarms and Route 53 health checks;
- private network access for the deployment runner;
- human security, operations, privacy, product, support, and legal approvals.

Terraform receives secret ARNs, never secret values. The Helm chart only references Kubernetes
Secrets. Do not put credentials in values files, Terraform variables, GitHub variables, or image
build arguments.

## AWS account and state boundaries

Use separate AWS accounts for staging and production. Production DR can share the production
account only if the organization's threat model accepts that failure domain; a separate account is
preferred. Each environment needs a unique encrypted Terraform state bucket and DynamoDB lock
table. Copy the matching `backend.hcl.example`, fill only identifiers, and keep the resulting file
outside source control.

The AWS platform owner must create:

1. a human-controlled platform administrator role passed as `cluster_admin_role_arn`;
2. a narrowly scoped GitHub OIDC deployment role restricted to this repository and the matching
   GitHub Environment claim;
3. remote state and lock resources;
4. a private/self-hosted runner route to the private EKS endpoint;
5. secret records and the operator that syncs them into both namespaces;
6. production environment protection rules with required reviewers.

`bootstrap_cluster_creator_admin_permissions` is disabled. If the administrator role is wrong,
there is intentionally no implicit creator back door.

## Validate before an apply

Use the exact Terraform and provider versions in the module:

```bash
terraform fmt -check -recursive infra/terraform
terraform -chdir=infra/terraform/environments/staging init -backend=false
terraform -chdir=infra/terraform/environments/staging validate
terraform -chdir=infra/terraform/environments/production init -backend=false
terraform -chdir=infra/terraform/environments/production validate
terraform -chdir=infra/terraform/environments/production-dr init -backend=false
terraform -chdir=infra/terraform/environments/production-dr validate
helm lint infra/helm/loopguard
helm template loopguard infra/helm/loopguard \
  -f infra/helm/loopguard/values-staging.yaml \
  --set-string global.buildSha=0000000000000000000000000000000000000000 \
  --set-string global.certificateArn=arn:aws:acm:us-east-1:000000000000:certificate/validation
services/control-api/.venv/bin/python -m pytest -q infra/tests/test_rendered_manifests.py
```

Validation is read-only. A plan or apply still requires a platform-owner review. Verify each pinned
EKS add-on version with `aws eks describe-addon-versions` in the target region before the apply;
AWS availability can vary by region.

## Bootstrap sequence

The first deployment is deliberately two-phase because an ALB hostname does not exist before the
chart creates its ingress:

1. Apply the regional Terraform environment with `ingress_hostname=""`.
2. Install the AWS Load Balancer Controller and gVisor runtime, then verify both.
3. Create the two namespace-local runtime Secrets through the approved secrets operator.
4. Install the chart using signed ECR digests, an exact build SHA, and the ACM certificate ARN.
5. Read the ingress ALB hostname.
6. Reapply Terraform with that hostname. Use `NONE` routing in staging, `PRIMARY` in production,
   and `SECONDARY` in production DR. Both production records require a Route 53 health check.

Example chart installation (replace every example value):

```bash
helm upgrade --install loopguard infra/helm/loopguard \
  --namespace loopguard \
  --create-namespace \
  -f infra/helm/loopguard/values-production.yaml \
  --set-string global.buildSha="$RELEASE_COMMIT" \
  --set-string global.clusterName="$EKS_CLUSTER" \
  --set-string global.certificateArn="$ACM_CERTIFICATE_ARN" \
  --set-string images.controlApi.repository="$CONTROL_API_REPOSITORY" \
  --set-string images.controlApi.digest="$CONTROL_API_DIGEST" \
  --set-string images.web.repository="$WEB_REPOSITORY" \
  --set-string images.web.digest="$WEB_DIGEST" \
  --set-string images.worker.repository="$WORKER_REPOSITORY" \
  --set-string images.worker.digest="$WORKER_DIGEST" \
  --atomic --timeout 15m
```

The worker image must include an approved activity adapter and set
`LOOPGUARD_REPAIR_ACTIVITY_FACTORY=package.module:create_activities`. The worker validates that the
adapter registers exactly the durable workflow activities. No host filesystem, container socket,
cloud role, service-account token, or metadata endpoint is available to repair pods.

## Normal deployment

Run the `deploy` workflow manually with the protected environment and three
`repository@sha256:digest` inputs. It:

1. exchanges GitHub OIDC for a short-lived, account-bound AWS role;
2. verifies every image with Cosign against the release workflow identity;
3. rehearses the additive migration sequence on an isolated PostgreSQL database;
4. runs schema migration and an isolated control-API canary;
5. probes the canary through a private port-forward;
6. atomically promotes API, web, and worker digests;
7. verifies the public build SHA and all SLO alarms;
8. rolls back to the captured Helm revision on failure and uploads bounded evidence.

The workflow does not accept tags, `latest`, public registries, long-lived AWS keys, or images from
another account/region. Production GitHub Environment approval is a required human gate.

## Regional recovery

Multi-AZ is not regional failover. `production-dr` creates independent network, compute, KMS,
ingress, capacity, artifact storage, and a cross-region read replica. Production separately enables
S3 and ECR replication. Apply DR first, then pass its bucket and KMS ARNs into the production
environment. Confirm replication metrics and checksum probes before calling DR warm.

Promotion is operator-authorized:

1. fence and verify the former writer;
2. promote the DR database or restore the tested encrypted snapshot;
3. update secret/endpoints;
4. check Alembic revision, Temporal search/state, S3 manifests, event/action idempotency, and stream
   reconnect;
5. shift Route 53 only after validation;
6. preserve a tested failback path.

`rehearse_region_failover.sh` only runs with an explicit staging/DR approval file. Operator-owned
wrappers perform account-specific fencing, promotion, validation, and failback and must produce
passed RPO, checksum, database-integrity, idempotency, stream-reconnect, and rollback evidence.
Never point the rehearsal wrappers at production.

## Rollback boundaries

Application rollback is Helm revision rollback. Database changes must follow expand/read-switch/
contract sequencing and keep the previous application compatible for the rollback window. A
destructive contract migration ships only after the previous version is outside the rollback
window and a restore rehearsal passes. Regional database promotion and failback are never automatic.
