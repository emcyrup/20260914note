import datetime
import json
import base64
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.views import View
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import Http404, JsonResponse
from django.contrib import messages
from django.db import models as db_models
from django.conf import settings
import anthropic

from .models import Beneficiary, BeneficiaryAssessment, BeneficiaryOffice, Guardian, RecipientCertificate, BeneficiaryDocument, DOCUMENT_EXTENSIONS, DOCUMENT_MAX_BYTES, KANA_ROWS
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
        # 50 音の行で絞る（row=か など。row=他 はふりがなの無い人）
        row = self.request.GET.get('row', '')
        chars = dict(KANA_ROWS).get(row)
        if chars:
            cond = db_models.Q()
            for ch in chars:
                cond |= db_models.Q(last_name_kana__startswith=ch)
            qs = qs.filter(cond)
        elif row == '他':
            qs = qs.filter(last_name_kana='')
        # 50 音順。ふりがなの無い人は最後（かなが空だと先頭に来てしまうので）
        return qs.annotate(no_kana=db_models.Case(db_models.When(last_name_kana='', then=1), default=0,
                                                  output_field=db_models.IntegerField())) \
                 .order_by('no_kana', 'last_name_kana', 'first_name_kana', 'last_name', 'first_name', 'pk')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        ctx['status'] = self.request.GET.get('status', Beneficiary.STATUS_ACTIVE)
        ctx['row'] = self.request.GET.get('row', '')
        ctx['kana_rows'] = [r for r, _ in KANA_ROWS]
        return ctx


# =============================================
# 利用者の削除（退所した人だけ。管理者だけ）
# =============================================
RETENTION_NOTE = '障害児通所支援の記録（支援計画・サービス提供の記録など）は、サービスを終えた日から 5 年間の保存が求められています。'


def related_counts(b):
    """削除のときに一緒に消えるものの件数（画面で確かめてもらう）"""
    from records.models import DailyRecord
    rows = [
        ('療育記録', b.therapy_records.count()),
        ('予約', b.reservations.count()),
        ('月の利用希望', b.monthly_requests.count()),
        ('アセスメント・資料', b.assessments.count()),
        ('書類・画像', b.documents.count()),
        ('書類から分かっていること', b.knowledge.count()),
        ('個別支援計画', b.support_plans.count()),
        ('日誌（記録）', DailyRecord.objects.filter(beneficiary=b).count()),
        ('来所予定', b.scheduled_visits.count()),
        ('受給者証', b.recipient_certificates.count()),
        ('保護者', b.guardians.count()),
    ]
    return [(label, n) for label, n in rows if n]


class BeneficiaryDeleteView(LoginRequiredMixin, View):
    """
    退所した利用者を、関連する記録・書類ごと消す。戻せないので確認画面を挟み、管理者だけができる。
    在籍中の人は消せない（先に「編集」で在籍状況を「退所」にする）。
    """

    def _get(self, request, pk):
        b = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        if not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, f'{get_terms(request.user.facility)["beneficiary"]}の削除は管理者だけができます。')
            return b, redirect('beneficiaries:detail', pk=pk)
        if b.status != Beneficiary.STATUS_INACTIVE:
            messages.error(request, '在籍中の人は削除できません。先に「編集」で在籍状況を「退所」にしてください。')
            return b, redirect('beneficiaries:detail', pk=pk)
        return b, None

    def get(self, request, pk):
        b, bounce = self._get(request, pk)
        if bounce:
            return bounce
        return render(request, 'beneficiaries/delete.html',
                      {'beneficiary': b, 'counts': related_counts(b), 'retention_note': RETENTION_NOTE})

    def post(self, request, pk):
        b, bounce = self._get(request, pk)
        if bounce:
            return bounce
        if request.POST.get('confirm_name', '').replace('\u3000', ' ').strip() != b.full_name.strip() \
                or not request.POST.get('agree'):
            messages.error(request, '氏名が合っていないか、確認の印が付いていません。削除していません。')
            return redirect('beneficiaries:delete', pk=pk)
        name = b.full_name
        # ファイルは DB を消したあとに消す（途中で失敗しても DB と食い違わないように）
        from records.models import DailyRecordPhoto
        files = [d.file for d in b.documents.all()] + [a.file for a in b.assessments.all() if a.file] \
            + [c.scanned_image for c in b.recipient_certificates.all() if c.scanned_image] \
            + [ph.photo for ph in DailyRecordPhoto.objects.filter(daily_record__beneficiary=b) if ph.photo]
        b.support_plans.all().delete()        # PROTECT なので先に消す
        b.delete()
        for f in files:
            try:
                f.storage.delete(f.name)
            except Exception:       # ファイルが既に無いなどは無視（DB は消えている）
                pass
        messages.success(request, f'「{name}」を削除しました。')
        return redirect(f"{reverse('beneficiaries:list')}?status=inactive")


# =============================================
# 利用者詳細（支援の流れナビ付き）
# =============================================
RECENT_THERAPY_DAYS = 5     # 利用者の詳細に出す療育記録の日数（ゆあーず）


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
        # 書類から分かっていること（診断書などを AI で読み取ったもの。ゆあーず）
        ctx['knowledge'], ctx['knowledge_drafts'], ctx['ai_ready'] = [], [], bool(settings.ANTHROPIC_API_KEY)
        if self.request.user.facility.is_ryoiku:
            from . import knowledge as kn
            items = list(b.knowledge.select_related('created_by'))
            ctx['knowledge'] = [k for k in items if k.is_saved]
            ctx['knowledge_drafts'] = [k for k in items if not k.is_saved]
            read_doc = {k.document_id: k for k in reversed(items) if k.document_id}
            read_asm = {k.assessment_id: k for k in reversed(items) if k.assessment_id}
            docs = list(ctx['documents'])
            for d in docs:
                d.readable = kn.is_readable(d.file_name or d.file.name)
                d.read_k = read_doc.get(d.pk)
            ctx['documents'] = docs
            asms = list(ctx['assessments'])
            for a in asms:
                a.readable = bool(a.file) and kn.is_readable(a.file_name or a.file.name)
                a.read_k = read_asm.get(a.pk)
            ctx['assessments'] = asms
        ctx['document_accept'] = 'image/*,.pdf,.xlsx,.xls,.csv'
        ctx['document_max_mb'] = DOCUMENT_MAX_BYTES // (1024 * 1024)
        ctx['office_form'] = BeneficiaryOfficeForm()
        ctx['guardian_form'] = GuardianForm()
        ctx['certificate_form'] = RecipientCertificateForm()
        # 編集モーダル用に現在の利用者データをセットしたフォームを渡す
        ctx['edit_form'] = BeneficiaryForm(instance=b, facility=b.facility)
        ctx['pair_names'] = b.pair_names()
        ctx['pair_ids'] = set(b.cannot_pair.values_list('pk', flat=True))
        # 曜日チェックボックス用（フィールド名と表示名のペア）
        ctx['edit_weekdays'] = [
            ('weekday_mon', '月'), ('weekday_tue', '火'), ('weekday_wed', '水'),
            ('weekday_thu', '木'), ('weekday_fri', '金'), ('weekday_sat', '土'),
        ]
        # 受給者証の期限アラート判定用
        ctx['today'] = today
        ctx['cert_expiry_soon'] = today + datetime.timedelta(days=30)
        # 療育記録の直近5日分（ゆあーず。日付の新しい順に5日ぶん。同じ日に2件あればどちらも出す）
        ctx['recent_therapy'] = []
        if self.request.user.facility.is_ryoiku and getattr(self.request.user.facility, 'use_therapy_record', False):
            days = list(b.therapy_records.order_by('-date').values_list('date', flat=True).distinct()[:RECENT_THERAPY_DAYS])
            if days:
                ctx['recent_therapy'] = list(b.therapy_records.filter(date__in=days).select_related('staff')
                                             .order_by('-date', '-time', '-pk'))
            ctx['therapy_total'] = b.therapy_records.count()
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

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'facility': self.request.user.facility}

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

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'facility': self.request.user.facility}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['pair_ids'] = set(self.object.cannot_pair.values_list('pk', flat=True)) if self.object else set()
        return ctx

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


class DocumentRenameView(LoginRequiredMixin, View):
    """書類・画像の名前（件名）を変える。元のファイル名は残す"""

    def post(self, request, beneficiary_pk, document_pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        d = get_object_or_404(beneficiary.documents, pk=document_pk)
        title = (request.POST.get('title') or '').strip()[:100]
        if not title:
            messages.error(request, '名前を入れてください。')
        elif title != d.title:
            d.title = title
            d.save(update_fields=['title'])
            messages.success(request, f'書類の名前を「{title}」にしました。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#documents")


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
        conflict = check_conflict(request, a)
        if conflict:
            messages.error(request, conflict)
            return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#assessments")
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


# =============================================
# 利用者情報の Excel・CSV 取り込み（ゆあーず）
# =============================================
IMPORT_SESSION_KEY = 'beneficiary_import_rows'


class ImportRyoikuMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and (facility is None or not facility.is_ryoiku):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class BeneficiaryImportTemplateView(ImportRyoikuMixin, View):
    """取り込みの雛形（Excel）"""

    def get(self, request):
        from django.http import HttpResponse
        from . import importer
        res = HttpResponse(importer.template_xlsx(),
                           content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        res['Content-Disposition'] = "attachment; filename*=UTF-8''%E5%88%A9%E7%94%A8%E8%80%85_%E5%8F%96%E3%82%8A%E8%BE%BC%E3%81%BF%E9%9B%9B%E5%BD%A2.xlsx"
        return res


class BeneficiaryImportView(ImportRyoikuMixin, View):
    """
    Excel・CSV から利用者を取り込む。1) ファイルを選ぶ → 2) 行ごとの見込み（新規・書き換え・エラー）を確かめる → 3) 登録する。
    見込みはセッションに置き、「登録する」でその内容を保存する
    """
    template_name = 'beneficiaries/import.html'

    def _ctx(self, request, **extra):
        from . import importer
        return {'columns': importer.COLUMNS, 'headers': importer.HEADERS, 'max_rows': importer.MAX_ROWS,
                'guardian_headers': importer.GUARDIAN_HEADERS, **extra}

    def get(self, request):
        from django.shortcuts import render
        request.session.pop(IMPORT_SESSION_KEY, None)
        return render(request, self.template_name, self._ctx(request))

    def post(self, request):
        from django.shortcuts import render
        from . import importer
        facility = request.user.facility
        create_children = request.POST.get('create_children') == '1'
        if request.POST.get('action') == 'commit':
            rows = request.session.pop(IMPORT_SESSION_KEY, None)
            if not rows:
                messages.error(request, '取り込む内容がありません。もう一度ファイルを選んでください。')
                return redirect('beneficiaries:import')
            if importer.format_of(rows) == importer.FORMAT_GUARDIAN:
                planned = importer.plan_guardians(facility, rows, create_children=create_children)
                made, guardians, errors = importer.apply_guardians(facility, planned)
                msg = f'保護者一覧を取り込みました：保護者 {guardians} 件（新しく作った児童 {made} 名）'
            else:
                planned = importer.plan(facility, rows)
                created, updated, errors = importer.apply(facility, planned)
                msg = f'利用者を取り込みました：新規 {created} 名・書き換え {updated} 名'
            if errors:
                msg += f'（読めなかった行 {errors} 件は登録していません）'
            messages.success(request, msg + '。')
            return redirect('beneficiaries:list')
        f = request.FILES.get('file')
        if f is None:
            messages.error(request, 'ファイルを選んでください（Excel の .xlsx か CSV）。')
            return render(request, self.template_name, self._ctx(request))
        try:
            rows = importer.rows_from_file(f)
        except Exception as e:  # noqa: BLE001 - 壊れたファイルなど。理由をそのまま見せる
            messages.error(request, f'ファイルを読めませんでした：{e}')
            return render(request, self.template_name, self._ctx(request))
        if not rows:
            messages.error(request, 'データの行がありません（1行目は見出し、2行目から利用者）。')
            return render(request, self.template_name, self._ctx(request))
        fmt = importer.format_of(rows)
        planned = (importer.plan_guardians(facility, rows, create_children=create_children) if fmt == importer.FORMAT_GUARDIAN
                   else importer.plan(facility, rows))
        request.session[IMPORT_SESSION_KEY] = rows
        counts = {k: sum(1 for p in planned if p['action'] == k) for k in ('create', 'update', 'error')}
        return render(request, self.template_name, self._ctx(request, planned=planned, counts=counts, file_name=f.name,
                                                             fmt=fmt, create_children=create_children))


# =============================================
# 書類の読み取り（診断書など）→ 台帳への反映・療育記録の AI の参考（ゆあーず）
# =============================================
class KnowledgeReadView(ImportRyoikuMixin, View):
    """書類・画像（またはアセスメント・資料の書類）を AI で読み取り、確認画面へ進む"""

    def post(self, request, beneficiary_pk):
        from . import knowledge
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        back = redirect(f"{reverse('beneficiaries:detail', args=[beneficiary_pk])}#knowledge")
        source = request.POST.get('source')
        document = assessment = None
        if source == 'assessment':
            assessment = get_object_or_404(beneficiary.assessments, pk=request.POST.get('source_pk') or 0)
            file_field, name, hint = assessment.file, assessment.file_name or assessment.file.name, f'{assessment.title}\n{assessment.content}'
        else:
            document = get_object_or_404(beneficiary.documents, pk=request.POST.get('source_pk') or 0)
            file_field, name, hint = document.file, document.file_name or document.file.name, document.title
        if not file_field:
            messages.error(request, '書類が付いていません。')
            return back
        try:
            data = knowledge.read_file(beneficiary, file_field, name, hint=hint)
        except knowledge.ReadError as e:
            messages.error(request, str(e))
            return back
        draft = knowledge.make_draft(beneficiary, data, user=request.user, document=document, assessment=assessment)
        return redirect('beneficiaries:knowledge_review', beneficiary_pk=beneficiary.pk, pk=draft.pk)


class KnowledgeReviewView(ImportRyoikuMixin, View):
    """読み取った内容の確認・修正。台帳への反映（確認待ちのときだけ）・保存・破棄"""
    template_name = 'beneficiaries/knowledge_review.html'

    def _get(self, request, beneficiary_pk, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        return beneficiary, get_object_or_404(beneficiary.knowledge.select_related('document', 'assessment'), pk=pk)

    def get(self, request, beneficiary_pk, pk):
        from django.shortcuts import render
        from . import knowledge
        beneficiary, k = self._get(request, beneficiary_pk, pk)
        return render(request, self.template_name, {
            'beneficiary': beneficiary, 'k': k, 'p': k.proposals or {}, 'kinds': knowledge.KINDS,
            'therapy': getattr(request.user.facility, 'use_therapy_record', False),
        })

    def post(self, request, beneficiary_pk, pk):
        from . import knowledge
        beneficiary, k = self._get(request, beneficiary_pk, pk)
        back = f"{reverse('beneficiaries:detail', args=[beneficiary.pk])}#knowledge"
        action = request.POST.get('action')
        if action in ('discard', 'delete'):
            label = k.label
            k.delete()
            messages.success(request, f'「{label}」の読み取り結果を消しました。' if action == 'discard'
                             else f'「{label}」を書類から分かっていることから消しました（台帳に反映した内容は残ります）。')
            return redirect(back)
        conflict = check_conflict(request, k)
        if conflict:
            messages.error(request, conflict)
            return redirect('beneficiaries:knowledge_review', beneficiary_pk=beneficiary.pk, pk=k.pk)
        was_draft = not k.is_saved
        applied = knowledge.save_reviewed(k, request.POST, user=request.user)
        msg = f'「{k.label}」を保存しました。'
        if applied:
            msg += '台帳に反映：' + '・'.join(applied) + '。'
        elif was_draft:
            msg += '台帳は変えていません。'
        msg += '療育記録の AI が参考にします。' if k.use_in_ai else '療育記録の AI では使いません。'
        messages.success(request, msg)
        return redirect(back)


class KnowledgeToggleView(ImportRyoikuMixin, View):
    """療育記録の AI で参考にする／しない を切り替える"""

    def post(self, request, beneficiary_pk, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=request.user.facility)
        k = get_object_or_404(beneficiary.knowledge, pk=pk)
        k.use_in_ai = not k.use_in_ai
        k.save(update_fields=['use_in_ai', 'updated_at'])
        messages.success(request, f'「{k.label}」を療育記録の AI で{"参考にします" if k.use_in_ai else "使わないようにしました"}。')
        return redirect(f"{reverse('beneficiaries:detail', args=[beneficiary.pk])}#knowledge")
