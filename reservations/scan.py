"""
紙の「月予約利用希望」の写真を AI（画像入力）で読み取り、利用希望（wishes）の形にする。

読み取った内容はそのまま保存せず、職員が転記の画面で写真と見比べてから保存する。
"""
import datetime
import json
import logging

import anthropic
from django.conf import settings
from django.utils import timezone

from ai_assist.text import clean_ai_text, effort_kwargs
from beneficiaries.models import Beneficiary

from . import monthly, services
from .models import RequestScan

logger = logging.getLogger(__name__)

MAX_SIDE = 2400        # 表の細かいマスを読むので、紙の日誌より大きめに残す

SYSTEM_PROMPT = """あなたは放課後等デイサービスの事務員です。
保護者が書いた紙の「月予約利用希望」の写真から、どの日のどの時間に○が付いているかを読み取ります。

用紙のつくり
- 上に「○月予約利用希望（希望利用回数 □ 回）」と「氏名（ ）」がある
- 表は1行が1日。左から「日付」「曜日」「終日」、その右に時刻の列（9:00、10:00 … のように1時間ごと）が並ぶ
- 灰色の網掛けのマスや「お休み」の行は、もともと利用できない枠。印刷の網掛けを○と取り違えない
- 用紙の形が少し違っても（手書きの表、別の様式）、日付の行と時刻の列の考え方は同じ

読み取りのルール
- ○・◯・✓・レ・丸囲み・塗りつぶしなど、保護者が付けた印を「希望あり」とする
- ×、二重線や斜線で消した印、「不可」と書かれたマスは「希望なし」
- 「終日」の列に印があれば all_day を true にする（その行の時刻は空でよい）
- 行の全部の時刻に矢印や線を引いて「全部」を示しているときも all_day を true にする
- 印のない日は days に入れない
- 日付は印刷された日付の数字（1〜31）で答える。行を数え間違えないよう、曜日の並びと【この月の暦】を照らし合わせる
- 希望利用回数が読めなければ -1、氏名が読めなければ空文字
- 欄外の書き込み（「午前がよい」「送迎希望」など）は note に書き写す
- 読めない・迷った箇所は unreadable に短く書く（例：「12日の行は印がかすれている」）
- 推測で印を足さない"""

SCHEMA = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string'},
        'year': {'type': 'integer'},
        'month': {'type': 'integer'},
        'desired_count': {'type': 'integer'},
        'days': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'day': {'type': 'integer'},
                    'all_day': {'type': 'boolean'},
                    'hours': {'type': 'array', 'items': {'type': 'integer'}},
                },
                'required': ['day', 'all_day', 'hours'],
                'additionalProperties': False,
            },
        },
        'note': {'type': 'string'},
        'unreadable': {'type': 'string'},
    },
    'required': ['name', 'year', 'month', 'desired_count', 'days', 'note', 'unreadable'],
    'additionalProperties': False,
}


def image_payload(image_field):
    """写真を縮小して JPEG の base64 にする（向きの補正も）"""
    from records.paper import image_payload as paper_payload
    return paper_payload(image_field, max_side=MAX_SIDE)


def calendar_text(facility, year, month, setting):
    """AI に渡す【この月の暦】。日ごとの曜日と、枠のある時刻（お休みの日はお休み）"""
    first, last = monthly.month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    lines = []
    for day in monthly.month_days(year, month):
        hours = [] if services.is_closed(facility, day, setting, closed) else setting.slot_hours(day)
        what = '、'.join(f'{h}時' for h in hours) if hours else 'お休み'
        lines.append(f'{day.day}日({services.WEEK_JP[day.weekday()]}) {what}')
    return '\n'.join(lines)


def to_wishes(data, facility, year, month, setting):
    """
    AI の返答を利用希望の形 {"YYYY-MM-DD": "all" | [時, ...]} にする。
    月に無い日・お休みの日・枠の無い時刻は捨て、その内容を ignored に残す。
    """
    first, last = monthly.month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    wishes, ignored = {}, []
    for item in data.get('days') or []:
        try:
            day = datetime.date(year, month, int(item.get('day')))
        except (TypeError, ValueError):
            continue
        hours_ok = [] if services.is_closed(facility, day, setting, closed) else setting.slot_hours(day)
        label = f'{day.day}日'
        if not hours_ok:
            ignored.append(f'{label}（お休みの日）')
            continue
        if item.get('all_day'):
            wishes[day.isoformat()] = 'all'
            continue
        hours = sorted({int(h) for h in item.get('hours') or [] if str(h).lstrip('-').isdigit()})
        bad = [h for h in hours if h not in hours_ok]
        if bad:
            ignored.append(f'{label} {"・".join(f"{h}時" for h in bad)}（枠の無い時刻）')
        good = [h for h in hours if h in hours_ok]
        if good:
            wishes[day.isoformat()] = good
    return wishes, ignored


def normalize(data, facility, year, month, setting):
    wishes, ignored = to_wishes(data, facility, year, month, setting)
    try:
        desired = int(data.get('desired_count'))
    except (TypeError, ValueError):
        desired = -1
    read_month = data.get('month') if isinstance(data.get('month'), int) else 0
    out = {
        'name': clean_ai_text(data.get('name', ''), keep_newlines=False)[:50],
        'desired_count': desired if 0 <= desired <= 99 else None,
        'wishes': wishes,
        'ignored': ignored,
        'note': clean_ai_text(data.get('note', ''), keep_newlines=False)[:200],
        'unreadable': clean_ai_text(data.get('unreadable', ''), keep_newlines=False)[:300],
        'read_month': read_month if 1 <= read_month <= 12 else None,
    }
    out['month_mismatch'] = bool(out['read_month'] and out['read_month'] != month)
    out['slot_count'] = sum(len(setting.slot_hours(datetime.date.fromisoformat(d))) if w == 'all' else len(w)
                            for d, w in wishes.items())
    return out


def match_beneficiary(facility, name):
    """読み取った氏名から在籍中の利用者を1人に決める。決まらなければ (None, 候補)"""
    if not name:
        return None, []
    from ai_assist.services import resolve_beneficiary
    return resolve_beneficiary(facility, name)


def extract(scan):
    """写真1枚を読み取って RequestScan に保存する。戻り値は整えたデータ"""
    facility = scan.facility
    setting = services.get_setting(facility)
    names = '、'.join(b.full_name for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
    effort = effort_kwargs(settings.AI_PLAN_MODEL, 'medium')
    output_config = {**effort.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': SCHEMA}}
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    res = client.messages.create(
        model=settings.AI_PLAN_MODEL,
        max_tokens=8000,
        output_config=output_config,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': image_payload(scan.image)}},
            {'type': 'text', 'text': (
                f'この用紙は {scan.year}年{scan.month}月 の利用希望です。\n'
                f'【この月の暦】（日・曜日・枠のある時刻）\n{calendar_text(facility, scan.year, scan.month, setting)}\n\n'
                f'【在籍している利用者】{names or "（未登録）"}\n\n'
                '写真の用紙を読み取ってください。'
            )},
        ]}],
    )
    if res.stop_reason == 'refusal':
        raise ValueError('AIが読み取りを断りました。写真を撮り直してください')
    raw = ''.join(b.text for b in res.content if b.type == 'text').strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find('{'), raw.rfind('}') + 1
        if start < 0 or end <= start:
            raise ValueError('AIの返答に読み取り結果が含まれていません')
        data = json.loads(raw[start:end], strict=False)
    if not isinstance(data, dict):
        raise ValueError('AIの返答の形式が正しくありません')
    out = normalize(data, facility, scan.year, scan.month, setting)
    if scan.beneficiary_id is None:
        found, candidates = match_beneficiary(facility, out['name'])
        out['candidate_ids'] = [b.pk for b in candidates]
        if found is not None:
            scan.beneficiary = found
    scan.extracted = out
    scan.status = RequestScan.STATUS_EXTRACTED
    scan.error = ''
    scan.extracted_at = timezone.now()
    scan.save()
    return out


def as_request(scan):
    """確認画面の表に並べるための、保存していない利用希望（読み取り結果から）"""
    from .models import MonthlyRequest
    d = scan.extracted or {}
    return MonthlyRequest(facility=scan.facility, year=scan.year, month=scan.month,
                          desired_count=d.get('desired_count') or 0, wishes=d.get('wishes') or {},
                          note=d.get('note') or '')
