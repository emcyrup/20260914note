"""
紙の日誌の写真を AI（画像入力）で読み取り、日誌の項目に分ける
"""
import base64
import io
import json
import logging
import re
from datetime import date, datetime

import anthropic
from django.conf import settings
from django.utils import timezone

from ai_assist.text import JAPANESE_RULES, clean_ai_text, effort_kwargs
from beneficiaries.models import Beneficiary

from .models import DailyRecord, PaperScan

logger = logging.getLogger(__name__)

MAX_SIDE = 1600

PAPER_PROMPT = """あなたは放課後等デイサービスの記録を電子化する事務員です。
写真に写っている紙の日誌（手書きや印刷）を読み取り、項目に分けて JSON で返してください。

ルール
- 書かれていることだけを書き写す。読めない箇所や無い項目は空文字（または null）にし、推測で埋めない
- 手書きの誤字・省略は、意味が明らかなときだけ自然な表記に直す
- 日付は西暦の YYYY-MM-DD。年が書かれていなければ【今日】の年を使い、令和なら西暦に直す
- 時刻は HH:MM（24時間）
- 利用者の名前は写真の表記のまま beneficiary_name に入れる。【在籍している利用者】に同じ人がいれば、その正式な表記にする
- 観点（めあての細目・チェック項目）は viewpoints に、○／✓／はい なら "yes"、×／いいえ なら "no"、印が無ければ null
- 「観察・活動内容」「支援内容」「本人の反応」「考察」「保護者向け」に相当する欄が無い日誌は、本文を observation_text にまとめ、他は空でよい
- 読めなかった箇所や気になる点は unreadable に短く書く

""" + JAPANESE_RULES + """

必ず次の JSON だけを返してください（文字列内で改行しない）。
{"date": "YYYY-MM-DD", "beneficiary_name": "", "entry_time": "HH:MM", "exit_time": "HH:MM",
 "health_condition": "good|normal|special|", "health_note": "",
 "activity_name": "", "activity_aim": "", "viewpoints": [{"text": "", "answer": "yes|no|null"}],
 "activity_reflection": "", "observation_text": "", "support_text": "", "reaction_text": "",
 "parent_message": "", "other_notes": "", "unreadable": ""}"""

TEXT_KEYS = ('activity_name', 'activity_aim', 'activity_reflection', 'observation_text', 'support_text',
             'reaction_text', 'parent_message', 'other_notes', 'unreadable', 'health_note', 'beneficiary_name')


def image_payload(image_field, max_side=MAX_SIDE):
    """アップロード画像を縮小して JPEG の base64 にする（トークン節約・向きの補正）"""
    from PIL import Image, ImageOps
    image_field.open('rb')
    try:
        img = Image.open(image_field)
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
    finally:
        image_field.close()
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _valid_date(s):
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except (TypeError, ValueError):
        return ''


def _valid_time(s):
    m = re.match(r'^\s*(\d{1,2})[:：時](\d{2})', str(s or ''))
    if not m:
        return ''
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h < 24 and 0 <= mi < 60:
        return f'{h:02d}:{mi:02d}'
    return ''


def normalize(data, facility):
    """AI の返答を画面のフォームに入れられる形に整える"""
    out = {k: clean_ai_text(data.get(k, ''), keep_newlines=(k not in ('activity_name', 'beneficiary_name', 'activity_aim'))) for k in TEXT_KEYS}
    out['activity_name'] = out['activity_name'][:100]
    out['date'] = _valid_date(data.get('date'))
    out['entry_time'] = _valid_time(data.get('entry_time'))
    out['exit_time'] = _valid_time(data.get('exit_time'))
    hc = str(data.get('health_condition') or '').strip()
    out['health_condition'] = hc if hc in dict(DailyRecord.HEALTH_CHOICES) else ''
    out['viewpoints'] = DailyRecord.clean_viewpoints(json.dumps(data.get('viewpoints') or [], ensure_ascii=False))
    out['viewpoints'] = [{'text': clean_ai_text(v['text'], keep_newlines=False)[:100], 'answer': v['answer']} for v in out['viewpoints']]
    # 名前から利用者を探す
    from ai_assist.services import resolve_beneficiary
    beneficiary, candidates = (None, [])
    if out['beneficiary_name']:
        beneficiary, candidates = resolve_beneficiary(facility, out['beneficiary_name'])
    out['beneficiary_id'] = beneficiary.pk if beneficiary else None
    out['candidate_ids'] = [b.pk for b in candidates]
    return out


def extract_paper_scan(scan):
    """1枚の写真を読み取って PaperScan に保存する。戻り値は整えたデータ"""
    facility = scan.facility
    names = '、'.join(b.full_name for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE))
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    res = client.messages.create(
        model=settings.AI_PLAN_MODEL,
        max_tokens=4000,
        **effort_kwargs(settings.AI_PLAN_MODEL, 'medium'),
        system=PAPER_PROMPT,
        messages=[{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': image_payload(scan.image)}},
            {'type': 'text', 'text': f'【在籍している利用者】{names or "（未登録）"}\n【今日】{date.today().isoformat()}\n\nこの写真の日誌を読み取ってください。'},
        ]}],
    )
    raw = ''.join(b.text for b in res.content if b.type == 'text').strip()
    start, end = raw.find('{'), raw.rfind('}') + 1
    if start < 0 or end <= start:
        raise ValueError('AIの返答に読み取り結果（JSON）が含まれていません')
    data = json.loads(raw[start:end], strict=False)
    if not isinstance(data, dict):
        raise ValueError('AIの返答の形式が正しくありません')
    out = normalize(data, facility)
    scan.extracted = out
    if out['beneficiary_id'] and not scan.beneficiary_id:
        scan.beneficiary_id = out['beneficiary_id']
    scan.status = PaperScan.STATUS_EXTRACTED
    scan.error = ''
    scan.extracted_at = timezone.now()
    scan.save()
    return out
