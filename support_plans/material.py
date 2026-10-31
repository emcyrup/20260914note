"""
個別支援計画の AI 下書き（アセスメント・原案・モニタリング）に渡す「材料」を 1 か所でまとめる。

- 記録：事業所が療育記録を使っていれば TherapyRecord、そうでなければ日誌（DailyRecord）。期間内を新しい順に件数を絞る
- 書類から分かっていること（BeneficiaryKnowledge。保存ずみで「AI で参考にする」のもの）：要約・支援で気をつけること・
  本人と家族の意向・計画の目標。相談支援のサービス等利用計画があれば、個別支援計画はその方針・目標に沿うように別枠で渡す
- アセスメント・資料（面談・聞き取りなど）、5 領域アセスメント、療育記録の留意点
取り込んだ書類に個人の連絡先などが入っていても、ここでは要約と意向・目標だけを使う。
"""
import datetime

from beneficiaries.models import BeneficiaryKnowledge, DevelopmentAssessment

RECORDS_MAX = 60
KNOWLEDGE_MAX = 6


def _ymd(d):
    return f'{d.year}/{d.month}/{d.day}' if d else ''


def uses_therapy(beneficiary):
    return bool(getattr(beneficiary.facility, 'use_therapy_record', False))


# ---------------------------------------------------------------- 記録
class Line:
    """1 件の記録（療育記録・日誌のどちらでも同じ形で扱う）"""

    def __init__(self, date, text, record=None, therapy=None):
        self.date = date
        self.text = text
        self.record = record        # DailyRecord（根拠のひもづけができる）
        self.therapy = therapy      # TherapyRecord

    def line(self, limit=220):
        return f'- {self.date} {self.text[:limit]}'


def therapy_lines(beneficiary, start, end, limit=RECORDS_MAX):
    from therapy.models import TherapyRecord
    rows = []
    qs = (TherapyRecord.objects.filter(beneficiary=beneficiary, facility_id=beneficiary.facility_id, date__range=(start, end))
          .order_by('-date', '-time', '-pk')[:limit])
    for r in qs:
        parts = []
        if r.activity_text:
            parts.append(r.activity_text)
        if r.body.strip():
            parts.append('記録：' + r.body.strip())
        if r.situation.strip():
            parts.append('様子：' + r.situation.strip())
        rows.append(Line(r.date, ' / '.join(parts), therapy=r))
    return rows


def journal_lines(beneficiary, start, end, limit=RECORDS_MAX):
    from .ai import record_line, records_for
    return [Line(r.date, record_line(r)[2:].split(' ', 1)[-1], record=r) for r in records_for(beneficiary, start, end, limit=limit)]


def record_lines(beneficiary, start, end, limit=RECORDS_MAX):
    """期間の記録。療育記録を使う事業所は療育記録、それ以外は日誌。戻り値は Line の一覧（新しい順）と種類の名前"""
    if uses_therapy(beneficiary):
        return therapy_lines(beneficiary, start, end, limit), '療育記録'
    return journal_lines(beneficiary, start, end, limit), '日誌'


# ---------------------------------------------------------------- 書類・アセスメント
def knowledge_items(beneficiary, limit=KNOWLEDGE_MAX):
    return list(BeneficiaryKnowledge.objects.filter(beneficiary=beneficiary, status=BeneficiaryKnowledge.STATUS_SAVED, use_in_ai=True)
                .order_by('-doc_date', '-created_at')[:limit])


def consultation_text(beneficiary, exclude_pk=None):
    """このシステムで作った相談支援の計画（終了以外の最新。内容のあるもの）：意向・方針・長期／短期目標・課題と支援目標"""
    try:
        from consultation.models import ConsultationPlan
    except ImportError:          # アプリを入れていない構成
        return ''
    qs = ConsultationPlan.objects.filter(beneficiary=beneficiary).exclude(status=ConsultationPlan.STATUS_CLOSED)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    cp = qs.order_by('-plan_date', '-pk').first()
    if not cp or not (cp.wishes.strip() or cp.policy.strip() or cp.long_goal.strip() or cp.short_goal.strip() or cp.goals_text()):
        return ''
    body = [f'■ サービス等利用計画（{cp.office_name or "相談支援事業所"}{f" {_ymd(cp.plan_date)}" if cp.plan_date else ""}）']
    for label, value in (('意向', cp.wishes), ('方針', cp.policy), ('長期目標', cp.long_goal), ('短期目標', cp.short_goal)):
        if value.strip():
            body.append(f'{label}：' + value.strip().replace('\n', ' ')[:300])
    goals = cp.goals_text()
    if goals:
        body.append('課題と支援目標：' + '／'.join(goals[:6]))
    return '\n'.join(body)


def documents_text(beneficiary, exclude_consultation=None):
    """書類から分かっていること（要約・気をつけること・意向・目標）。相談支援のサービス等利用計画は見出しを分ける"""
    service_plan, others = [], []
    own = consultation_text(beneficiary, exclude_consultation)
    if own:
        service_plan.append(own)
    for k in knowledge_items(beneficiary):
        head = f'■ {k.kind}「{k.label}」' + (f'（{k.issuer}）' if k.issuer else '') + (f' {_ymd(k.doc_date)}' if k.doc_date else '')
        body = [head]
        if k.summary.strip():
            body.append('要約：' + k.summary.strip().replace('\n', ' ')[:400])
        if k.points_list:
            body.append('支援で気をつけること：' + '／'.join(k.points_list[:6]))
        if k.wishes_list:
            body.append('本人・家族の意向：' + '／'.join(k.wishes_list[:6]))
        if k.goals_list:
            body.append('目標：' + '／'.join(k.goals_list[:8]))
        if k.kind == 'サービス等利用計画':
            service_plan.append('\n'.join(body))
        else:
            others.append('\n'.join(body))
    return '\n'.join(service_plan), '\n'.join(others)


def ledger_text(beneficiary):
    """アセスメント・資料（最新）、5 領域アセスメント（最新）、療育記録の留意点"""
    from .sources import dev_text, latest_assessment
    parts = []
    a = latest_assessment(beneficiary)
    if a:
        parts.append(f'■ アセスメント・資料 {_ymd(a.date)}「{a.title}」\n{a.content.strip()[:1200]}')
    sheet = DevelopmentAssessment.objects.filter(beneficiary=beneficiary).order_by('-date', '-pk').first()
    if sheet:
        parts.append(f'■ 5領域アセスメント {_ymd(sheet.date)}\n{dev_text(sheet)}')
        if sheet.wishes_child or sheet.wishes_family:
            parts.append('■ 5領域アセスメントの希望\n' + '\n'.join(x for x in (f'本人：{sheet.wishes_child}' if sheet.wishes_child else '',
                                                                           f'家族：{sheet.wishes_family}' if sheet.wishes_family else '') if x))
    if uses_therapy(beneficiary):
        from therapy.models import TherapyProfile
        profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
        if profile and profile.cautions.strip():
            parts.append('■ 療育記録の留意点\n' + profile.cautions.strip()[:800])
    return '\n\n'.join(parts)


def collect(beneficiary, start, end, limit=RECORDS_MAX, exclude_consultation=None):
    """
    材料をまとめる。戻り値 dict：
      head（利用者の 1 行）、ledger（アセスメント・5領域・留意点）、service_plan（相談支援のサービス等利用計画）、
      documents（そのほかの書類）、lines（記録の Line の一覧）、kind（記録の種類名）、sources（使ったものの一覧。画面の文に）
    """
    b = beneficiary
    lines, kind = record_lines(b, start, end, limit)
    service_plan, documents = documents_text(b, exclude_consultation)
    ledger = ledger_text(b)
    sources = []
    if ledger:
        sources.append('アセスメント・5領域・留意点')
    if service_plan:
        sources.append('サービス等利用計画')
    if documents:
        sources.append('書類から分かっていること')
    if lines:
        sources.append(f'{kind} {len(lines)} 件')
    age = ''
    if b.date_of_birth:
        today = datetime.date.today()
        age = f'{today.year - b.date_of_birth.year - ((today.month, today.day) < (b.date_of_birth.month, b.date_of_birth.day))} 歳'
    head = f'{b.full_name}（{b.date_of_birth} 生 {age}、{b.disability_type or "障害種別未記入"}）'
    return {'head': head, 'ledger': ledger, 'service_plan': service_plan, 'documents': documents,
            'lines': lines, 'kind': kind, 'sources': sources}


def prompt_blocks(m, start=None, end=None):
    """collect() の結果を AI に渡す文に"""
    parts = [f'【利用者】{m["head"]}']
    if m['ledger']:
        parts.append('【台帳のアセスメント】\n' + m['ledger'])
    if m['service_plan']:
        parts.append('【相談支援事業所のサービス等利用計画（個別支援計画はこれに沿う）】\n' + m['service_plan'])
    if m['documents']:
        parts.append('【書類から分かっていること】\n' + m['documents'])
    if m['lines']:
        period = f'{start} 〜 {end}' if start and end else ''
        parts.append(f'【{m["kind"]}】{period}（{len(m["lines"])} 件、新しい順）\n' + '\n'.join(x.line() for x in m['lines']))
    return '\n\n'.join(parts)
