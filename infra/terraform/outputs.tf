output "static_ip" {
  description = "固定IP。DNS の A レコードをこれに向ける／GitHub Secrets の DEPLOY_HOST に設定する"
  value       = google_compute_address.app.address
}

output "ssh_command" {
  description = "サーバーへの接続コマンド"
  value       = "ssh ${var.ssh_user}@${google_compute_address.app.address}"
}

output "backup_bucket" {
  description = "バックアップ用バケット名（.env の BACKUP_GCS_BUCKET に設定）"
  value       = var.enable_backup_bucket ? google_storage_bucket.backups[0].name : null
}

output "service_account" {
  description = "VM に付けたサービスアカウント"
  value       = google_service_account.vm.email
}
