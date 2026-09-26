import datetime
import json
import base64
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.views import View
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.http import Http404, JsonResponse
from django.contrib import messages
from django.db import models as db_models
from django.conf import settings
import anthropic

from .models import Beneficiary, BeneficiaryAssessment, BeneficiaryOffice, Guardian, RecipientCertificate, BeneficiaryDocument, DOCUMENT_EXTENSIONS, DOCUMENT_MAX_BYTES
from .forms import BeneficiaryForm, BeneficiaryOfficeForm, GuardianForm, RecipientCertificateForm
from facilities.context_processors import get_terms
from config.concurrency import check_conflict


# =============================================
# 利用者一覧
# =============================================
class BeneficiaryListView(LoginRequiredMixin, ListView):
    """
    利用者一覧。ログインユーザーの施設に紐づく利用者のみ表示する。
    """
    model = Beneficiary
    template_name = 'beneficiaries/list.html'
    context_object_name = 'beneficiaries'

    def get_queryset(self):
        qs = Beneficiary.objects.filter(facility=self.request.user.facility).prefetch_related('guardians')
        # 氏名・かなで検索
        q = self.request.GET.get('q', '')
        if q:
            qs = qs.filter(
                db_models.Q(last_name__icontains=q) |
                db_models.Q(first_name__icontains=q) |
                db_models.Q(last_name_kana__icontains=q) |
                db_models.Q(first_name_kana__icontains=q)
            )
        # 在籍状況フィルタ（デフォルトは在籍中のみ）
        status = self.request.GET.get('status', Beneficiary.STATUS_ACTIVE)
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['status'] = self.request.GET.get('status', Beneficiary.STATUS_ACTIVE)
        return ctx


# =============================================
# 利用者詳細（支援の流れナビ付き）
# =============================================
class BeneficiaryDetailView(LoginRequiredMixin, DetailView):
    """
    利用者詳細。保護者・受給者証・支援計画の流れを1画面で確認できる。
    """
    model = Beneficiary
    template_name = 'beneficiaries/detail.html'
    context_object_name = 'beneficiary'

    def get_queryset(self):
        # 自施設の利用者のみアクセス可能
        return Beneficiary.objects.filter(facility=self.request.user.facility)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        b = self.object
        today = datetime.date.today()
        ctx['guardians'] = b.guardians.all()
        ctx['certificates'] = b.recipient_certificates.all()
        ctx['offices'] = b.offices.all()
        ctx['assessments'] = b.assessments.select_related('created_by')
        ctx['assessment_kinds'] = BeneficiaryAssessment.KIND_CHOICES
        ctx['documents'] = b.documents.select_related('uploaded_by') if self.request.user.facility.is_ryoiku else []
        ctx['document_accept'] = 'image/*,.pdf,.xlsx,.xls,.csv'
        ctx['document_max_mb'] = DOCUMENT_MAX_BYTES // (1024 * 1024)
        ctx['office_form'] = BeneficiaryOfficeForm()
        ctx['guardian_form'] = GuardianForm()
        ctx['certificate_form'] = RecipientCertificateForm()
        # 編集モーダル用に現在の利用者データをセットしたフォームを渡す
        ctx['edit_form'] = BeneficiaryForm(instance=b)
        # 曜日チェックボックス用（フィールド名と表示名のペア）
        ctx['edit_weekdays'] = [
            ('weekday_mon', '月'), ('weekday_tue', '火'), ('weekday_wed', '水'),
            ('weekday_thu', '木'), ('weekday_fri', '金'), ('weekday_sat', '土'),
        ]
        # 受給者証の期限アラート判定用
        ctx['today'] = today
        ctx['cert_expiry_soon'] = today + datetime.timedelta(days=30)
        # 個別支援計画（進行中のものを先頭に）
        plans = list(b.support_plans.select_related('manager'))
        ctx['support_plans'] = plans
        ctx['current_plan'] = next((p for p in plans if p.status != 'closed'), None)
        return ctx


# =============================================
# 利用者 新規登録
# =============================================
class BeneficiaryCreateView(LoginRequiredMixin, CreateView):
    model = Beneficiary
    form_class = BeneficiaryForm
    template_name = 'beneficiaries/form.html'

    def form_valid(self, form):
        # 施設を自動でセット（手入力させない）
        form.instance.facility = self.request.user.facility
        # 先に保存を完了させてからメッセージを追加する（保存失敗時にメッセージが残らないよう順番を逆にする）
        response = super().form_valid(form)
        messages.success(self.request, f'{get_terms(self.request.user)["beneficiary"]}「{self.object.full_name}」を登録しました。')
        return response

    def get_success_url(self):
        return reverse('beneficiaries:detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = f'{get_terms(self.request.user)["beneficiary"]} 新規登録'
        return ctx


# =============================================
# 利用者 編集
# =============================================
class BeneficiaryUpdateView(LoginRequiredMixin, UpdateView):
    model = Beneficiary
    form_class = BeneficiaryForm
    template_name = 'beneficiaries/form.html'

    def get_queryset(self):
        return Beneficiary.objects.filter(facility=self.request.user.facility)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        conflict = check_conflict(request, self.object)
        if conflict:
            messages.error(request, conflict)
            form = self.get_form()
            return self.render_to_response(self.get_context_data(form=form, conflict=conflict))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, f'{get_terms(self.request.user)["beneficiary"]}情報を更新しました。')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('beneficiaries:detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = f'{get_terms(self.request.user)["beneficiary"]}編集 — {self.object.full_name}'
        return ctx


# =============================================
# 保護者 追加
# =============================================
class GuardianCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から保護者を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        form = GuardianForm(request.POST)
        if form.is_valid():
            guardian = form.save(commit=False)
            guardian.beneficiary = beneficiary
            guardian.save()
            messages.success(request, '保護者を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 保護者 編集
# =============================================
class GuardianUpdateView(LoginRequiredMixin, View):
    """利用者詳細画面から保護者情報を編集する"""

    def post(self, request, beneficiary_pk, guardian_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        guardian = get_object_or_404(Guardian, pk=guardian_pk, beneficiary=beneficiary)
        conflict = check_conflict(request, guardian)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:detail', pk=beneficiary_pk)
        form = GuardianForm(request.POST, instance=guardian)
        if form.is_valid():
            form.save()
            messages.success(request, '保護者情報を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 利用事業所（上限額管理の相手先） 追加 / 編集 / 削除
# =============================================
class BeneficiaryOfficeCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から利用事業所を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        form = BeneficiaryOfficeForm(request.POST)
        if form.is_valid():
            office = form.save(commit=False)
            office.beneficiary = beneficiary
            if office.is_this_office:
                # 当施設の行は施設設定の名前・番号をそのまま使う
                office.name = office.name or request.user.facility.name
                office.office_number = office.office_number or request.user.facility.office_number
                beneficiary.offices.filter(is_this_office=True).update(is_this_office=False)
            office.save()
            messages.success(request, '利用事業所を追加しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class BeneficiaryOfficeUpdateView(LoginRequiredMixin, View):
    """利用事業所を編集する"""

    def post(self, request, beneficiary_pk, office_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        office = get_object_or_404(BeneficiaryOffice, pk=office_pk, beneficiary=beneficiary)
        form = BeneficiaryOfficeForm(request.POST, instance=office)
        if form.is_valid():
            office = form.save(commit=False)
            if office.is_this_office:
                beneficiary.offices.filter(is_this_office=True).exclude(pk=office.pk).update(is_this_office=False)
            office.save()
            messages.success(request, '利用事業所を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class BeneficiaryOfficeDeleteView(LoginRequiredMixin, View):
    """利用事業所を削除する（POSTのみ）"""

    def post(self, request, beneficiary_pk, office_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        office = get_object_or_404(BeneficiaryOffice, pk=office_pk, beneficiary=beneficiary)
        office.delete()
        messages.success(request, '利用事業所を削除しました。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# 受給者証 追加 / 編集
# =============================================
class RecipientCertificateCreateView(LoginRequiredMixin, View):
    """利用者詳細画面から受給者証を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        form = RecipientCertificateForm(request.POST, request.FILES)
        if form.is_valid():
            cert = form.save(commit=False)
            cert.beneficiary = beneficiary
            cert.save()
            messages.success(request, '受給者証を登録しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


class RecipientCertificateUpdateView(LoginRequiredMixin, View):
    """利用者詳細画面から受給者証を編集する"""

    def post(self, request, beneficiary_pk, cert_pk):
        beneficiary = get_object_or_404(
            Beneficiary, pk=beneficiary_pk, facility=request.user.facility
        )
        cert = get_object_or_404(RecipientCertificate, pk=cert_pk, beneficiary=beneficiary)
        conflict = check_conflict(request, cert)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:detail', pk=beneficiary_pk)
        form = RecipientCertificateForm(request.POST, request.FILES, instance=cert)
        if form.is_valid():
            form.save()
            messages.success(request, '受給者証を更新しました。')
        else:
            messages.error(request, '入力内容を確認してください。')
        return redirect('beneficiaries:detail', pk=beneficiary_pk)


# =============================================
# OCR：受給者証の画像をClaude Vision APIで読み取る
# =============================================
class RecipientCertificateOcrView(LoginRequiredMixin, View):
    """
    アップロードされた受給者証の画像を Claude Vision API に送り、
    必要項目を抽出してJSONで返す。フロント側でフォームに自動入力する。
    """

    def post(self, request, beneficiary_pk):
        # 施設チェック
        get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)

        image_file = request.FILES.get('image')
        if not image_file:
            return JsonResponse({'error': '画像が選択されていません。'}, status=400)

        # ファイルサイズチェック（5MB上限）
        MAX_SIZE = 5 * 1024 * 1024
        if image_file.size > MAX_SIZE:
            return JsonResponse({'error': '画像ファイルは5MB以下にしてください。'}, status=400)

        # MIMEタイプをPillowで実際に判定（content_typeはユーザーが偽造できるため）
        from PIL import Image
        import io
        ALLOWED_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'GIF': 'image/gif'}
        try:
            img = Image.open(io.BytesIO(image_file.read()))
            media_type = ALLOWED_TYPES.get(img.format, '')
            if not media_type:
                return JsonResponse({'error': '対応していない画像形式です（JPEG・PNG・WEBP・GIFのみ）。'}, status=400)
        except Exception:
            return JsonResponse({'error': '画像ファイルを読み込めませんでした。'}, status=400)
        image_file.seek(0)

        # 画像をBase64エンコード（APIに送るため）
        image_data = base64.standard_b64encode(image_file.read()).decode('utf-8')

        prompt = """
この画像は放課後等デイサービスの受給者証です。
以下の項目を読み取り、JSONのみで返してください（説明文は不要）。
読み取れなかった項目は空文字にしてください。

{
  "certificate_number": "受給者証番号（数字・ハイフン）",
  "granted_days": "支給量（月あたり日数。数字のみ）",
  "monthly_cap": "負担上限月額（数字のみ、円マーク不要）",
  "valid_from": "有効期間の開始日（YYYY-MM-DD形式）",
  "valid_until": "有効期間の終了日（YYYY-MM-DD形式）",
  "municipality": "市区町村名",
  "support_office": "相談支援事業所名"
}
"""

        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model='claude-opus-4-6',
                max_tokens=512,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': image_data,
                            },
                        },
                        {'type': 'text', 'text': prompt},
                    ],
                }],
            )
            # レスポンスからJSONを取り出す
            text = response.content[0].text.strip()
            # コードブロック（```json ... ```）が含まれる場合は取り除く
            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            extracted = json.loads(text)
            return JsonResponse({'result': extracted})

        except json.JSONDecodeError:
            return JsonResponse({'error': 'AIの返答をJSONに変換できませんでした。'}, status=500)
        except Exception as e:
            return JsonResponse({'error': f'OCR処理中にエラーが発生しました: {str(e)}'}, status=500)


class RegenerateLineCodeView(LoginRequiredMixin, View):
    """
    LINE登録コードを再発行する。
    古いコードは無効になり、新しいコードが発行される。
    """
    def post(self, request, guardian_pk):
        guardian = get_object_or_404(
            Guardian, pk=guardian_pk,
            beneficiary__facility=request.user.facility
        )
        guardian.issue_line_code()
        guardian.save(update_fields=['line_registration_code', 'line_code_expires_at', 'updated_at'])
        messages.success(request, f'{guardian}のLINE登録コードを再発行しました（72時間有効）。')
        return redirect('beneficiaries:detail', pk=guardian.beneficiary_id)



# =============================================
# アセスメント・資料（台帳に付ける記録と書類）
# =============================================
ASSESSMENT_EXTS = ('pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic', 'gif')
ASSESSMENT_MAX_SIZE = 20 * 1024 * 1024


def _assessment_fields(request):
    """フォームの値を読む。(値の dict, エラーの文 or None)"""
    p = request.POST
    try:
        date = datetime.date.fromisoformat(p.get('date') or '')
    except ValueError:
        return None, '日付を入れてください。'
    title = (p.get('title') or '').strip()[:100]
    kinds = dict(BeneficiaryAssessment.KIND_CHOICES)
    kind = p.get('kind') if p.get('kind') in kinds else BeneficiaryAssessment.KIND_ASSESSMENT
    fields = {'date': date, 'kind': kind, 'title': title or kinds[kind], 'content': (p.get('content') or '').strip()[:20000]}
    f = request.FILES.get('file')
    if f:
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in ASSESSMENT_EXTS:
            return None, '書類は PDF か写真（JPEG・PNG など）にしてください。'
        if f.size > ASSESSMENT_MAX_SIZE:
            return None, '書類が大きすぎます（20MB まで）。'
        fields['file'] = f
        fields['file_name'] = f.name[:200]
    return fields, None


DOCUMENT_MAX_FILES = 20


class DocumentUploadView(LoginRequiredMixin, View):
    """利用者の基本情報に書類・画像（写真・PDF・Excel・CSV）を付ける。1回に 20 個まで。ゆあーず（療育の型）だけ"""

    def post(self, request, beneficiary_pk):
        facility = request.user.facility
        if not facility.is_ryoiku:
            raise Http404
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        back = redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")
        files = request.FILES.getlist('files')[:DOCUMENT_MAX_FILES]
        title = (request.POST.get('title') or '').strip()[:100]
        made, skipped = 0, []
        for f in files:
            ext = (f.name or '').lower().rsplit('.', 1)[-1] if '.' in (f.name or '') else ''
            if ext not in DOCUMENT_EXTENSIONS:
                skipped.append(f'{f.name}（この種類は入れられません）')
                continue
            if f.size > DOCUMENT_MAX_BYTES:
                skipped.append(f'{f.name}（{DOCUMENT_MAX_BYTES // (1024 * 1024)} MB を超えています）')
                continue
            BeneficiaryDocument.objects.create(beneficiary=beneficiary, file=f, file_name=(f.name or '')[:200],
                                               title=title if len(files) == 1 else '', uploaded_by=request.user)
            made += 1
        if made:
            messages.success(request, f'書類を {made} 件取り込みました。')
        if skipped:
            messages.error(request, '取り込めなかったもの：' + '、'.join(skipped))
        if not made and not skipped:
            messages.error(request, 'ファイルを選ぶか、枠の中に置いてください（写真・PDF・Excel・CSV）。')
        return back


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, beneficiary_pk, document_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        d = get_object_or_404(beneficiary.documents, pk=document_pk)
        label = d.label
        if d.file:
            d.file.delete(save=False)
        d.delete()
        messages.success(request, f'「{label}」を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")


class AssessmentCreateView(LoginRequiredMixin, View):
    """利用者詳細画面からアセスメント・資料を追加する"""

    def post(self, request, beneficiary_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        fields, error = _assessment_fields(request)
        if error is None and not fields['content'] and 'file' not in fields:
            error = '内容を書くか、書類を付けてください。'
        if error:
            messages.error(request, error)
        else:
            BeneficiaryAssessment.objects.create(beneficiary=beneficiary, created_by=request.user, **fields)
            messages.success(request, f'「{fields["title"]}」を登録しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")


class AssessmentUpdateView(LoginRequiredMixin, View):
    """アセスメント・資料を直す（書類を付け替える・外すこともできる）"""

    def post(self, request, beneficiary_pk, assessment_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        a = get_object_or_404(beneficiary.assessments, pk=assessment_pk)
        fields, error = _assessment_fields(request)
        if error:
            messages.error(request, error)
            return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")
        old_file = a.file.name if a.file else ''
        if request.POST.get('remove_file') and 'file' not in fields:
            fields['file'], fields['file_name'] = '', ''
        for k, v in fields.items():
            setattr(a, k, v)
        a.save()
        if old_file and old_file != (a.file.name if a.file else ''):
            a.file.storage.delete(old_file)
        messages.success(request, f'「{a.title}」を保存しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")


class AssessmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, beneficiary_pk, assessment_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        a = get_object_or_404(beneficiary.assessments, pk=assessment_pk)
        if a.file:
            a.file.delete(save=False)
        a.delete()
        messages.success(request, f'「{a.title}」を削除しました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")
