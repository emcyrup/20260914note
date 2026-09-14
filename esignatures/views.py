"""
電子サイン機能のビュー定義
- SaveSignatureView: canvas の base64画像データを受け取りEsignatureRecordとして保存する
"""

import base64
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import EsignatureRecord


class SaveSignatureView(LoginRequiredMixin, View):
    """
    署名画像を保存する（JSON POST）。
    canvas.toDataURL() で得たbase64データをPNG画像として保存し、
    EsignatureRecord として対象記録と紐づける。
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'リクエストの形式が不正です'})

        image_data   = data.get('image_data', '')
        signer_name  = data.get('signer_name', '').strip()
        relationship = data.get('relationship', '').strip()
        target_type  = data.get('target_type', '')
        target_id    = data.get('target_id', 0)

        if not signer_name:
            return JsonResponse({'ok': False, 'error': '署名者氏名を入力してください'})
        if not image_data:
            return JsonResponse({'ok': False, 'error': '署名データがありません'})
        if target_type not in dict(EsignatureRecord.TARGET_TYPE_CHOICES):
            return JsonResponse({'ok': False, 'error': '無効な対象種別です'})

        # target_id が自施設のレコードに属するか確認（他施設レコードへの署名を防止）
        facility = request.user.facility
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': '対象の指定が正しくありません'})
        if target_type == 'daily_record':
            from records.models import DailyRecord
            if not DailyRecord.objects.filter(pk=target_id, facility=facility).exists():
                return JsonResponse({'ok': False, 'error': '対象の日誌が見つかりません'})
        elif target_type == 'support_plan':
            from support_plans.models import SupportPlan
            plan = SupportPlan.objects.filter(pk=target_id, facility=facility).first()
            if not plan:
                return JsonResponse({'ok': False, 'error': '対象の支援計画が見つかりません'})
            if plan.current_step != SupportPlan.STEP_CONSENT:
                return JsonResponse({'ok': False, 'error': '同意の署名はステップ4「説明・同意・交付」で行います'})
        elif target_type == 'monitoring':
            from support_plans.models import MonitoringRecord
            if not MonitoringRecord.objects.filter(pk=target_id, plan__facility=facility).exists():
                return JsonResponse({'ok': False, 'error': '対象のモニタリング記録が見つかりません'})
        else:
            return JsonResponse({'ok': False, 'error': '無効な対象種別です'})

        # base64 → バイナリに変換
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return JsonResponse({'ok': False, 'error': '署名データの変換に失敗しました'})

        import uuid
        filename = f'sig_{target_type}_{target_id}_{uuid.uuid4().hex[:8]}.png'
        image_file = ContentFile(image_bytes, name=filename)

        record = EsignatureRecord(
            facility=facility,
            signer_name=signer_name,
            relationship=relationship,
            target_type=target_type,
            target_id=int(target_id),
            created_by=request.user,
        )
        record.signature_image.save(filename, image_file, save=False)
        record.save()

        return JsonResponse({'ok': True})
