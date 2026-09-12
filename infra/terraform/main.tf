# =============================================
# 最小コスト構成：Compute Engine 1台（Docker Compose）＋ 固定IP ＋ バックアップ用 Cloud Storage
#   月額の目安（東京・e2-small）：VM 約 $16 ＋ ディスク 約 $1.5 ＋ 外部IP 約 $3.7 ＋ GCS 数十円
#   VM にはサービスアカウントを付けるので、バックアップにアクセスキーは不要
# =============================================

locals {
  labels = {
    project    = var.name
    managed-by = "terraform"
  }
  backup_bucket_name = var.backup_bucket_name != "" ? var.backup_bucket_name : "${var.name}-backups-${var.project_id}"
}

# ---- 使う API を有効化（新しいプロジェクトでは必須）----
resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iam" {
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

# ---- ネットワーク（default ネットワークに依存しない）----
resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.compute]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.name}-subnet"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

# ---- ファイアウォール：HTTP / HTTPS は全開放、SSH は指定範囲 ----
resource "google_compute_firewall" "web" {
  name    = "${var.name}-allow-web"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["${var.name}-web"]
}

resource "google_compute_firewall" "ssh" {
  name    = "${var.name}-allow-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_allowed_cidrs
  target_tags   = ["${var.name}-ssh"]
}

# ---- 固定IP（DNS の A レコードはこれに向ける）----
resource "google_compute_address" "app" {
  name       = "${var.name}-ip"
  region     = var.region
  depends_on = [google_project_service.compute]
}

# ---- VM 用サービスアカウント（バックアップの書き込みに使う）----
resource "google_service_account" "vm" {
  account_id   = "${var.name}-vm"
  display_name = "${var.name} VM"
  depends_on   = [google_project_service.iam]
}

# ---- サーバー本体 ----
resource "google_compute_instance" "app" {
  name         = var.name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["${var.name}-web", "${var.name}-ssh"]
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = var.disk_size_gb
      type  = "pd-standard" # いちばん安いディスク
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id

    access_config {
      nat_ip = google_compute_address.app.address
    }
  }

  metadata = {
    # メタデータの ssh-keys でログインする（OS Login は使わない）
    enable-oslogin = "FALSE"
    ssh-keys       = "${var.ssh_user}:${trimspace(file(pathexpand(var.ssh_public_key_path)))}"
    # 初回起動時に Docker などを入れる（deploy/setup-server.sh を cloud-init が実行）
    user-data = file("${path.module}/../../deploy/setup-server.sh")
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  allow_stopping_for_update = true
}

# ---- DB バックアップ先（任意）----
resource "google_storage_bucket" "backups" {
  count                       = var.enable_backup_bucket ? 1 : 0
  name                        = local.backup_bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.labels

  # 30日より古いバックアップは自動削除（保管料を抑える）
  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["db/"]
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "vm_backup" {
  count  = var.enable_backup_bucket ? 1 : 0
  bucket = google_storage_bucket.backups[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.vm.email}"
}
