import json
import logging
import os
from datetime import date, datetime, timedelta
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.views.generic import TemplateView
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.db import models
from django.http import JsonResponse
from django.urls import reverse
from django.conf import settings
import anthropic

logger = logging.getLogger(__name__)

from beneficiaries.models import Beneficiary
from esignatures.models import EsignatureRecord
from facilities.models import Facility, SupportContentTag
from schedules.models import ScheduledVisit
from .models import DailyRecord, ActivityTag, DailyRecordPhoto, PaperScan, RecordTemplate, StaffMemo
from facilities.context_processors import get_terms
from ai_assist.text import JAPANESE_RULES, clean_ai_dict, clean_ai_text, effort_kwargs
from .paper import extract_paper_scan
from config.utils import safe_next, to_int
from config.concurrency import check_conflict, saved_at_label
from .severe import clean_severe_care, severe_care_fields


def to_time(value):
    """'15:30' → time。形式が違えば None"""
    from datetime import time as _time
    try:
        h, m = (value or '').strip().split(':')[:2]
        return _time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _special_support_fields(request, facility):
    """専門的支援の担当者・開始／終了時刻（終了が空なら開始の30分後）"""
    p = request.POST
    staff_id = None
    staff_pk = p.get('special_support_staff')
    if staff_pk and facility.staff_accounts.filter(pk=staff_pk).exists():
        staff_id = int(staff_pk)
    start = to_time(p.get('special_support_start'))
    end = to_time(p.get('special_support_end'))
    if start and not end:
        end = (datetime.combine(date.today(), start) + timedelta(minutes=30)).time()
    return {'special_support_staff_id': staff_id, 'special_support_start': start, 'special_support_end': end}


def _record_kind(request, beneficiary, current=None):
    kind = request.POST.get('record_kind')
    if kind in (DailyRecord.KIND_STANDARD, DailyRecord.KIND_SEVERE):
        return kind
    if current:
        return current
    return DailyRecord.KIND_SEVERE if beneficiary.is_severe else DailyRecord.KIND_STANDARD


def _sync_addons(request, record):
    """日誌の「加算の入力」を請求セルへ反映する（請求機能を使う事業所のみ）"""
    if not record.facility.use_billing or 'addons_present' not in request.POST:
        return
    from billing.services import sync_record_addons
    sync_record_addons(record, request.POST.getlist('addon_ids'))


def to_date(value):
    """'2026-09-14' → date。形式が違えば None"""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


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
        conflict = check_conflict(request, memo)
        if conflict:
            messages.error(request, conflict)
            return redirect('records:dashboard')
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
        if to_int(selected_pk) is not None:
            selected_record = records.filter(pk=to_int(selected_pk)).first()
        elif records.exists():
            selected_record = records.first()  # デフォルトは最新

        # 新規入力モーダル用タグ一覧
        activity_tags = ActivityTag.objects.filter(facility=facility, is_active=True)
        support_tags  = SupportContentTag.objects.filter(facility=facility, is_active=True)

        # 日誌テンプレート（他の利用者の日誌から作ったもの）
        ctx['record_templates'] = RecordTemplate.objects.filter(facility=facility).order_by('-use_count', '-updated_at')

        # 支援計画への導線（進行中の計画があればそのステップへ）
        current_plan = beneficiary.support_plans.exclude(status='closed').order_by('-created_at').first()
        ctx['current_plan'] = current_plan
        ctx['plan_step_urls'] = {}
        if current_plan:
            from django.urls import reverse as _rev
            ctx['plan_step_urls'] = {
                n: _rev('support_plans:step', args=[current_plan.pk, n]) if current_plan.can_open_step(n) else None
                for n in (1, 5)
            }

        # 加算の入力（請求機能を使う事業所だけ）。選択中の日誌には、その日の請求セルの加算を初期値にする
        addon_rows = []
        selected_addon_ids = set()
        selected_addons = []
        if facility.use_billing:
            from billing.services import applied_addon_ids, journal_addon_rows
            from facilities.models import facility_addon_rows
            addon_rows = journal_addon_rows(facility)
            if selected_record:
                selected_addon_ids = applied_addon_ids(facility, beneficiary, selected_record.date)
                selected_addons = [r for r in facility_addon_rows(facility, addon_type='individual') if r.pk in selected_addon_ids]

        ctx.update({
            'beneficiary':     beneficiary,
            'records':         records,
            'selected_record': selected_record,
            'activity_tags':   activity_tags,
            'support_tags':    support_tags,
            'status_choices':  DailyRecord.STATUS_CHOICES,
            'health_choices':  DailyRecord.HEALTH_CHOICES,
            'kind_choices':    DailyRecord.KIND_CHOICES,
            'default_kind':    DailyRecord.KIND_SEVERE if beneficiary.is_severe else DailyRecord.KIND_STANDARD,
            'severe_fields':   severe_care_fields(),
            'severe_fields_edit': severe_care_fields(selected_record.severe_care if selected_record else None),
            'addon_rows':      addon_rows,
            'selected_addon_ids': selected_addon_ids,
            'selected_addon_ids_json': json.dumps([str(i) for i in selected_addon_ids]),
            'selected_addons': selected_addons,
            'collab_addon_ids_json':  json.dumps([str(r.pk) for r in addon_rows if r.is_collaboration]),
            'special_addon_ids_json': json.dumps([str(r.pk) for r in addon_rows if r.is_special_support]),
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
                facility=facility,
            )
            ctx['photos'] = ctx['selected_record'].photos.all()
            ctx['photos_count'] = ctx['photos'].count()
            ctx['can_add_photo'] = ctx['photos_count'] < MAX_PHOTOS_PER_RECORD
            ctx['addon_suggestions'] = ctx['selected_record'].addon_suggestions.select_related('addon').order_by('status', '-created_at')
            ctx['ai_enabled'] = bool(settings.ANTHROPIC_API_KEY)
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


def _suggest_addons_after_save(record):
    if not record.facility.use_billing:
        return
    """日誌の保存後に AI 加算提案を作る（失敗しても保存は成功扱い）"""
    if not (settings.AI_ADDON_SUGGESTIONS and settings.ANTHROPIC_API_KEY):
        return
    try:
        from ai_assist.services import generate_addon_suggestions
        generate_addon_suggestions(record)
    except Exception:  # noqa: BLE001
        logger.exception('保存後の加算提案でエラー（日誌ID %s）', record.pk)


# =============================================
# 日誌 新規作成
# =============================================
class DailyRecordCreateView(LoginRequiredMixin, View):
    """モーダルフォームからPOSTで日誌を新規作成する"""

    def post(self, request, beneficiary_pk):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)

        date = to_date(request.POST.get('date'))
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
            'record_kind':             _record_kind(request, beneficiary),
            'severe_care':             clean_severe_care(p),
            'collaboration_note':      p.get('collaboration_note', ''),
            **_special_support_fields(request, facility),
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

        # 同じ日の日誌がすでにある（他の職員が先に作った）ときは黙って上書きしない
        existing = DailyRecord.objects.filter(beneficiary=beneficiary, date=date).first()
        if existing is not None and 'version' in p and p.get('force_save') != '1':
            messages.error(request, f'{date} の日誌はすでにあります（{saved_at_label(existing)} 保存）。他の職員が先に作成した可能性があります。'
                                    '一覧から開いて編集してください。')
            return redirect(f'/records/{beneficiary_pk}/?selected={existing.pk}')
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
        _sync_addons(request, record)

        messages.success(request, f'{date} の日誌を保存しました。')
        _suggest_addons_after_save(record)

        # 「保存してLINE送信」ボタンが押された場合
        if p.get('send_line') == '1' and record.status == DailyRecord.STATUS_CONFIRMED:
            _send_line_for_record(request, record)

        return redirect(safe_next(request, p.get('next', ''), f'/records/{beneficiary_pk}/?selected={record.pk}'))


# =============================================
# 日誌 更新
# =============================================
class DailyRecordUpdateView(LoginRequiredMixin, View):
    """詳細画面の編集モーダルから日誌を更新する"""

    def post(self, request, pk):
        facility = request.user.facility
        record   = get_object_or_404(DailyRecord, pk=pk, facility=facility)
        p = request.POST
        conflict = check_conflict(request, record)
        if conflict:
            messages.error(request, conflict)
            return redirect(f'/records/{record.beneficiary_id}/?selected={record.pk}')

        # 担当者IDの検証（自施設の職員のみ受け付ける）
        author_pk = p.get('author')
        if author_pk and facility.staff_accounts.filter(pk=author_pk).exists():
            record.author_id = author_pk
        elif not author_pk:
            pass  # 空の場合は変更しない
        record.record_kind             = _record_kind(request, record.beneficiary, record.record_kind)
        if record.record_kind == DailyRecord.KIND_SEVERE:
            record.severe_care = clean_severe_care(p)
        record.collaboration_note      = p.get('collaboration_note', record.collaboration_note)
        for k, v in _special_support_fields(request, facility).items():
            setattr(record, k, v)
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
        _sync_addons(request, record)

        messages.success(request, '日誌を更新しました。')
        _suggest_addons_after_save(record)
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
        user_content = f'{prompt}\n\n{JAPANESE_RULES}\n\n【メモ】\n{memo}'
        if tags:
            user_content += f'\n\n【選択されたタグ（活動・支援内容）】\n{tags}'

        try:
            client   = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_TEXT_MODEL,
                max_tokens=1024,
                **effort_kwargs(settings.AI_TEXT_MODEL),
                messages=[{
                    'role': 'user',
                    'content': user_content,
                }],
            )
            polished = clean_ai_text(''.join(b.text for b in response.content if b.type == 'text'))
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

    ITEM_SPECS = {
        'observation':    ('observation',    '活動内容・観察記録（80〜120字・事実に基づき客観的に）'),
        'support':        ('support',        '支援内容の記録（80〜120字・どのような支援をしたか具体的に）'),
        'reaction':       ('reaction',       '本人の反応・変化の記録（60〜100字・言動や表情など具体的に）'),
        'parent_message': ('parent_message', '保護者向けメッセージ（100〜150字・温かみのある表現で今日の様子を伝える）'),
    }

    @classmethod
    def build_prompt(cls, keys):
        """施設で決めた項目を、優先順位の順に並べた JSON の形で指示する"""
        keys = [k for k in keys if k in cls.ITEM_SPECS] or list(cls.ITEM_SPECS)
        lines = ',\n'.join(f'  "{cls.ITEM_SPECS[k][0]}": "{cls.ITEM_SPECS[k][1]}"' for k in keys)
        order = '、'.join(f'{i + 1}. {Facility.JOURNAL_SECTION_LABELS[k]}' for i, k in enumerate(keys))
        return cls.SYSTEM_PROMPT.replace('{ITEMS}', lines).replace('{COUNT}', str(len(keys))).replace('{ORDER}', order)

    SYSTEM_PROMPT = """あなたは放課後等デイサービスの記録専門AIアシスタントです。
職員のメモ書きと選択されたタグをもとに、{COUNT}種類の記録文章を生成してください。「して下さいました」などの過剰な敬語は不要です。
項目の優先順位は {ORDER} の順です。優先順位の高い項目ほどメモの事実を漏らさず丁寧に書き、後の項目は前の項目と重複しない内容にしてください。

""" + JAPANESE_RULES + """

必ず以下のJSON形式のみで返してください。余分なテキストや説明、コードフェンスは一切不要です。
文字列の中に改行を入れず、1つの文字列は1行で書いてください。JSON の文字列に \\u のようなエスケープを使わず、日本語をそのまま書いてください。

{
{ITEMS}
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
                max_tokens=2048,
                **effort_kwargs(settings.AI_TEXT_MODEL),
                system=self.build_prompt(request.user.facility.journal_text_keys()),
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
            out = clean_ai_dict(data, ('observation', 'support', 'reaction', 'parent_message'))
            out['order'] = request.user.facility.journal_text_keys()
            return JsonResponse(out)
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

""" + JAPANESE_RULES + """

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

            aim        = clean_ai_text(data.get('aim', ''), keep_newlines=False)
            new_points = [clean_ai_text(v, keep_newlines=False)[:100] for v in data.get('viewpoints', []) if clean_ai_text(v)]
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
                'reflection': clean_ai_text(data.get('reflection', '')),
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


# =============================================
# 日誌テンプレート（ある利用者の日誌を他の利用者に流用する）
# =============================================


class TemplateListView(LoginRequiredMixin, View):
    def get(self, request):
        facility = request.user.facility
        templates = (RecordTemplate.objects.filter(facility=facility)
                     .select_related('source_beneficiary', 'created_by').prefetch_related('activity_tags', 'support_tags'))
        return render(request, 'records/templates.html', {
            'templates': templates,
            'domains': [('domain_health_life', '健康・生活'), ('domain_motor_sensory', '運動・感覚'),
                        ('domain_cognition_behavior', '認知・行動'), ('domain_language_comm', '言語・コミュニケーション'),
                        ('domain_social', '人間関係・社会性')],
        })


class TemplateCreateFromRecordView(LoginRequiredMixin, View):
    def post(self, request, record_pk):
        record = get_object_or_404(DailyRecord, pk=record_pk, facility=request.user.facility)
        name = request.POST.get('name', '').strip()
        t = RecordTemplate.from_record(record, name, request.user)
        messages.success(request, f'テンプレート「{t.name}」を保存しました。他の{get_terms(request.user)["beneficiary"]}の「日誌を追加」で「テンプレートから入力」に出ます。')
        return redirect(f'/records/{record.beneficiary_id}/?selected={record.pk}')


class TemplateApplyView(LoginRequiredMixin, View):
    """テンプレートを利用者に合わせた入力値（JSON）で返す"""

    def get(self, request, pk):
        t = get_object_or_404(RecordTemplate, pk=pk, facility=request.user.facility)
        b = get_object_or_404(Beneficiary, pk=request.GET.get('beneficiary'), facility=request.user.facility)
        data = t.apply_for(b)
        RecordTemplate.objects.filter(pk=t.pk).update(use_count=models.F('use_count') + 1)
        return JsonResponse(data)


class TemplateUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        t = get_object_or_404(RecordTemplate, pk=pk, facility=request.user.facility)
        conflict = check_conflict(request, t)
        if conflict:
            messages.error(request, conflict)
            return redirect('records:template_list')
        p = request.POST
        t.name = p.get('name', '').strip()[:100] or t.name
        for f in RecordTemplate.TEXT_FIELDS:
            if f in p:
                setattr(t, f, p.get(f, '')[:100] if f == 'activity_name' else p.get(f, ''))
        if 'activity_viewpoints' in p:
            t.activity_viewpoints = [{'text': ln.strip().lstrip('・-').strip()[:100], 'answer': None}
                                     for ln in p.get('activity_viewpoints', '').splitlines() if ln.strip()]
        t.domains = [k for k in RecordTemplate.DOMAIN_KEYS if k in p.getlist('domains')]
        t.save()
        t.activity_tags.set(ActivityTag.objects.filter(facility=t.facility, pk__in=p.getlist('activity_tags')))
        t.support_tags.set(SupportContentTag.objects.filter(facility=t.facility, pk__in=p.getlist('support_tags')))
        messages.success(request, f'テンプレート「{t.name}」を更新しました。')
        return redirect('records:template_list')


class TemplateDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        t = get_object_or_404(RecordTemplate, pk=pk, facility=request.user.facility)
        name = t.name
        t.delete()
        messages.success(request, f'テンプレート「{name}」を削除しました。')
        return redirect('records:template_list')



# =============================================
# 紙の日誌の取り込み（カメラ → AI 読み取り → 確認して保存）
# =============================================
MAX_PAPER_UPLOAD = 20


class PaperScanListView(LoginRequiredMixin, View):
    def get(self, request):
        facility = request.user.facility
        scans = (PaperScan.objects.filter(facility=facility).exclude(status=PaperScan.STATUS_IMPORTED)
                 .select_related('beneficiary', 'uploaded_by'))
        recent = (PaperScan.objects.filter(facility=facility, status=PaperScan.STATUS_IMPORTED)
                  .select_related('beneficiary', 'record')[:10])
        return render(request, 'records/paper_list.html', {
            'scans': scans, 'recent': recent,
            'beneficiaries': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
        })


class PaperScanUploadView(LoginRequiredMixin, View):
    def post(self, request):
        facility = request.user.facility
        files = request.FILES.getlist('images')[:MAX_PAPER_UPLOAD]
        if not files:
            messages.error(request, '写真を選んでください。')
            return redirect('records:paper_list')
        beneficiary = None
        if request.POST.get('beneficiary'):
            beneficiary = Beneficiary.objects.filter(facility=facility, pk=to_int(request.POST['beneficiary'], -1)).first()
        created = 0
        for f in files:
            if not (f.content_type or '').startswith('image/'):
                continue
            PaperScan.objects.create(facility=facility, uploaded_by=request.user, beneficiary=beneficiary, image=f)
            created += 1
        if created:
            messages.success(request, f'{created} 枚を取り込みました。「AIで読み取る」を押すと項目に分かれます。')
        else:
            messages.error(request, '画像ファイル（JPEG・PNG・HEIC など）を選んでください。')
        return redirect('records:paper_list')


class PaperScanExtractView(LoginRequiredMixin, View):
    """1枚を AI で読み取る（画面から fetch で順番に呼ぶ）"""

    def post(self, request, pk):
        scan = get_object_or_404(PaperScan, pk=pk, facility=request.user.facility)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'ANTHROPIC_API_KEY が設定されていません。'}, status=500)
        try:
            data = extract_paper_scan(scan)
        except Exception as e:  # noqa: BLE001
            logger.exception('紙の日誌の読み取りに失敗（scan %s）', scan.pk)
            scan.error = f'{type(e).__name__}: {e}'[:500]
            scan.save(update_fields=['error'])
            return JsonResponse({'error': f'読み取りに失敗しました: {type(e).__name__}: {e}'}, status=500)
        return JsonResponse({
            'ok': True, 'status': scan.get_status_display(), 'date': data.get('date', ''),
            'beneficiary_id': scan.beneficiary_id,
            'beneficiary_name': scan.beneficiary.full_name if scan.beneficiary else data.get('beneficiary_name', ''),
            'activity_name': data.get('activity_name', ''), 'unreadable': data.get('unreadable', ''),
            'review_url': reverse('records:paper_review', args=[scan.pk]),
        })


class PaperScanReviewView(LoginRequiredMixin, View):
    """読み取り結果を確認・修正して日誌として保存する"""

    def _render(self, request, scan, existing=None):
        facility = request.user.facility
        d = scan.extracted or {}
        return render(request, 'records/paper_review.html', {
            'scan': scan, 'd': d, 'existing': existing,
            'beneficiaries': Beneficiary.objects.filter(facility=facility).order_by('status', 'last_name_kana', 'first_name_kana'),
            'candidate_ids': d.get('candidate_ids') or [],
            'staff': facility.staff_accounts.filter(is_active=True),
            'health_choices': DailyRecord.HEALTH_CHOICES,
            'viewpoints_json': json.dumps(d.get('viewpoints') or [], ensure_ascii=False),
            'ai_enabled': bool(settings.ANTHROPIC_API_KEY),
        })

    def get(self, request, pk):
        scan = get_object_or_404(PaperScan, pk=pk, facility=request.user.facility)
        return self._render(request, scan)

    def post(self, request, pk):
        facility = request.user.facility
        scan = get_object_or_404(PaperScan, pk=pk, facility=facility)
        p = request.POST
        beneficiary = Beneficiary.objects.filter(facility=facility, pk=to_int(p.get('beneficiary'), -1)).first()
        try:
            rec_date = datetime.strptime(p.get('date', ''), '%Y-%m-%d').date()
        except ValueError:
            rec_date = None
        if not beneficiary or not rec_date:
            messages.error(request, f'{get_terms(request.user)["beneficiary"]}と日付を確認してください。')
            scan.beneficiary = beneficiary
            return self._render(request, scan)

        existing = DailyRecord.objects.filter(beneficiary=beneficiary, date=rec_date).first()
        if existing and p.get('overwrite') != '1':
            messages.warning(request, f'{rec_date} の日誌はすでにあります。上書きする場合は「既存の日誌を上書きする」にチェックを入れて保存してください。')
            scan.beneficiary = beneficiary
            return self._render(request, scan, existing=existing)

        def t(name):
            return p.get(name, '') or None

        fields = {
            'facility': facility, 'author': request.user,
            'entry_time': t('entry_time'), 'exit_time': t('exit_time'),
            'health_condition': p.get('health_condition') or DailyRecord.HEALTH_GOOD,
            'health_note': p.get('health_note', ''),
            'activity_name': p.get('activity_name', '')[:100], 'activity_aim': p.get('activity_aim', ''),
            'activity_viewpoints': DailyRecord.clean_viewpoints(p.get('activity_viewpoints', '')),
            'activity_reflection': p.get('activity_reflection', ''),
            'observation_memo': p.get('observation_memo', ''),
            'observation_text': p.get('observation_text', ''), 'support_text': p.get('support_text', ''),
            'reaction_text': p.get('reaction_text', ''), 'parent_message_draft': p.get('parent_message_draft', ''),
            'status': p.get('status', DailyRecord.STATUS_CONFIRMED),
        }
        author_pk = p.get('author')
        if author_pk and facility.staff_accounts.filter(pk=author_pk).exists():
            fields['author_id'] = author_pk
            fields.pop('author')
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.save()
            record = existing
        else:
            record = DailyRecord.objects.create(beneficiary=beneficiary, date=rec_date, **fields)

        # 元の写真を日誌に添付する（別ファイルとしてコピー）
        if record.photos.count() < MAX_PHOTOS_PER_RECORD:
            from django.core.files.base import ContentFile
            scan.image.open('rb')
            try:
                content = ContentFile(scan.image.read(), name=os.path.basename(scan.image.name))
            finally:
                scan.image.close()
            DailyRecordPhoto.objects.create(facility=facility, daily_record=record, photo=content, order=record.photos.count())

        scan.beneficiary = beneficiary
        scan.record = record
        scan.status = PaperScan.STATUS_IMPORTED
        scan.save()
        messages.success(request, f'{beneficiary.full_name} の {rec_date} の日誌として保存しました。')
        return redirect(f'/records/{beneficiary.pk}/?selected={record.pk}')


class PaperScanDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        scan = get_object_or_404(PaperScan, pk=pk, facility=request.user.facility)
        if scan.status != PaperScan.STATUS_IMPORTED:
            scan.image.delete(save=False)
        scan.delete()
        messages.success(request, '取り込んだ写真を削除しました。')
        return redirect('records:paper_list')
