import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from records.models import DailyRecord

from . import services
from .models import AddonSuggestion, ReferenceDocument
from .retrieval import index_document

logger = logging.getLogger(__name__)


def _back_to_record(record):
    return redirect(f'/records/{record.beneficiary_id}/?selected={record.pk}')


class AdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'この操作は管理者だけができます。')
            return redirect('facilities:settings')
        return super().dispatch(request, *args, **kwargs)


# =============================================
# 加算提案
# =============================================
class SuggestionRefreshView(LoginRequiredMixin, View):
    def post(self, request, record_pk):
        record = get_object_or_404(DailyRecord, pk=record_pk, facility=request.user.facility)
        created = services.generate_addon_suggestions(record)
        if created:
            messages.success(request, f'AIが加算を {len(created)} 件提案しました。')
        elif not services.settings.ANTHROPIC_API_KEY:
            messages.warning(request, 'ANTHROPIC_API_KEY が設定されていないため提案できません。')
        else:
            messages.info(request, '提案できる加算はありませんでした（既に適用済み・見送り済みの加算は除きます）。')
        return _back_to_record(record)


class SuggestionDecideView(LoginRequiredMixin, View):
    def post(self, request, pk, action):
        s = get_object_or_404(AddonSuggestion.objects.select_related('daily_record', 'addon'),
                              pk=pk, facility=request.user.facility)
        if action == 'adopt':
            services.adopt_suggestion(s, request.user)
            messages.success(request, f'「{s.addon.name}」を請求マトリックスに反映しました。ポップアップを開いて内容を確認・保存してください（◆）。')
        elif action == 'dismiss':
            services.dismiss_suggestion(s, request.user)
            messages.info(request, f'「{s.addon.name}」の提案を見送りました。')
        elif action == 'reopen':
            services.reopen_suggestion(s)
            messages.info(request, f'「{s.addon.name}」の提案を未対応に戻しました。')
        return _back_to_record(s.daily_record)


# =============================================
# チャットボット
# =============================================
class ChatView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'error': 'リクエストの形式が不正です。'}, status=400)
        message = str(data.get('message', '')).strip()
        if not message:
            return JsonResponse({'error': '質問を入力してください。'}, status=400)
        if not services.settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'ANTHROPIC_API_KEY が設定されていないため、チャットは使えません。'}, status=500)
        facility = request.user.facility
        beneficiary, candidates = services.resolve_beneficiary(facility, message, data.get('beneficiary_id'))
        if candidates:
            return JsonResponse({
                'question': '同じ名前の利用者が複数います。どちらの利用者についてですか？',
                'choices': [{'id': b.pk, 'name': f'{b.full_name}（{b.full_name_kana}）'} for b in candidates],
            })
        try:
            reply = services.chat_reply(facility, request.user, message, data.get('history') or [], beneficiary)
        except Exception as e:  # noqa: BLE001
            logger.exception('チャットの回答生成に失敗')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)
        return JsonResponse({
            'reply': reply,
            'beneficiary': {'id': beneficiary.pk, 'name': beneficiary.full_name} if beneficiary else None,
        })


# =============================================
# 算定要件資料
# =============================================
class DocumentUploadView(AdminRequiredMixin, View):
    def post(self, request):
        f = request.FILES.get('file')
        title = request.POST.get('title', '').strip()
        if not f:
            messages.error(request, 'PDFファイルを選択してください。')
            return redirect('facilities:settings')
        if not f.name.lower().endswith('.pdf'):
            messages.error(request, 'PDFファイルのみアップロードできます。')
            return redirect('facilities:settings')
        if f.size > 30 * 1024 * 1024:
            messages.error(request, 'ファイルサイズは 30MB までです。')
            return redirect('facilities:settings')
        doc = ReferenceDocument.objects.create(
            facility=request.user.facility, title=title or f.name.rsplit('.', 1)[0][:200], file=f, uploaded_by=request.user)
        messages.success(request, f'「{doc.title}」をアップロードしました。続けて「ベクトル化する」を押してください。')
        return redirect('facilities:settings')


class DocumentIndexView(AdminRequiredMixin, View):
    def post(self, request, pk):
        doc = get_object_or_404(ReferenceDocument, pk=pk, facility=request.user.facility)
        try:
            n = index_document(doc)
        except Exception as e:  # noqa: BLE001
            logger.exception('資料のベクトル化に失敗（ID %s）', doc.pk)
            doc.error = f'{type(e).__name__}: {e}'[:500]
            doc.save(update_fields=['error'])
            messages.error(request, f'ベクトル化に失敗しました: {doc.error}')
            return redirect('facilities:settings')
        if n == 0:
            messages.warning(request, f'「{doc.title}」から文字を取り出せませんでした（画像だけのPDFの可能性があります）。')
        else:
            messages.success(request, f'「{doc.title}」をベクトル化しました（{doc.page_count}ページ・{n}チャンク）。加算提案とチャットが参照します。')
        return redirect('facilities:settings')


class DocumentDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        doc = get_object_or_404(ReferenceDocument, pk=pk, facility=request.user.facility)
        title = doc.title
        doc.file.delete(save=False)
        doc.delete()
        messages.success(request, f'「{title}」を削除しました。')
        return redirect('facilities:settings')


class TranscribeView(LoginRequiredMixin, View):
    """
    画面で録った音声（WAV・1分未満）を文字にして返す。iPhone の音声入力で使う（static/js/voice-input.js）。
    音声は保存しない。
    """

    def post(self, request):
        from . import speech
        f = request.FILES.get('audio')
        if f is None:
            return JsonResponse({'error': '音声が届いていません。'}, status=400)
        if f.size > speech.MAX_BYTES + 4096:
            return JsonResponse({'error': '音声が長すぎます（1回に送れるのは1分まで）。'}, status=400)
        try:
            text = speech.transcribe(f.read())
        except speech.SpeechError as e:
            return JsonResponse({'error': str(e)}, status=400 if '形式' in str(e) or '長すぎ' in str(e) else 502)
        return JsonResponse({'text': text})
