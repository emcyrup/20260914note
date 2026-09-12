variable "aws_region" {
  description = "リージョン（東京）"
  type        = string
  default     = "ap-northeast-1"
}

variable "name" {
  description = "リソース名の接頭辞"
  type        = string
  default     = "dayservice"
}

variable "bundle_id" {
  description = <<-EOT
    Lightsail のプラン。2GB メモリ（WeasyPrint の PDF 生成と PostgreSQL 同居に十分）で最安のもの。
    最新の ID と価格は `aws lightsail get-bundles --region ap-northeast-1` で確認できる。
  EOT
  type        = string
  default     = "small_3_0"
}

variable "ssh_public_key_path" {
  description = "サーバーに登録する SSH 公開鍵（GitHub Actions のデプロイにも同じ鍵ペアを使う）"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_allowed_cidrs" {
  description = "SSH(22) を許可する CIDR。GitHub Actions からデプロイするため既定は全許可（鍵認証のみ）"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_backup_bucket" {
  description = "DB バックアップ用の S3 バケットと IAM ユーザーを作るか"
  type        = bool
  default     = true
}

variable "backup_bucket_name" {
  description = "バックアップ用バケット名（空なら name-backups- に乱数を付けて自動生成）"
  type        = string
  default     = ""
}
