import json
import logging
from datetime import date, timedelta
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.conf import settings
import anthropic

logger = logging.getLogger(__name__)

from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord
from facilities.models import SupportContentTag
from schedules.models import ScheduledVisit
from .models import DailyRecord, ActivityTag, DailyRecordPhoto, StaffMemo


MAX_PHOTOS_PER_RECORD = 5  # 1件の日誌に添付できる写真の最大枚数


def _save_photos(request, record):
    """
    フォームからアップロードされた写真を DailyRecordPhoto に保存する。
    すでに上限枚数に達している場合は追加しない。
    """
    files = request.FILES.getlist('photos')
    if not files:
        return
    current_count = record.photos.count()
    remaining = MAX_PHOTOS_PER_RECORD - current_count
    for i, f in enumerate(files[:remaining]):
        DailyRecordPhoto.objects.create(
            facility=record.facility,
            daily_record=record,
            photo=f,
            order=current_count + i,
        )


# =============================================
# 日誌ダッシュボード（週間カレンダー）
# =============================================
class RecordsDashboardView(LoginRequiredMixin, TemplateView):
    """
    1週間分の来所予定者と日誌ステータスをカレンダー表示する。
    ナビバーの「日誌」から遷移するトップページ。
    ?week=YYYY-MM-DD で表示週を切り替え可能。
    """
    template_name = 'records/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facility = self.request.user.facility
        today = date.today()

        # 表示週の月曜日を決定
        week_str = self.request.GET.get('week')
        if week_str:
            try:
                base = date.fromisoformat(week_str)
            except ValueError:
                base = today
        else:
            base = today
        week_start = base - timedelta(days=base.weekday())  # 月曜日
        week_end   = week_start + timedelta(days=6)         # 日曜日

        week_dates = [week_start + timedelta(days=i) for i in range(7)]

        # 来所予定を取得
        visits = ScheduledVisit.objects.filter(
            facility=facility,
            date__range=(week_start, week_end),
        ).select_related('beneficiary').order_by('date', 'beneficiary__last_name_kana')

        # 日誌を取得
        records = DailyRecord.objects.filter(
            facility=facility,
            date__range=(week_start, week_end),
        )

        # (beneficiary_id, date) → record のマップ
        record_map = {(r.beneficiary_id, r.date): r for r in records}

        # 日付 → [{visit, record, record_status}] のマップ
        day_data = {d: [] for d in week_dates}
        for visit in visits:
            record = record_map.get((visit.beneficiary_id, visit.date))
            day_data[visit.date].append({
                'visit':         visit,
                'record':        record,
                'record_status': record.status if record else 'none',
            })

        # テンプレートで扱いやすいようにリスト形式に変換
        week_info = []
        weekday_names = ['月', '火', '水', '木', '金', '土', '日']
        for d in week_dates:
            week_info.append({
                'date':        d,
                'date_iso':    d.isoformat(),
                'weekday_ja':  weekday_names[d.weekday()],
                'is_today':    d == today,
                'is_saturday': d.weekday() == 5,
                'is_sunday':   d.weekday() == 6,
                'entries':     day_data[d],
            })

        # 利用者一覧（予定外来所の日誌作成用）
        beneficiaries = Beneficiary.objects.filter(
            facility=facility
        ).order_by('last_name_kana')

        # スタッフメモ（新しい順に20件）
        memos = StaffMemo.objects.filter(facility=facility).select_related('author')[:20]

        ctx.update({
            'week_info':     week_info,
            'today':         today,
            'week_start':    week_start,
            'week_end':      week_end,
            'prev_week':     (week_start - timedelta(days=7)).isoformat(),
            'next_week':     (week_start + timedelta(days=7)).isoformat(),
            'beneficiaries': beneficiaries,
            'memos':         memos,
        })
        return ctx


# =============================================
# スタッフメモ（ダッシュボードのメモボックス）
# =============================================

class StaffMemoCreateView(LoginRequiredMixin, View):
    """メモを新規作成してダッシュボードに戻る"""
    def post(self, request):
        content = request.POST.get('content', '').strip()
        if content:
            StaffMemo.objects.create(
                facility=request.user.facility,
                author=request.user,
                content=content,
            )
        return redirect('records:dashboard')


class StaffMemoDeleteView(LoginRequiredMixin, View):
    """メモを削除してダッシュボードに戻る（自施設のメモのみ）"""
    def post(self, request, pk):
        memo = get_object_or_404(StaffMemo, pk=pk, facility=request.user.facility)
        memo.delete()
        return redirect('records:dashboard')


class StaffMemoUpdateView(LoginRequiredMixin, View):
    """メモ本文を編集してダッシュボードに戻る（自施設のメモのみ）"""
    def post(self, request, pk):
        memo = get_object_or_404(StaffMemo, pk=pk, facility=request.user.facility)
        content = request.POST.get('content', '').strip()
        if content:
            memo.content = content
            memo.save()
        return redirect('records:dashboard')


# =============================================
# 日誌一覧＋詳細（左リスト・右詳細の2カラム）
# =============================================
class DailyRecordListView(LoginRequiredMixin, TemplateView):
    """
    利用者の日誌を左リスト・右詳細の2カラムで表示する（みちのーとUX）。
    ?selected=<pk> で右側に表示する日誌を指定する。
    """
    template_name = 'records/list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facility    = self.request.user.facility
        beneficiary = get_object_or_404(
            Beneficiary, pk=self.kwargs['beneficiary_pk'], facility=facility
        )

        records = DailyRecord.objects.filter(
            beneficiary=beneficiary
        ).prefetch_related('activity_tags', 'support_tags')

        # 選択中の日誌（右側に表示）
        selected_pk = self.request.GET.get('selected')
        selected_record = None
        if selected_pk:
            selected_record = records.filter(pk=selected_pk).first()
        elif records.exists():
            selected_record = records.first()  # デフォルトは最新

        # 新規入力モーダル用タグ一覧
        activity_tags = ActivityTag.objects.filter(facility=facility, is_active=True)
        support_tags  = SupportContentTag.objects.filter(facility=facility, is_active=True)

        ctx.update({
            'beneficiary':     beneficiary,
            'records':         records,
            'selected_record': selected_record,
            'activity_tags':   activity_tags,
            'support_tags':    support_tags,
            'status_choices':  DailyRecord.STATUS_CHOICES,
            'health_choices':  DailyRecord.HEALTH_CHOICES,
            'staff_list':      facility.staff_accounts.all(),
            'new_date':        self.request.GET.get('new_date', ''),
            'domains': [
                ('domain_health_life',        '健康・生活'),
                ('domain_motor_sensory',      '運動・感覚'),
                ('domain_cognition_behavior', '認知・行動'),
                ('domain_language_comm',      '言語・コミュニケーション'),
                ('domain_social',             '人間関係・社会性'),
            ],
        })

        # 選択中の日誌に紐づく電子サイン・写真を取得
        if ctx['selected_record']:
            ctx['signatures'] = EsignatureRecord.objects.filter(
                target_type='daily_record',
                target_id=ctx['selected_record'].pk,
            )
            ctx['photos'] = ctx['selected_record'].photos.all()
            ctx['photos_count'] = ctx['photos'].count()
            ctx['can_add_photo'] = ctx['photos_count'] < MAX_PHOTOS_PER_RECORD
        else:
            ctx['signatures'] = []
            ctx['photos'] = []
            ctx['photos_count'] = 0
            ctx['can_add_photo'] = True

        # 編集モーダル用：選択済みのタグ・domainキーをJSON形式で渡す
        if ctx['selected_record']:
            r = ctx['selected_record']
            domain_keys = [
                key for key in [
                    'domain_health_life', 'domain_motor_sensory',
                    'domain_cognition_behavior', 'domain_language_comm', 'domain_social',
                ] if getattr(r, key)
            ]
            ctx['selected_domain_keys_json'] = json.dumps(domain_keys)
            ctx['selected_support_tag_ids_json'] = json.dumps(
                list(r.support_tags.values_list('pk', flat=True))
            )
            ctx['selected_activity_tag_ids_json'] = json.dumps(
                list(r.activity_tags.values_list('pk', flat=True))
            )
        else:
            ctx['selected_domain_keys_json'] = '[]'
            ctx['selected_support_tag_ids_json'] = '[]'
            ctx['selected_activity_tag_ids_json'] = '[]'
        return ctx


# =============================================
# LINE送信ヘルパー（作成・更新ビュー共用）
# =============================================
def _send_line_for_record(request, record):
    """
    保存済みの日誌レコードに対してLINE Push Messageを送信する。
    保護者未連携やトークン未設定の場合は警告メッセージを表示して処理を続ける（例外はraiseしない）。
    """
    from linebot.v3.messaging import (
        ApiClient, Configuration, MessagingApi,
        PushMessageRequest, TextMessage as LineTextMessage,
    )
    from line_integration.models import LineDeliveryLog

    facility = request.user.facility

    if not record.parent_message_draft:
        messages.warning(request, 'メッセージが空のためLINE送信をスキップしました。')
        return

    guardian = (
        record.beneficiary.guardians.filter(line_linked=True, is_primary=True).first()
        or record.beneficiary.guardians.filter(line_linked=True).first()
    )
    if not guardian:
        messages.warning(request, f'{record.beneficiary.full_name}様の保護者がLINE未連携のため送信できませんでした。')
        return

    if not facility.line_channel_access_token:
        messages.warning(request, 'LINEチャネルアクセストークンが未設定のため送信できませんでした。')
        return

    send_text = (
        f'{record.beneficiary.full_name}さんの本日の様子をご報告します。\n\n'
        f'{record.parent_message_draft}\n\n'
        f'次回もお待ちしております。'
    )
    is_success = False
    error_message = ''
    config = Configuration(access_token=facility.line_channel_access_token)
    try:
        with ApiClient(config) as api_client:
            api = MessagingApi(api_client)
            api.push_message(PushMessageRequest(
                to=guardian.line_user_id,
                messages=[LineTextMessage(type='text', text=send_text)]
            ))
        is_success = True
    except Exception as e:
        error_message = str(e)

    LineDeliveryLog.objects.create(
        facility=facility,
        guardian=guardian,
        daily_record=record,
        content=send_text,
        is_success=is_success,
        error_message=error_message,
    )
    if is_success:
        messages.success(request, f'{guardian}へLINEメッセージを送信しました。')
    else:
        messages.error(request, f'LINE送信に失敗しました：{error_message}')


# =============================================
# 日誌 新規作成
# =============================================
class DailyRecordCreateView(LoginRequiredMixin, View):
    """モーダルフォームからPOSTで日誌を新規作成する"""

    def post(self, request, beneficiary_pk):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)

        date = request.POST.get('date')
        if not date:
            messages.error(request, '記録日を入力してください。')
            return redirect('records:list', beneficiary_pk=beneficiary_pk)

        p = request.POST
        # 担当者IDの検証（自施設の職員のみ受け付ける）
        author_pk = p.get('author')
        if author_pk and facility.staff_accounts.filter(pk=author_pk).exists():
            author_id = author_pk
        else:
            author_id = request.user.pk
        defaults = {
            'facility':                facility,
            'author_id':               author_id,
            'entry_time':              p.get('entry_time') or None,
            'exit_time':               p.get('exit_time') or None,
            'health_condition':        p.get('health_condition', DailyRecord.HEALTH_GOOD),
            'health_note':             p.get('health_note', ''),
            'domain_health_life':      'domain_health_life' in p,
            'domain_motor_sensory':    'domain_motor_sensory' in p,
            'domain_cognition_behavior': 'domain_cognition_behavior' in p,
            'domain_language_comm':    'domain_language_comm' in p,
            'domain_social':           'domain_social' in p,
            'activity_name':           p.get('activity_name', '').strip()[:100],
            'activity_aim':            p.get('activity_aim', ''),
            'activity_viewpoints':     DailyRecord.clean_viewpoints(p.get('activity_viewpoints', '')),
            'activity_reflection':     p.get('activity_reflection', ''),
            'observation_memo':        p.get('observation_memo', ''),
            'support_memo':            p.get('support_memo', ''),
            'reaction_memo':           p.get('reaction_memo', ''),
            'observation_text':        p.get('observation_text', ''),
            'support_text':            p.get('support_text', ''),
            'reaction_text':           p.get('reaction_text', ''),
            'parent_message_draft':    p.get('parent_message_draft', ''),
            'status':                  p.get('status', DailyRecord.STATUS_DRAFT),
        }

        record, created = DailyRecord.objects.get_or_create(
            beneficiary=beneficiary,
            date=date,
            defaults=defaults,
        )

        if not created:
            for k, v in defaults.items():
                setattr(record, k, v)
            record.save()

        # 活動タグ・支援タグの保存（自施設のタグIDのみ受け付ける）
        valid_activity_ids = ActivityTag.objects.filter(
            facility=facility, pk__in=p.getlist('activity_tags')
        ).values_list('pk', flat=True)
        record.activity_tags.set(valid_activity_ids)
        valid_support_ids = SupportContentTag.objects.filter(
            facility=facility, pk__in=p.getlist('support_tags')
        ).values_list('pk', flat=True)
        record.support_tags.set(valid_support_ids)

        # 写真を保存する
        _save_photos(request, record)

        messages.success(request, f'{date} の日誌を保存しました。')

        # 「保存してLINE送信」ボタンが押された場合
        if p.get('send_line') == '1' and record.status == DailyRecord.STATUS_CONFIRMED:
            _send_line_for_record(request, record)

        nxt = p.get('next', '')
        if nxt.startswith('/') and not nxt.startswith('//'):
            return redirect(nxt)
        return redirect(f'/records/{beneficiary_pk}/?selected={record.pk}')


# =============================================
# 日誌 更新
# =============================================
class DailyRecordUpdateView(LoginRequiredMixin, View):
    """詳細画面の編集モーダルから日誌を更新する"""

    def post(self, request, pk):
        facility = request.user.facility
        record   = get_object_or_404(DailyRecord, pk=pk, facility=facility)
        p = request.POST

        # 担当者IDの検証（自施設の職員のみ受け付ける）
        author_pk = p.get('author')
        if author_pk and facility.staff_accounts.filter(pk=author_pk).exists():
            record.author_id = author_pk
        elif not author_pk:
            pass  # 空の場合は変更しない
        record.entry_time              = p.get('entry_time') or None
        record.exit_time               = p.get('exit_time') or None
        record.health_condition        = p.get('health_condition', record.health_condition)
        record.health_note             = p.get('health_note', '')
        record.domain_health_life      = 'domain_health_life' in p
        record.domain_motor_sensory    = 'domain_motor_sensory' in p
        record.domain_cognition_behavior = 'domain_cognition_behavior' in p
        record.domain_language_comm    = 'domain_language_comm' in p
        record.domain_social           = 'domain_social' in p
        record.activity_name           = p.get('activity_name', '').strip()[:100]
        record.activity_aim            = p.get('activity_aim', '')
        record.activity_viewpoints     = DailyRecord.clean_viewpoints(p.get('activity_viewpoints', ''))
        record.activity_reflection     = p.get('activity_reflection', '')
        record.observation_memo        = p.get('observation_memo', '')
        record.support_memo            = p.get('support_memo', '')
        record.reaction_memo           = p.get('reaction_memo', '')
        record.observation_text        = p.get('observation_text', '')
        record.support_text            = p.get('support_text', '')
        record.reaction_text           = p.get('reaction_text', '')
        record.parent_message_draft    = p.get('parent_message_draft', '')
        record.status                  = p.get('status', record.status)
        record.save()

        # 自施設のタグIDのみ受け付ける
        valid_activity_ids = ActivityTag.objects.filter(
            facility=facility, pk__in=p.getlist('activity_tags')
        ).values_list('pk', flat=True)
        record.activity_tags.set(valid_activity_ids)
        valid_support_ids = SupportContentTag.objects.filter(
            facility=facility, pk__in=p.getlist('support_tags')
        ).values_list('pk', flat=True)
        record.support_tags.set(valid_support_ids)

        # 写真を追加保存する（既存写真はそのまま残す）
        _save_photos(request, record)

        messages.success(request, '日誌を更新しました。')
        return redirect(f'/records/{record.beneficiary_id}/?selected={record.pk}')


# =============================================
# 日誌写真 削除
# =============================================
class DailyRecordPhotoDeleteView(LoginRequiredMixin, View):
    """日誌に添付した写真を1枚削除する（POSTのみ）"""

    def post(self, request, photo_pk):
        facility = request.user.facility
        photo = get_object_or_404(DailyRecordPhoto, pk=photo_pk, facility=facility)
        record_pk = photo.daily_record_id
        beneficiary_pk = photo.daily_record.beneficiary_id
        # ファイルを削除してからレコードを削除
        photo.photo.delete(save=False)
        photo.delete()
        return redirect(f'/records/{beneficiary_pk}/?selected={record_pk}')


# =============================================
# AI文章整え（Claude API）
# =============================================
class AiPolishView(LoginRequiredMixin, View):
    """
    メモ書きテキストをClaude APIに送り、
    指定した記録種別（観察・支援・反応）の文章として整えて返す。
    フロントからfetchで呼び出し、結果をテキストエリアに流し込む。
    """

    PROMPTS = {
        'observation': '放課後等デイサービスの「活動内容・観察記録」として、職員のメモや入力されたタグを元に80〜120字程度の記録文章に整えてください。事実に基づき、客観的かつ具体的に書いてください。「です、ます調」にして下さい。文章のみ返してください。',
        'support':     '放課後等デイサービスの「支援内容の記録」として、職員のメモ入力されたタグを元に80〜120字程度の記録文章に整えてください。どのような支援を行ったか具体的に書いてください。「です、ます調」にして下さい。文章のみ返してください。',
        'reaction':    '放課後等デイサービスの「本人の反応・変化の記録」として、職員のメモ入力されたタグを元に60〜100字程度の記録文章に整えてください。本人の言動や表情など具体的な反応を書いてください。「です、ます調」にして下さい。文章のみ返してください。',
    }

    def post(self, request):
        memo      = request.POST.get('memo', '').strip()
        field_key = request.POST.get('field', 'observation')
        tags      = request.POST.get('tags', '').strip()

        if not memo:
            return JsonResponse({'error': 'メモが入力されていません。'}, status=400)

        prompt = self.PROMPTS.get(field_key, self.PROMPTS['observation'])

        # タグが選択されている場合はプロンプトに追記する
        user_content = f'{prompt}\n\n【メモ】\n{memo}'
        if tags:
            user_content += f'\n\n【選択されたタグ（活動・支援内容）】\n{tags}'

        try:
            client   = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_TEXT_MODEL,  # 文章整えは高速・低コストのモデルを使用
                max_tokens=300,
                messages=[{
                    'role': 'user',
                    'content': user_content,
                }],
            )
            polished = ''.join(b.text for b in response.content if b.type == 'text').strip()
            return JsonResponse({'result': polished})
        except Exception as e:  # noqa: BLE001
            logger.exception('AI文章整えでエラー')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)


# =============================================
# AI一括生成（メモ+タグ → 観察・支援・反応・保護者向けメッセージ）
# =============================================
class AiGenerateAllView(LoginRequiredMixin, View):
    """
    職員の1つのメモ書きと選択タグから、4種類の記録文章を一括生成する。
    - 観察・活動内容の記録（80〜120字）
    - 支援内容の記録（80〜120字）
    - 本人の反応・変化の記録（60〜100字）
    - 保護者向けメッセージ（100〜150字・LINE送信用）
    """

    SYSTEM_PROMPT = """あなたは放課後等デイサービスの記録専門AIアシスタントです。
職員のメモ書きと選択されたタグをもとに、4種類の記録文章を生成してください。です/ます調・「して下さいました」などの過剰な敬語は不要です。
必ず以下のJSON形式のみで返してください。余分なテキストや説明、コードフェンスは一切不要です。
文字列の中に改行を入れず、1つの文字列は1行で書いてください。

{
  "observation": "活動内容・観察記録（80〜120字・事実に基づき客観的に）",
  "support": "支援内容の記録（80〜120字・どのような支援をしたか具体的に）",
  "reaction": "本人の反応・変化の記録（60〜100字・言動や表情など具体的に）",
  "parent_message": "保護者向けメッセージ（100〜150字・温かみのある表現で今日の様子を伝える）"
}"""

    def post(self, request):
        memo = request.POST.get('memo', '').strip()
        tags = request.POST.get('tags', '').strip()

        if not memo:
            return JsonResponse({'error': 'メモを入力してから生成してください。'}, status=400)

        user_content = f'【職員のメモ】\n{memo}'
        if tags:
            user_content += f'\n\n【選択されたタグ（活動・支援内容）】\n{tags}'

        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_TEXT_MODEL,
                max_tokens=800,
                system=self.SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': user_content}],
            )
            raw = ''.join(b.text for b in response.content if b.type == 'text').strip()
            # JSON部分だけ抽出（念のため）
            start = raw.find('{')
            end   = raw.rfind('}') + 1
            if start < 0 or end <= start:
                raise json.JSONDecodeError('JSONが含まれていない', raw, 0)
            # 文字列中に改行がそのまま入ることがあるため strict=False で許容する
            data  = json.loads(raw[start:end], strict=False)
            if not isinstance(data, dict):
                raise json.JSONDecodeError('object expected', raw, 0)
            return JsonResponse({
                'observation':    data.get('observation', ''),
                'support':        data.get('support', ''),
                'reaction':       data.get('reaction', ''),
                'parent_message': data.get('parent_message', ''),
            })
        except json.JSONDecodeError:
            logger.warning('AI一括生成の返答がJSONでない: %r', raw[:200] if 'raw' in locals() else None)
            return JsonResponse({'error': 'AIの返答を解析できませんでした。もう一度お試しください。'}, status=500)
        except Exception as e:  # noqa: BLE001
            logger.exception('AI一括生成でエラー')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)


# =============================================
# AI めあて・考察の生成（活動名 → 観察の観点と考察の下書き）
# =============================================
class AiActivityPlanView(LoginRequiredMixin, View):
    """
    「クッキー作り」「かるた」など活動名を入力すると、
    紙の業務日誌の「めあて：」に相当する観察の観点と、考察の下書きを生成する。
    メモが入力済みならそれを踏まえた考察に、未入力なら活動から想定される観点で下書きする。
    """

    SYSTEM_PROMPT = """あなたは放課後等デイサービスの児童発達支援管理責任者を補助するAIです。
職員が入力した「活動」から、業務日誌に書く「めあて」と「考察」を作成してください。
対象は発達に特性のある小学生〜高校生です。安全・役割分担・感覚・言語・社会性など、
活動の性質に合った観点を選び、抽象的な言葉ではなく現場で観察できる行動で書いてください。

必ず以下のJSON形式のみで返してください。余分な説明やコードフェンスは不要です。
文字列の中に改行を入れず、1つの文字列は1行で書いてください。
{
  "aim": "活動全体のめあて（1文・30字以内。例：役割分担、気を付けて調理道具をつかおう）",
  "viewpoints": ["観察の観点1", "観察の観点2", "観察の観点3"],
  "reflection": "考察（120〜180字・です/ます調）"
}

viewpoints は3〜4項目、各20字以内で、職員が活動中に見るポイントを書いてください。
reflection は、【職員のメモ】がある場合はその事実に基づいて本人の様子と次回への示唆を書き、
メモがない場合は「（下書き）」で始め、観点に沿って確認すべき点を示す下書きにしてください。"""

    def post(self, request):
        activity = request.POST.get('activity', '').strip()
        memo     = request.POST.get('memo', '').strip()
        tags     = request.POST.get('tags', '').strip()
        # 既に観点が出ていて「はい／いいえ」を付けた場合は、その結果を踏まえて考察を作り直す
        viewpoints = DailyRecord.clean_viewpoints(request.POST.get('viewpoints', ''))
        answered   = [v for v in viewpoints if v['answer']]

        if not activity:
            return JsonResponse({'error': '活動名を入力してから生成してください。'}, status=400)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'ANTHROPIC_API_KEY が設定されていません。施設設定または .env を確認してください。'}, status=500)

        user_content = f'【活動】\n{activity}'
        if tags:
            user_content += f'\n\n【選択されたタグ（活動・支援内容）】\n{tags}'
        if memo:
            user_content += f'\n\n【職員のメモ】\n{memo}'
        if answered:
            lines = '\n'.join(f'・{v["text"]} → {DailyRecord.VIEWPOINT_ANSWERS[v["answer"]]}' for v in answered)
            user_content += ('\n\n【観点ごとの職員の確認結果（はい＝できていた／いいえ＝できていなかった）】\n' + lines +
                             '\n\nこの確認結果を事実として考察を書いてください。「（下書き）」は付けず、'
                             '「いいえ」の観点には次回に向けた具体的な手立てを1つ添えてください。'
                             'viewpoints は上の観点をそのまま同じ順番・同じ文言で返してください。')

        try:
            client   = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_PLAN_MODEL,
                max_tokens=1500,
                output_config={'effort': 'medium'},
                system=self.SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': user_content}],
            )
            # 思考ブロックが先頭に来ることがあるため、テキストブロックだけを取り出す
            raw = ''.join(b.text for b in response.content if b.type == 'text').strip()
            start = raw.find('{')
            end   = raw.rfind('}') + 1
            # 文字列中に改行がそのまま入ることがあるため strict=False で許容する
            data  = json.loads(raw[start:end], strict=False)
            if not isinstance(data, dict):
                raise json.JSONDecodeError('object expected', raw, 0)

            aim        = str(data.get('aim', '')).strip()
            new_points = [str(v).strip()[:100] for v in data.get('viewpoints', []) if str(v).strip()]
            if answered:
                # 職員が確認した観点はそのまま（回答つき）で返す
                result_points = viewpoints
            else:
                result_points = [{'text': v, 'answer': None} for v in new_points]
            aim_text   = aim
            if new_points:
                aim_text += ('\n' if aim_text else '') + '\n'.join(f'・{v}' for v in new_points)
            return JsonResponse({
                'aim':        aim,
                'viewpoints': result_points,
                'aim_text':   aim_text,
                'reflection': str(data.get('reflection', '')).strip(),
            })
        except json.JSONDecodeError:
            logger.warning('活動プラン生成の返答がJSONでない: %r', raw[:200] if 'raw' in locals() else None)
            return JsonResponse({'error': 'AIの返答を解析できませんでした。もう一度お試しください。'}, status=500)
        except anthropic.AuthenticationError:
            return JsonResponse({'error': 'Anthropic APIキーが無効です。設定を確認してください。'}, status=500)
        except anthropic.RateLimitError:
            return JsonResponse({'error': 'AIの利用上限に達しました。少し待ってからもう一度お試しください。'}, status=503)
        except anthropic.APIStatusError as e:
            return JsonResponse({'error': f'AIサービスでエラーが発生しました（{e.status_code}）。'}, status=502)
        except anthropic.APIConnectionError:
            return JsonResponse({'error': 'AIサービスに接続できませんでした。ネットワークを確認してください。'}, status=502)
        except Exception as e:  # noqa: BLE001 — 画面に理由を返し、詳細はログに残す
            logger.exception('活動プラン生成で予期しないエラー')
            return JsonResponse({'error': f'AIでの処理中にエラーが発生しました: {type(e).__name__}: {e}'}, status=500)


# =============================================
# かんたん3ステップ（メニューを隠し、その日にやることだけを順番に出す画面）
# =============================================
class SimpleHomeView(LoginRequiredMixin, View):
    """きょうの きろく：今日来る子の一覧と、記録の状態"""

    def get(self, request):
        facility = request.user.facility
        try:
            day = date.fromisoformat(request.GET.get('date', ''))
        except ValueError:
            day = date.today()
        visits = (ScheduledVisit.objects.filter(facility=facility, date=day)
                  .exclude(status='absent').select_related('beneficiary')
                  .order_by('beneficiary__last_name_kana'))
        records = {r.beneficiary_id: r for r in DailyRecord.objects.filter(facility=facility, date=day)}
        rows = []
        seen = set()
        for v in visits:
            rows.append({'beneficiary': v.beneficiary, 'record': records.get(v.beneficiary_id)})
            seen.add(v.beneficiary_id)
        # 予定に無いが記録がある子も並べる
        for b_id, r in records.items():
            if b_id not in seen:
                rows.append({'beneficiary': r.beneficiary, 'record': r})
        others = Beneficiary.objects.filter(facility=facility, status='active').exclude(pk__in=seen | set(records))
        done = sum(1 for r in rows if r['record'] and r['record'].status == DailyRecord.STATUS_CONFIRMED)
        return render(request, 'records/simple_home.html', {
            'day': day, 'today': date.today(), 'rows': rows, 'others': others,
            'done': done, 'total': len(rows),
            'prev_day': day - timedelta(days=1), 'next_day': day + timedelta(days=1),
        })


class SimpleRecordView(LoginRequiredMixin, View):
    """①メモを入れる ②下書きをつくる ③確認して確定 の3ステップ画面"""

    def get(self, request, beneficiary_pk):
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        try:
            day = date.fromisoformat(request.GET.get('date', ''))
        except ValueError:
            day = date.today()
        record = DailyRecord.objects.filter(beneficiary=beneficiary, date=day).first()
        editing = request.GET.get('edit') == '1'
        return render(request, 'records/simple_record.html', {
            'beneficiary': beneficiary, 'day': day, 'record': record,
            'confirmed': bool(record and record.status == DailyRecord.STATUS_CONFIRMED and not editing),
            'init': {
                'memo':        record.observation_memo if record else '',
                'activity':    record.activity_name if record else '',
                'observation': record.observation_text if record else '',
                'support':     record.support_text if record else '',
                'reaction':    record.reaction_text if record else '',
                'parent':      record.parent_message_draft if record else '',
                'viewpoints':  record.activity_viewpoints if record else [],
                'aim':         record.activity_aim if record else '',
                'reflection':  record.activity_reflection if record else '',
            },
            'next_url': reverse('records:simple_home') + f'?date={day.isoformat()}',
        })
