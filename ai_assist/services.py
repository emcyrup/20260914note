"""
AI支援のサービス層：加算提案の生成・チャットの回答
"""
import json
import logging
import re
from datetime import date

import anthropic
from django.conf import settings
from django.db import transaction

from facilities.models import AddonMaster
from schedules.models import ScheduledVisit

from .models import AddonSuggestion
from .retrieval import format_context, search

logger = logging.getLogger(__name__)


def _client():
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _text_of(response):
    return ''.join(b.text for b in response.content if b.type == 'text').strip()


def _parse_json(raw):
    start, end = raw.find('{'), raw.rfind('}') + 1
    if start < 0 or end <= start:
        raise json.JSONDecodeError('JSONが含まれていない', raw, 0)
    data = json.loads(raw[start:end], strict=False)
    if not isinstance(data, dict):
        raise json.JSONDecodeError('object expected', raw, 0)
    return data


# =============================================
# 加算提案
# =============================================
ADDON_SYSTEM_PROMPT = """あなたは放課後等デイサービスの請求事務を支援するAIです。
その日の日誌（支援記録）と、事業所が算定できる個別加算の一覧、算定要件の資料を読み、
その日に算定できる可能性がある加算だけを提案してください。

ルール
- 記録に書かれている事実から要件を満たす根拠が読み取れる加算だけを挙げる。推測で足さない
- 一覧にない加算は挙げない。加算名は一覧の表記をそのまま使う
- 「既に適用済み」と書かれた加算は挙げない
- 資料に該当箇所があれば evidence に「資料名 p.ページ」を書く。無ければ空文字
- 該当が無ければ suggestions を空配列にする

必ず次のJSONだけを返してください（文字列内で改行しない）。
{"suggestions": [{"addon": "加算名", "reason": "根拠となる記録の内容（60字以内）", "evidence": "資料名 p.数字 または空"}]}"""


def build_record_summary(record):
    """日誌の内容をプロンプト用に要約"""
    visit = ScheduledVisit.objects.filter(beneficiary=record.beneficiary, date=record.date).first()
    lines = [
        f'日付: {record.date}',
        f'利用者: {record.beneficiary.full_name}（{record.beneficiary.disability_type or "障害種別未記入"}）',
        f'入室: {record.entry_time or "-"} 退室: {record.exit_time or "-"}',
        f'体調: {record.get_health_condition_display()} {record.health_note}'.strip(),
        f'活動タグ: {", ".join(t.name for t in record.activity_tags.all()) or "なし"}',
        f'支援内容タグ: {", ".join(t.name for t in record.support_tags.all()) or "なし"}',
    ]
    if visit:
        lines.append(f'送迎: 迎え{"あり" if visit.has_pickup else "なし"} / 送り{"あり" if visit.has_dropoff else "なし"}')
    if record.activity_name:
        lines.append(f'活動: {record.activity_name}')
    for label, text in (('観察・活動内容', record.observation_text), ('支援内容', record.support_text),
                        ('本人の反応', record.reaction_text), ('職員メモ', record.observation_memo),
                        ('考察', record.activity_reflection)):
        if text:
            lines.append(f'{label}: {text}')
    if record.activity_viewpoints:
        lines.append('観点: ' + ' / '.join(
            f'{v.get("text")}={ {"yes": "はい", "no": "いいえ"}.get(v.get("answer"), "未確認") }'
            for v in record.activity_viewpoints))
    return '\n'.join(lines)


def _applied_addon_names(record):
    from billing.models import BillingMatrixAddon
    return set(BillingMatrixAddon.objects.filter(
        entry__facility=record.facility, entry__beneficiary=record.beneficiary, entry__date=record.date,
        is_applied=True).values_list('addon__name', flat=True))


def generate_addon_suggestions(record):
    """日誌から加算を提案して AddonSuggestion に保存する。戻り値は作成した提案のリスト"""
    if not settings.ANTHROPIC_API_KEY:
        return []
    addons = list(AddonMaster.objects.filter(is_active=True, addon_type='individual').order_by('name'))
    if not addons:
        return []
    applied = _applied_addon_names(record)
    decided = {s.addon_id: s for s in record.addon_suggestions.exclude(status=AddonSuggestion.STATUS_PENDING)}

    summary = build_record_summary(record)
    addon_lines = '\n'.join(
        f'- {a.name}（{a.unit_count}単位）: {a.description or "要件未登録"}' + ('【既に適用済み】' if a.name in applied else '')
        for a in addons)
    query = summary[:400] + ' ' + ' '.join(a.name for a in addons)
    hits = search(record.facility, query, top_k=6)
    doc_context = format_context(hits) if hits else '（算定要件資料は登録されていません。加算一覧の要件概要だけで判断してください）'

    user_content = f'【日誌】\n{summary}\n\n【算定できる個別加算の一覧】\n{addon_lines}\n\n【算定要件の資料（抜粋）】\n{doc_context}'
    try:
        res = _client().messages.create(
            model=settings.AI_TEXT_MODEL, max_tokens=700, system=ADDON_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_content}],
        )
        data = _parse_json(_text_of(res))
    except Exception:  # noqa: BLE001
        logger.exception('加算提案の生成に失敗（日誌ID %s）', record.pk)
        return []

    by_name = {a.name: a for a in addons}
    created = []
    with transaction.atomic():
        record.addon_suggestions.filter(status=AddonSuggestion.STATUS_PENDING).delete()
        for item in data.get('suggestions', [])[:6]:
            addon = by_name.get(str(item.get('addon', '')).strip())
            if not addon or addon.name in applied or addon.pk in decided:
                continue
            created.append(AddonSuggestion.objects.create(
                facility=record.facility, daily_record=record, addon=addon,
                reason=str(item.get('reason', ''))[:300], evidence=str(item.get('evidence', ''))[:200],
            ))
    return created


def adopt_suggestion(suggestion, user):
    """提案を採用：請求マトリックスのセルに加算を入れ、未確認（◆）に戻す"""
    from billing.models import BillingMatrixAddon, BillingMatrixEntry
    from django.utils import timezone

    record = suggestion.daily_record
    visit = ScheduledVisit.objects.filter(beneficiary=record.beneficiary, date=record.date).exclude(status='scheduled').first()
    entry, created = BillingMatrixEntry.objects.get_or_create(
        facility=record.facility, beneficiary=record.beneficiary, date=record.date,
        defaults={'status': visit.status if visit else 'attended', 'is_finalized': False},
    )
    if not created and entry.is_finalized:
        entry.is_finalized = False  # 職員がポップアップで確認し直す
        entry.save(update_fields=['is_finalized'])
    addon_row, _ = BillingMatrixAddon.objects.get_or_create(entry=entry, addon=suggestion.addon, defaults={'is_applied': True})
    if not addon_row.is_applied:
        addon_row.is_applied = True
        addon_row.save(update_fields=['is_applied'])
    suggestion.status = AddonSuggestion.STATUS_ADOPTED
    suggestion.decided_at = timezone.now()
    suggestion.decided_by = user
    suggestion.save(update_fields=['status', 'decided_at', 'decided_by'])
    return entry


def dismiss_suggestion(suggestion, user):
    from django.utils import timezone
    suggestion.status = AddonSuggestion.STATUS_DISMISSED
    suggestion.decided_at = timezone.now()
    suggestion.decided_by = user
    suggestion.save(update_fields=['status', 'decided_at', 'decided_by'])


def reopen_suggestion(suggestion):
    """採用の取り消し：請求マトリックスの加算も外す"""
    from billing.models import BillingMatrixAddon
    if suggestion.status == AddonSuggestion.STATUS_ADOPTED:
        record = suggestion.daily_record
        BillingMatrixAddon.objects.filter(
            entry__facility=record.facility, entry__beneficiary=record.beneficiary, entry__date=record.date,
            addon=suggestion.addon).delete()
    suggestion.status = AddonSuggestion.STATUS_PENDING
    suggestion.decided_at = None
    suggestion.decided_by = None
    suggestion.save(update_fields=['status', 'decided_at', 'decided_by'])


# =============================================
# チャットボット
# =============================================
CHAT_SYSTEM_PROMPT = """あなたは放課後等デイサービスの職員を支援する業務アシスタントです。日本語で、簡潔に、です/ます調で答えます。

守ること
- 「参考情報」に書かれている事実だけを根拠に答える。書かれていないことは「記録にありません」と言い、推測で数字や様子を作らない
- 加算・算定要件の質問には、資料の抜粋があればそれを根拠に答え、末尾に「※参考情報です。正確な算定要件は厚生労働省の通知・自治体の解釈をご確認ください」と添える
- 利用者の個人情報は質問に必要な範囲だけ触れる
- 長くなるときは箇条書きにする（5項目以内）"""

NAME_MIN_LEN = 2


def resolve_beneficiary(facility, message, beneficiary_id=None):
    """メッセージに含まれる利用者名を探す。戻り値 (beneficiary or None, candidates list)"""
    from beneficiaries.models import Beneficiary
    qs = Beneficiary.objects.filter(facility=facility, status='active')
    if beneficiary_id:
        b = qs.filter(pk=beneficiary_id).first()
        return b, []
    text = re.sub(r'\s+', '', message)
    matches = []
    for b in qs:
        keys = {b.full_name.replace(' ', ''), b.last_name, b.first_name,
                b.full_name_kana.replace(' ', ''), b.first_name_kana, b.last_name_kana}
        keys = {k for k in keys if k and len(k) >= NAME_MIN_LEN}
        if any(k in text for k in keys):
            matches.append(b)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def build_beneficiary_context(beneficiary, today=None):
    """利用者に関する参考情報（今月の出席、直近の日誌、支援計画の目標）"""
    from records.models import DailyRecord
    today = today or date.today()
    visits = ScheduledVisit.objects.filter(beneficiary=beneficiary, date__year=today.year, date__month=today.month)
    attended = visits.filter(status='attended', date__lte=today).count()
    absent = visits.filter(status='absent', date__lte=today).count()
    scheduled = visits.filter(date__gt=today).count()
    lines = [
        f'利用者: {beneficiary.full_name}（{beneficiary.date_of_birth} 生、{beneficiary.disability_type or "障害種別未記入"}）',
        f'利用予定曜日: {beneficiary.scheduled_weekdays_display}',
        f'今月（{today.month}月）の出席: {attended}日、欠席: {absent}日、これからの予定: {scheduled}日',
    ]
    cert = beneficiary.recipient_certificates.order_by('-valid_until').first()
    if cert:
        lines.append(f'受給者証: 有効期限 {cert.valid_until}、支給量 {cert.granted_days}日/月、負担上限 {cert.monthly_cap}円')
    plan = beneficiary.support_plans.exclude(status='closed').order_by('-created_at').first()
    if plan:
        goals = '；'.join(f'{g.get_goal_type_display()}「{g.content}」' for g in plan.goals.all()[:4])
        lines.append(f'個別支援計画: {plan.title}（ステップ{plan.current_step} {plan.get_current_step_display()}）{goals}')
    records = DailyRecord.objects.filter(beneficiary=beneficiary).order_by('-date')[:5]
    if records:
        lines.append('直近の日誌:')
        for r in records:
            parts = [f'  - {r.date}']
            if r.activity_name:
                parts.append(f'活動「{r.activity_name}」')
            if r.observation_text:
                parts.append(r.observation_text[:120])
            if r.reaction_text:
                parts.append('反応: ' + r.reaction_text[:80])
            if r.activity_viewpoints:
                parts.append('観点: ' + ' / '.join(
                    f'{v.get("text")}={ {"yes": "はい", "no": "いいえ"}.get(v.get("answer"), "未確認") }'
                    for v in r.activity_viewpoints[:4]))
            lines.append(' '.join(parts))
    else:
        lines.append('直近の日誌: なし')
    return '\n'.join(lines)


def build_addon_context(facility, message):
    addons = AddonMaster.objects.filter(is_active=True).order_by('addon_type', 'name')
    lines = ['加算マスタ（名称・単位・要件の概要）:'] + [
        f'- {a.name}（{a.get_addon_type_display()}、{a.unit_count}単位）: {a.description or "-"}' for a in addons]
    hits = search(facility, message, top_k=5)
    if hits:
        lines.append('\n算定要件資料の抜粋:\n' + format_context(hits, max_chars=2500))
    return '\n'.join(lines)


def chat_reply(facility, user, message, history=None, beneficiary=None):
    """参考情報を組み立てて Claude に問い合わせる"""
    today = date.today()
    context = [f'今日: {today}（{"月火水木金土日"[today.weekday()]}）', f'事業所: {facility.name}', f'質問者: {user}（{user.get_role_display()}）']
    if beneficiary:
        context.append(build_beneficiary_context(beneficiary, today))
    context.append(build_addon_context(facility, message))
    messages = []
    for h in (history or [])[-10:]:
        if h.get('role') in ('user', 'assistant') and str(h.get('content', '')).strip():
            messages.append({'role': h['role'], 'content': str(h['content'])[:2000]})
    messages.append({'role': 'user', 'content': f'【参考情報】\n' + '\n'.join(context) + f'\n\n【質問】\n{message}'})
    res = _client().messages.create(
        model=settings.AI_TEXT_MODEL, max_tokens=900, system=CHAT_SYSTEM_PROMPT, messages=messages,
    )
    return _text_of(res)
