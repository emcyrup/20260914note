"""
個別支援計画のアセスメントに、台帳と療育記録の内容をそのまま入れる（Facility.plan_no_ai。AI は使わず、文も作らない）。

- 心身の状況：最新のアセスメント・資料（種類「アセスメント」。無ければ一番新しいもの）、最新の 5 領域アセスメント、
               療育記録の留意点、「療育記録に反映する」にした書類の要約
- 希望する生活：最新の 5 領域アセスメントの本人・家族の希望
- その他の情報：期間の療育記録（日付・やったこと・記録・様子）
画面では、いまの欄の後ろに足し、職員がそのまま直す。
"""
from beneficiaries.models import BeneficiaryAssessment, BeneficiaryKnowledge, DevelopmentAssessment

RECORDS_MAX = 120     # 期間の療育記録は新しい順にこの件数まで


def _block(title, body):
    body = (body or '').strip()
    return f'【{title}】\n{body}' if body else ''


def _join(*parts):
    return '\n\n'.join(p for p in parts if p)


def _ymd(d):
    return f'{d.year}/{d.month}/{d.day}'


def latest_assessment(beneficiary):
    qs = BeneficiaryAssessment.objects.filter(beneficiary=beneficiary).exclude(content='')
    return (qs.filter(kind=BeneficiaryAssessment.KIND_ASSESSMENT).order_by('-date', '-pk').first()
            or qs.order_by('-date', '-pk').first())


def dev_text(sheet):
    lines = []
    for r in sheet.rows():
        if r['rating'] or r['now']:
            head = r['label'] + (f'（{r["rating"]} {r["rating_label"]}）' if r['rating'] else '')
            lines.append(f'・{head}' + (f'：{r["now"]}' if r['now'] else ''))
    if sheet.strengths:
        lines.append(f'・強み・好きなこと：{sheet.strengths}')
    if sheet.concerns:
        lines.append(f'・気になること・課題：{sheet.concerns}')
    return '\n'.join(lines)


def record_line(rec):
    parts = [f'・{rec.date.month}/{rec.date.day}' + (f' {rec.time_label}' if rec.time_label else '')]
    if rec.activity_text:
        parts.append(rec.activity_text)
    if rec.body.strip():
        parts.append(f'記録：{rec.body.strip()}')
    if rec.situation.strip():
        parts.append(f'様子：{rec.situation.strip()}')
    return '　'.join(parts)


def assessment_material(beneficiary, start, end):
    """戻り値 {'condition', 'wishes', 'notes', 'records'（使った療育記録の件数）, 'sources'（入れたもの の一覧）}"""
    from therapy.models import TherapyProfile, TherapyRecord
    sources = []

    a = latest_assessment(beneficiary)
    asm = ''
    if a:
        asm = _block(f'アセスメント・資料 {_ymd(a.date)}「{a.title}」', a.content)
        sources.append(f'アセスメント・資料（{_ymd(a.date)}）')
    sheet = DevelopmentAssessment.objects.filter(beneficiary=beneficiary).order_by('-date', '-pk').first()
    dev, wishes = '', ''
    if sheet:
        dev = _block(f'5領域アセスメント {_ymd(sheet.date)}', dev_text(sheet))
        wishes = _join(_block('本人の希望', sheet.wishes_child), _block('家族の希望', sheet.wishes_family))
        sources.append(f'5領域アセスメント（{_ymd(sheet.date)}）')
    profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
    cautions = _block('療育記録の留意点', profile.cautions) if profile else ''
    if cautions:
        sources.append('療育記録の留意点')
    known = []
    for k in (BeneficiaryKnowledge.objects.filter(beneficiary=beneficiary, status=BeneficiaryKnowledge.STATUS_SAVED,
                                                  use_in_ai=True).exclude(summary='').order_by('-doc_date', '-pk')[:5]):
        label = k.title or k.kind or '書類'
        known.append(f'・{label}' + (f'（{_ymd(k.doc_date)}）' if k.doc_date else '') + f'：{k.summary.strip()}')
    docs = _block('書類から分かっていること', '\n'.join(known))
    if docs:
        sources.append(f'書類から分かっていること {len(known)} 件')

    records = list(TherapyRecord.objects.filter(beneficiary=beneficiary, facility_id=beneficiary.facility_id,
                                                date__range=(start, end)).order_by('-date', '-time', '-pk')[:RECORDS_MAX])
    records.reverse()      # 読みやすいよう日付の古い順に
    notes = ''
    if records:
        notes = _block(f'療育記録 {_ymd(start)}〜{_ymd(end)}（{len(records)} 件）', '\n'.join(record_line(r) for r in records))
        sources.append(f'療育記録 {len(records)} 件')
    return {'condition': _join(asm, dev, cautions, docs), 'wishes': wishes, 'notes': notes,
            'records': len(records), 'sources': sources}
