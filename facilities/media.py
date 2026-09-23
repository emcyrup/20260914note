"""
アップロードファイル（写真・紙の日誌・利用希望の用紙・署名・受給者証・資料・ロゴ）の配信。

/media/ をそのまま公開せず、ログイン中の職員の事業所が持つファイルだけを返す。
LINE に写真を送るときのように外部から取りに来る場合は、有効期限つきの署名 URL を使う。
"""
import mimetypes
import posixpath

from django.apps import apps
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core import signing
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.views import View

# パスの先頭 → (モデル, ファイル項目, 事業所への絞り込み)
OWNERS = [
    ('facility_logos/',         'facilities.Facility',             'logo',            'pk'),
    ('daily_record_photos/',    'records.DailyRecordPhoto',        'photo',           'facility'),
    ('paper_scans/',            'records.PaperScan',               'image',           'facility'),
    ('signatures/',             'esignatures.EsignatureRecord',    'signature_image', 'facility'),
    ('recipient_certificates/', 'beneficiaries.RecipientCertificate', 'scanned_image', 'beneficiary__facility'),
    ('reference_docs/',         'ai_assist.ReferenceDocument',     'file',            'facility'),
    ('request_scans/',          'reservations.RequestScan',        'image',           'facility'),
    ('beneficiary_assessments/', 'beneficiaries.BeneficiaryAssessment', 'file',       'beneficiary__facility'),
]
SALT = 'protected-media'
DEFAULT_MAX_AGE = 30 * 60  # 署名 URL の有効期限（秒）


def clean_path(path):
    """`..` や絶対パスを含まない正規化したパスを返す。不正なら None"""
    path = (path or '').replace('\\', '/').lstrip('/')
    norm = posixpath.normpath(path)
    if not path or norm != path or norm.startswith('..') or '/../' in f'/{norm}/':
        return None
    return norm


def is_owned_by(path, facility):
    """そのパスのファイルが、この事業所のものか"""
    if facility is None:
        return False
    for prefix, model_label, field, lookup in OWNERS:
        if path.startswith(prefix):
            model = apps.get_model(model_label)
            return model.objects.filter(**{field: path, lookup: facility.pk}).exists()
    return False


def signed_media_url(path, max_age=DEFAULT_MAX_AGE):
    """有効期限つきの署名 URL（相対）。外部サービス（LINE など）に渡すときに使う"""
    token = signing.TimestampSigner(salt=SALT).sign(path)
    return f'{settings.MEDIA_URL}{path}?sig={token}&max_age={int(max_age)}'


def verify_signature(path, token, max_age):
    try:
        max_age = min(int(max_age), 24 * 3600)
    except (TypeError, ValueError):
        max_age = DEFAULT_MAX_AGE
    try:
        return signing.TimestampSigner(salt=SALT).unsign(token, max_age=max_age) == path
    except signing.BadSignature:
        return False


class ProtectedMediaView(View):
    def get(self, request, path):
        path = clean_path(path)
        if path is None:
            raise Http404
        token = request.GET.get('sig')
        if token:
            if not verify_signature(path, token, request.GET.get('max_age', DEFAULT_MAX_AGE)):
                raise Http404
        else:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not is_owned_by(path, getattr(request.user, 'facility', None)):
                raise Http404
        if not default_storage.exists(path):
            raise Http404
        content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        resp = FileResponse(default_storage.open(path, 'rb'), content_type=content_type)
        resp['Cache-Control'] = 'private, max-age=0'
        resp['X-Content-Type-Options'] = 'nosniff'
        return resp
