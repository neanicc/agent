variable "name" {
  description = "Globally identifying deployment name."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.name))
    error_message = "name must be a bounded lowercase slug"
  }
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production", "production-dr"], var.environment)
    error_message = "environment must be staging, production, or production-dr"
  }
}

variable "vpc_cidr" {
  type = string
}

variable "database_instance_class" {
  type = string
}

variable "database_allocated_storage_gib" {
  type = number
}

variable "database_backup_retention_days" {
  type    = number
  default = 14
  validation {
    condition     = var.database_backup_retention_days >= 7 && var.database_backup_retention_days <= 35
    error_message = "database backup retention must be 7-35 days"
  }
}

variable "database_replica_source_arn" {
  description = "Cross-region source DB ARN for production-dr; empty in primary regions."
  type        = string
  default     = ""
}

variable "artifact_replication_destination_arn" {
  description = "DR bucket ARN for primary-region S3 replication; empty outside production."
  type        = string
  default     = ""
}

variable "artifact_replication_destination_kms_key_arn" {
  description = "DR KMS key ARN paired with the replication destination bucket."
  type        = string
  default     = ""
}

variable "ecr_replication_region" {
  type    = string
  default = ""
}

variable "cluster_version" {
  type    = string
  default = "1.33"
}

variable "cluster_admin_role_arn" {
  description = "Human-controlled platform role granted EKS cluster-admin access; never an application role."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/", var.cluster_admin_role_arn))
    error_message = "cluster_admin_role_arn must be an IAM role ARN"
  }
}

variable "eks_addon_versions" {
  description = "Explicit versions for vpc-cni, coredns, kube-proxy, and eks-pod-identity-agent."
  type        = map(string)
  validation {
    condition = setequals(
      toset(keys(var.eks_addon_versions)),
      toset(["vpc-cni", "coredns", "kube-proxy", "eks-pod-identity-agent"]),
    ) && alltrue([
      for version in values(var.eks_addon_versions) :
      can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+-eksbuild\\.[0-9]+$", version))
    ])
    error_message = "eks_addon_versions must pin exactly the four required EKS add-ons"
  }
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m7g.large"]
}

variable "repair_node_instance_types" {
  type    = list(string)
  default = ["m7g.xlarge"]
}

variable "hosted_zone_id" {
  type = string
}

variable "api_domain" {
  type = string
}

variable "web_domain" {
  type = string
}

variable "ingress_hostname" {
  description = "Regional ALB DNS hostname. Leave empty for the bootstrap apply, then set after Helm creates the ingress."
  type        = string
  default     = ""
  validation {
    condition     = var.ingress_hostname == "" || can(regex("^[A-Za-z0-9.-]+$", var.ingress_hostname))
    error_message = "ingress_hostname must be an unqualified DNS hostname without a scheme or path"
  }
}

variable "route53_failover_role" {
  description = "NONE for staging, PRIMARY for production, SECONDARY for production-dr."
  type        = string
  default     = "NONE"
  validation {
    condition     = contains(["NONE", "PRIMARY", "SECONDARY"], var.route53_failover_role)
    error_message = "route53_failover_role must be NONE, PRIMARY, or SECONDARY"
  }
}

variable "route53_health_check_id" {
  description = "Route 53 health check used by failover records; required for PRIMARY and SECONDARY."
  type        = string
  default     = ""
}

variable "temporal_endpoint" {
  description = "External Temporal Cloud endpoint; no credential is stored in Terraform."
  type        = string
  sensitive   = false
}

variable "oidc_issuer" {
  description = "External tenant identity-provider issuer."
  type        = string
}

variable "external_secret_arns" {
  description = "Secret Manager ARNs for OIDC, Temporal, APNs, signing and billing material."
  type        = list(string)
  sensitive   = false
  validation {
    condition     = length(var.external_secret_arns) > 0 && alltrue([for arn in var.external_secret_arns : startswith(arn, "arn:aws:secretsmanager:")])
    error_message = "external secret ARNs must be non-empty Secrets Manager ARNs"
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
