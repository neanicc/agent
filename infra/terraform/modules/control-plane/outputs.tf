output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value     = aws_eks_cluster.this.endpoint
  sensitive = true
}

output "database_endpoint" {
  value     = aws_db_instance.primary.address
  sensitive = true
}

output "database_master_secret_arn" {
  value     = local.is_replica ? null : aws_db_instance.primary.master_user_secret[0].secret_arn
  sensitive = true
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "artifact_kms_key_arn" {
  value = aws_kms_key.data.arn
}

output "ecr_repository_urls" {
  value = { for name, repository in aws_ecr_repository.services : name => repository.repository_url }
}

output "certificate_arn" {
  value = aws_acm_certificate.ingress.arn
}

output "load_balancer_controller_role_arn" {
  value = aws_iam_role.load_balancer_controller.arn
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "external_dependencies" {
  value = {
    temporal_endpoint    = var.temporal_endpoint
    oidc_issuer          = var.oidc_issuer
    external_secret_arns = var.external_secret_arns
  }
}
