# =============================================
# 最小コスト構成：Lightsail 1台（Docker Compose）＋ 固定IP ＋ バックアップ用 S3
#   月額の目安：Lightsail 2GB プラン 約 $7 ＋ S3 数十円
# =============================================

locals {
  tags = {
    Project   = var.name
    ManagedBy = "terraform"
  }
}

# ---- SSH 鍵（手元の公開鍵を登録）----
resource "aws_lightsail_key_pair" "deploy" {
  name       = "${var.name}-deploy"
  public_key = file(pathexpand(var.ssh_public_key_path))
  tags       = local.tags
}

# ---- サーバー本体 ----
resource "aws_lightsail_instance" "app" {
  name              = var.name
  availability_zone = "${var.aws_region}a"
  blueprint_id      = "ubuntu_22_04"
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.deploy.name
  # 初回起動時に Docker などを入れる（deploy/setup-server.sh）
  user_data = file("${path.module}/../../deploy/setup-server.sh")
  tags      = local.tags
}

# ---- 固定IP（再起動しても変わらない。DNS の A レコードはこれに向ける）----
resource "aws_lightsail_static_ip" "app" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "app" {
  static_ip_name = aws_lightsail_static_ip.app.name
  instance_name  = aws_lightsail_instance.app.name
}

# ---- ファイアウォール：SSH / HTTP / HTTPS のみ ----
resource "aws_lightsail_instance_public_ports" "app" {
  instance_name = aws_lightsail_instance.app.name

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.ssh_allowed_cidrs
  }

  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
  }

  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
  }
}

# ---- DB バックアップ先（任意）----
resource "aws_s3_bucket" "backups" {
  count         = var.enable_backup_bucket ? 1 : 0
  bucket        = var.backup_bucket_name != "" ? var.backup_bucket_name : null
  bucket_prefix = var.backup_bucket_name == "" ? "${var.name}-backups-" : null
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "backups" {
  count  = var.enable_backup_bucket ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  count  = var.enable_backup_bucket ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 30日より古いバックアップは自動削除（保管料を抑える）
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  count  = var.enable_backup_bucket ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {
      prefix = "db/"
    }

    expiration {
      days = 30
    }
  }
}

# サーバーからバケットに書き込むための IAM ユーザー（Lightsail は IAM ロールを付けられないため）
# アクセスキーは state に残さないよう手動で発行する（README 参照）
resource "aws_iam_user" "backup" {
  count = var.enable_backup_bucket ? 1 : 0
  name  = "${var.name}-backup"
  tags  = local.tags
}

data "aws_iam_policy_document" "backup" {
  count = var.enable_backup_bucket ? 1 : 0

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.backups[0].arn]
  }

  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.backups[0].arn}/db/*"]
  }
}

resource "aws_iam_user_policy" "backup" {
  count  = var.enable_backup_bucket ? 1 : 0
  name   = "${var.name}-backup-write"
  user   = aws_iam_user.backup[0].name
  policy = data.aws_iam_policy_document.backup[0].json
}
