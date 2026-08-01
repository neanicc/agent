terraform {
  required_version = "= 1.12.2"
  backend "s3" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 5.100.0"
    }
  }
}

variable "aws_region" { type = string }
variable "account_id" { type = string }
variable "hosted_zone_id" { type = string }
variable "api_domain" { type = string }
variable "web_domain" { type = string }
variable "ingress_hostname" {
  type    = string
  default = ""
}
variable "temporal_endpoint" { type = string }
variable "oidc_issuer" { type = string }
variable "external_secret_arns" { type = list(string) }
variable "cluster_admin_role_arn" { type = string }

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      Application = "loopguard"
      Environment = "staging"
    }
  }
}

module "control_plane" {
  source = "../../modules/control-plane"

  name                           = "loopguard-staging"
  environment                    = "staging"
  vpc_cidr                       = "10.40.0.0/16"
  database_instance_class        = "db.t4g.medium"
  database_allocated_storage_gib = 100
  database_backup_retention_days = 7
  node_instance_types            = ["m7g.large"]
  repair_node_instance_types     = ["m7g.xlarge"]
  cluster_admin_role_arn          = var.cluster_admin_role_arn
  eks_addon_versions = {
    vpc-cni                = "v1.20.3-eksbuild.1"
    coredns                = "v1.12.4-eksbuild.18"
    kube-proxy             = "v1.33.10-eksbuild.13"
    eks-pod-identity-agent = "v1.3.10-eksbuild.2"
  }
  hosted_zone_id                 = var.hosted_zone_id
  api_domain                     = var.api_domain
  web_domain                     = var.web_domain
  ingress_hostname               = var.ingress_hostname
  route53_failover_role          = "NONE"
  temporal_endpoint              = var.temporal_endpoint
  oidc_issuer                    = var.oidc_issuer
  external_secret_arns           = var.external_secret_arns
}
