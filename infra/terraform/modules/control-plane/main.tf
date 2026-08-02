data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
  common_tags = merge(var.tags, {
    Application = "loopguard"
    Environment = var.environment
    ManagedBy   = "terraform"
  })
  is_replica = var.database_replica_source_arn != ""
  create_dns = var.ingress_hostname != ""
  failover_dns = local.create_dns && var.route53_failover_role != "NONE"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.common_tags, { Name = var.name })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${var.name}-igw" })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = false
  tags = merge(local.common_tags, {
    Name                     = "${var.name}-public-${count.index + 1}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  tags = merge(local.common_tags, {
    Name                              = "${var.name}-private-${count.index + 1}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_eip" "nat" {
  count  = 2
  domain = "vpc"
  tags   = local.common_tags
  depends_on = [aws_internet_gateway.this]
}

resource "aws_nat_gateway" "this" {
  count         = 2
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = local.common_tags
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = local.common_tags
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index].id
  }
  tags = local.common_tags
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_kms_key" "data" {
  description             = "${var.name} tenant data"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.common_tags
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name}-artifacts-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
  tags          = local.common_tags
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.data.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "noncurrent-retention"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_iam_role" "artifact_replication" {
  count = var.artifact_replication_destination_arn == "" ? 0 : 1
  name  = "${var.name}-artifact-replication"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "artifact_replication" {
  count = var.artifact_replication_destination_arn == "" ? 0 : 1
  role  = aws_iam_role.artifact_replication[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = aws_s3_bucket.artifacts.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectRetention",
          "s3:GetObjectLegalHold",
        ]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
        ]
        Resource = "${var.artifact_replication_destination_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = compact([
          aws_kms_key.data.arn,
          var.artifact_replication_destination_kms_key_arn,
        ])
      },
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "artifacts" {
  count  = var.artifact_replication_destination_arn == "" ? 0 : 1
  bucket = aws_s3_bucket.artifacts.id
  role   = aws_iam_role.artifact_replication[0].arn
  rule {
    id     = "production-dr"
    status = "Enabled"
    filter {}
    delete_marker_replication {
      status = "Enabled"
    }
    destination {
      bucket        = var.artifact_replication_destination_arn
      storage_class = "STANDARD"
      encryption_configuration {
        replica_kms_key_id = var.artifact_replication_destination_kms_key_arn
      }
      metrics {
        status = "Enabled"
        event_threshold {
          minutes = 15
        }
      }
      replication_time {
        status = "Enabled"
        time {
          minutes = 15
        }
      }
    }
    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }
  }
  depends_on = [aws_s3_bucket_versioning.artifacts]
  lifecycle {
    precondition {
      condition     = var.artifact_replication_destination_kms_key_arn != ""
      error_message = "artifact replication requires a destination KMS key ARN"
    }
  }
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
  tags       = local.common_tags
}

resource "aws_security_group" "database" {
  name_prefix = "${var.name}-database-"
  description = "PostgreSQL only from private EKS nodes"
  vpc_id      = aws_vpc.this.id
  egress      = []
  tags        = local.common_tags
}

resource "aws_security_group_rule" "database_from_nodes" {
  type                     = "ingress"
  security_group_id        = aws_security_group.database.id
  source_security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
}

resource "aws_db_instance" "primary" {
  identifier                    = var.name
  replicate_source_db           = local.is_replica ? var.database_replica_source_arn : null
  engine                        = local.is_replica ? null : "postgres"
  engine_version                = local.is_replica ? null : "16.8"
  instance_class                = var.database_instance_class
  allocated_storage             = local.is_replica ? null : var.database_allocated_storage_gib
  max_allocated_storage         = local.is_replica ? null : var.database_allocated_storage_gib * 2
  storage_type                  = local.is_replica ? null : "gp3"
  storage_encrypted             = true
  kms_key_id                    = aws_kms_key.data.arn
  db_name                       = local.is_replica ? null : "loopguard"
  username                      = local.is_replica ? null : "loopguard_admin"
  manage_master_user_password   = local.is_replica ? null : true
  db_subnet_group_name          = aws_db_subnet_group.this.name
  vpc_security_group_ids        = [aws_security_group.database.id]
  publicly_accessible           = false
  multi_az                      = local.is_replica ? false : true
  backup_retention_period       = var.database_backup_retention_days
  backup_window                 = "04:00-05:00"
  maintenance_window            = "sun:06:00-sun:07:00"
  auto_minor_version_upgrade    = false
  deletion_protection           = true
  skip_final_snapshot           = false
  final_snapshot_identifier     = "${var.name}-final"
  performance_insights_enabled  = true
  monitoring_interval           = 60
  monitoring_role_arn           = aws_iam_role.rds_monitoring.arn
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  copy_tags_to_snapshot          = true
  apply_immediately              = false
  tags                           = local.common_tags
}

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.name}-rds-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_iam_role" "eks_cluster" {
  name = "${var.name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = var.environment == "staging" ? 30 : 365
  kms_key_id        = aws_kms_key.data.arn
  tags              = local.common_tags
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.cluster_version
  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = false
    security_group_ids      = [aws_security_group.cluster.id]
  }
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }
  depends_on = [
    aws_cloudwatch_log_group.eks,
    aws_iam_role_policy_attachment.eks_cluster,
  ]
  tags = local.common_tags
}

resource "aws_eks_access_entry" "platform_admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.cluster_admin_role_arn
  type          = "STANDARD"
  tags          = local.common_tags
}

resource "aws_eks_access_policy_association" "platform_admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.cluster_admin_role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope {
    type = "cluster"
  }
  depends_on = [aws_eks_access_entry.platform_admin]
}

resource "aws_eks_addon" "core" {
  for_each                    = var.eks_addon_versions
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  addon_version               = each.value
  resolve_conflicts_on_create = "NONE"
  resolve_conflicts_on_update = "PRESERVE"
  tags                        = local.common_tags
}

resource "aws_security_group" "cluster" {
  name_prefix = "${var.name}-cluster-"
  vpc_id      = aws_vpc.this.id
  tags        = local.common_tags
}

resource "aws_iam_role" "nodes" {
  name = "${var.name}-eks-nodes"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "nodes" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
  ])
  role       = aws_iam_role.nodes.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "system"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = var.node_instance_types
  capacity_type   = "ON_DEMAND"
  scaling_config {
    desired_size = var.environment == "staging" ? 2 : 3
    min_size     = 2
    max_size     = var.environment == "staging" ? 4 : 12
  }
  update_config {
    max_unavailable_percentage = 25
  }
  labels = { workload = "system" }
  tags   = local.common_tags
  depends_on = [
    aws_iam_role_policy_attachment.nodes,
    aws_eks_addon.core,
  ]
}

resource "aws_eks_node_group" "repair" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "repair-isolated"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = var.repair_node_instance_types
  capacity_type   = "ON_DEMAND"
  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = var.environment == "staging" ? 2 : 10
  }
  labels = {
    workload = "repair"
    isolation = "gvisor"
  }
  taint {
    key    = "loopguard.dev/repair"
    value  = "true"
    effect = "NO_SCHEDULE"
  }
  tags = local.common_tags
  depends_on = [
    aws_iam_role_policy_attachment.nodes,
    aws_eks_addon.core,
  ]
}

resource "aws_ecr_repository" "services" {
  for_each             = toset(["control-api", "web", "worker"])
  name                 = "${var.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "services" {
  for_each   = aws_ecr_repository.services
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain 100 immutable release images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 100
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_replication_configuration" "dr" {
  count = var.ecr_replication_region == "" ? 0 : 1
  replication_configuration {
    rule {
      destination {
        region      = var.ecr_replication_region
        registry_id = data.aws_caller_identity.current.account_id
      }
      repository_filter {
        filter      = "${var.name}/"
        filter_type = "PREFIX_MATCH"
      }
    }
  }
}

resource "aws_iam_role" "control_api" {
  name = "${var.name}-control-api"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "control_api" {
  role = aws_iam_role.control_api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${aws_s3_bucket.artifacts.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.artifacts.arn
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = aws_kms_key.data.arn
      },
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = var.external_secret_arns
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "control_api" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "loopguard"
  service_account = "loopguard-control-api"
  role_arn        = aws_iam_role.control_api.arn
}

resource "aws_iam_role" "load_balancer_controller" {
  name = "${var.name}-load-balancer-controller"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "load_balancer_controller" {
  role = aws_iam_role.load_balancer_controller.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeAccountAttributes",
          "ec2:DescribeAddresses",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeCoipPools",
          "ec2:DescribeInstances",
          "ec2:DescribeInternetGateways",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "ec2:DescribeTags",
          "ec2:DescribeVpcPeeringConnections",
          "ec2:DescribeVpcs",
          "elasticloadbalancing:DescribeListenerAttributes",
          "elasticloadbalancing:DescribeListenerCertificates",
          "elasticloadbalancing:DescribeListeners",
          "elasticloadbalancing:DescribeLoadBalancerAttributes",
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeRules",
          "elasticloadbalancing:DescribeSSLPolicies",
          "elasticloadbalancing:DescribeTags",
          "elasticloadbalancing:DescribeTargetGroupAttributes",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "acm:DescribeCertificate",
          "acm:ListCertificates",
          "iam:CreateServiceLinkedRole",
          "shield:GetSubscriptionState",
          "shield:DescribeProtection",
          "shield:CreateProtection",
          "shield:DeleteProtection",
          "waf-regional:GetWebACLForResource",
          "waf-regional:GetWebACL",
          "waf-regional:AssociateWebACL",
          "waf-regional:DisassociateWebACL",
          "wafv2:GetWebACLForResource",
          "wafv2:GetWebACL",
          "wafv2:AssociateWebACL",
          "wafv2:DisassociateWebACL",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:CreateSecurityGroup",
          "ec2:CreateTags",
          "ec2:DeleteSecurityGroup",
          "ec2:RevokeSecurityGroupIngress",
          "elasticloadbalancing:AddListenerCertificates",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:CreateListener",
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:ModifyListener",
          "elasticloadbalancing:ModifyListenerAttributes",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:RemoveListenerCertificates",
          "elasticloadbalancing:RemoveTags",
          "elasticloadbalancing:SetIpAddressType",
          "elasticloadbalancing:SetSecurityGroups",
          "elasticloadbalancing:SetSubnets",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/elbv2.k8s.aws/cluster" = var.name
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/elbv2.k8s.aws/cluster" = var.name
          }
        }
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "load_balancer_controller" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.load_balancer_controller.arn
}

resource "aws_acm_certificate" "ingress" {
  domain_name               = var.api_domain
  subject_alternative_names = [var.web_domain]
  validation_method         = "DNS"
  lifecycle {
    create_before_destroy = true
  }
  tags = local.common_tags
}

resource "aws_route53_record" "certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.ingress.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }
  zone_id = var.hosted_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "ingress" {
  certificate_arn         = aws_acm_certificate.ingress.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate_validation : record.fqdn]
}

resource "aws_route53_record" "regional_api" {
  count   = local.create_dns && !local.failover_dns ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = var.api_domain
  type    = "CNAME"
  ttl     = 60
  records = [var.ingress_hostname]
}

resource "aws_route53_record" "regional_web" {
  count   = local.create_dns && !local.failover_dns ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = var.web_domain
  type    = "CNAME"
  ttl     = 60
  records = [var.ingress_hostname]
}

resource "aws_route53_record" "failover_api" {
  count           = local.failover_dns ? 1 : 0
  zone_id         = var.hosted_zone_id
  name            = var.api_domain
  type            = "CNAME"
  ttl             = 60
  records         = [var.ingress_hostname]
  set_identifier  = "${var.name}-api"
  health_check_id = var.route53_health_check_id
  failover_routing_policy {
    type = var.route53_failover_role
  }
  lifecycle {
    precondition {
      condition     = var.route53_health_check_id != ""
      error_message = "failover DNS requires a Route 53 health check ID"
    }
  }
}

resource "aws_route53_record" "failover_web" {
  count           = local.failover_dns ? 1 : 0
  zone_id         = var.hosted_zone_id
  name            = var.web_domain
  type            = "CNAME"
  ttl             = 60
  records         = [var.ingress_hostname]
  set_identifier  = "${var.name}-web"
  health_check_id = var.route53_health_check_id
  failover_routing_policy {
    type = var.route53_failover_role
  }
  lifecycle {
    precondition {
      condition     = var.route53_health_check_id != ""
      error_message = "failover DNS requires a Route 53 health check ID"
    }
  }
}
