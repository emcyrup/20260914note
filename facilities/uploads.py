"""
アップロードファイルの保存先。元のファイル名は使わず UUID にして推測できないようにする。
（元の名前は各モデルの他の項目や表示には使っていない）
"""
import uuid
from pathlib import Path

from django.utils import timezone

_MAX_EXT = 10


def _name(prefix, filename, dated=True):
    ext = Path(filename or '').suffix.lower()[:_MAX_EXT]
    if not ext.replace('.', '').isalnum():
        ext = ''
    if dated:
        return f'{prefix}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}'
    return f'{prefix}/{uuid.uuid4().hex}{ext}'


def logo_upload_to(instance, filename):
    return _name('facility_logos', filename, dated=False)


def photo_upload_to(instance, filename):
    return _name('daily_record_photos', filename)


def paper_scan_upload_to(instance, filename):
    return _name('paper_scans', filename)


def signature_upload_to(instance, filename):
    return _name('signatures', filename)


def certificate_upload_to(instance, filename):
    return _name('recipient_certificates', filename, dated=False)


def reference_doc_upload_to(instance, filename):
    return _name('reference_docs', filename)


def request_scan_upload_to(instance, filename):
    return _name('request_scans', filename)
