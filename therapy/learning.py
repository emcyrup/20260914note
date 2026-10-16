"""
療育記録の整文（音声入力→書き言葉）を、職員の直し方に寄せていく（2026-10-16 ゆあーず）。

- 音声入力を今日の記録へ直接入れる事業所（Facility.therapy_voice_direct）だけ
- 記録を保存するとき、整える前の音声の文字（spoken_text）と AI が整えた文（ai_body）を記録に一緒に残す
- 次に整えるとき、この事業所の直近の記録から「AI の文 → 職員が直した文」の違う文だけを例として AI に渡す
  （名前は {名前} に伏せる。ほかの事業所の記録は使わない）
- 整える前の文字と AI の文は KEEP_DAYS 日で消す（記録の本文は残る）。職員が記録ごとに消すこともできる
"""
import datetime
import difflib
import re

KEEP_DAYS = 90          # 整える前の文字・AI の文を残す日数
EXAMPLE_RECORDS = 30    # 例にする記録の数（新しい順）
EXAMPLE_PAIRS = 15      # AI に渡す例の数
SIDE_MAX = 160          # 例の片側の長さ
NAME = '{名前}'


def mask(text, beneficiary):
    """利用者の名前を {名前} に伏せる（1 字の名前は「さん・くん・ちゃん・君」が付いたときだけ）"""
    from records.models import RecordTemplate
    text = RecordTemplate.anonymize(text or '', beneficiary)
    for n in {beneficiary.first_name, beneficiary.last_name, beneficiary.first_name_kana, beneficiary.last_name_kana}:
        if n and len(n) == 1:
            text = re.sub(re.escape(n) + r'(?=(さん|くん|ちゃん|君))', NAME, text)
    return text


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=。)|\n', text or '') if s.strip()]


def _side(parts):
    return ' '.join(parts)[:SIDE_MAX]


def pairs_for(facility, limit=EXAMPLE_PAIRS):
    """
    [(AI の文, 職員が直した文)] 新しい記録から。直した文が '' のときは「その文を消した」。
    文の単位で比べ、変えたところだけを取る（職員が足した文は AI のせいではないので取らない）
    """
    from .models import TherapyRecord
    out, seen = [], set()
    records = (TherapyRecord.objects.filter(facility=facility).exclude(ai_body='').exclude(body='')
               .select_related('beneficiary').order_by('-date', '-pk')[:EXAMPLE_RECORDS])
    for rec in records:
        if rec.body.strip() == rec.ai_body.strip():
            continue
        a, b = sentences(rec.ai_body), sentences(rec.body)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op not in ('replace', 'delete'):
                continue
            pair = (mask(_side(a[i1:i2]), rec.beneficiary), mask(_side(b[j1:j2]), rec.beneficiary) if op == 'replace' else '')
            if pair[0] and pair not in seen:
                seen.add(pair)
                out.append(pair)
            if len(out) >= limit:
                return out
    return out


def examples_block(facility):
    """AI に渡す例の文。無ければ ''"""
    if not getattr(facility, 'therapy_voice_direct', False):
        return ''
    pairs = pairs_for(facility)
    if not pairs:
        return ''
    lines = ['【この事業所での直し方の例（以前に AI が整えた文 → 職員が直した文。{名前} は子どもの名前）】']
    for ai, fixed in pairs:
        lines.append(f'・AI：{ai}\n　→ 直し：{fixed or "（この文は消した）"}')
    return '\n'.join(lines)


def expire(facility, today=None):
    """KEEP_DAYS より前の記録から、整える前の文字と AI の文を消す（本文は残る）"""
    from .models import TherapyRecord
    cutoff = (today or datetime.date.today()) - datetime.timedelta(days=KEEP_DAYS)
    return (TherapyRecord.objects.filter(facility=facility, date__lt=cutoff)
            .exclude(spoken_text='', ai_body='').update(spoken_text='', ai_body=''))
