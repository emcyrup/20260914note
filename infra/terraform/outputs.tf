output "static_ip" {
  description = "固定IP。DNS の A レコードをこれに向ける／GitHub Secrets の DEPLOY_HOST に設定する"
  value       = aws_lightsail_static_ip.app.ip_address
}

output "ssh_command" {
  description = "サーバーへの接続コマンド"
  value       = "ssh ubuntu@${aws_lightsail_static_ip.app.ip_address}"
}

output "backup_bucket" {
  description = "バックアップ用 S3 バケット名（.env の BACKUP_S3_BUCKET に設定）"
  value       = var.enable_backup_bucket ? aws_s3_bucket.backups[0].bucket : null
}

output "backup_iam_user" {
  description = "バックアップ用 IAM ユーザー名（aws iam create-access-key --user-name <これ>）"
  value       = var.enable_backup_bucket ? aws_iam_user.backup[0].name : null
}
