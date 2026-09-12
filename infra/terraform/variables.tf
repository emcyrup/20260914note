variable "project_id" {
  description = "GCP プロジェクト ID（例: dayservice-prod-123456）"
  type        = string
}

variable "region" {
  description = "リージョン。東京は asia-northeast1。Always Free 枠を使うなら us-central1 / us-west1 / us-east1"
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "ゾーン（region 内）"
  type        = string
  default     = "asia-northeast1-a"
}

variable "name" {
  description = "リソース名の接頭辞"
  type        = string
  default     = "dayservice"
}

variable "machine_type" {
  description = <<-EOT
    VM の種類。
      e2-small : 2GB メモリ・共有2vCPU。東京で月 約 $16。PDF 生成と PostgreSQL 同居に余裕がある（既定）
      e2-micro : 1GB メモリ。東京で月 約 $8。スワップ2GBを自動で作るので小規模なら動く。
                 us-central1 / us-west1 / us-east1 なら Always Free 枠で VM 代 $0
  EOT
  type        = string
  default     = "e2-small"
}

variable "disk_size_gb" {
  description = "ブートディスク容量（pd-standard）。写真とDBがここに入る"
  type        = number
  default     = 30
}

variable "ssh_user" {
  description = "サーバーのログインユーザー名（GitHub Actions のデプロイもこのユーザーで行う）"
  type        = string
  default     = "deploy"
}

variable "ssh_public_key_path" {
  description = "サーバーに登録する SSH 公開鍵（GitHub Actions には対になる秘密鍵を登録する）"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_allowed_cidrs" {
  description = "SSH(22) を許可する CIDR。GitHub Actions からデプロイするため既定は全許可（鍵認証のみ）"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_backup_bucket" {
  description = "DB バックアップ用の Cloud Storage バケットを作るか"
  type        = bool
  default     = true
}

variable "backup_bucket_name" {
  description = "バックアップ用バケット名（世界で一意）。空なら name-backups-project_id で自動生成"
  type        = string
  default     = ""
}
