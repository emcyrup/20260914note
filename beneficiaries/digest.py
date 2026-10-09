"""
面談資料（記録のまとめ）：期間を選んで、その子の日誌・療育記録・5領域アセスメント・支援計画の目標を集め、
数字（利用回数・記録の件数など）は計算で、文章は AI で「記録にあることだけ」を使ってまとめる。
半期の支援計画の見直し・モニタリング・保護者面談の準備に使う（過去 3 か月・半年など）。
"""
import datetime

from ai_assist.quick import ask_ai, tidy_sections

MAX_RECORDS = 120          # AI に渡す記録の数の上限（多いときは期間全体から均等に選ぶ）
LINE_MAX = 300             # 1 件の記録の長さの上限（字）
PERIODS = (('3m', '過去 3 か月', 3), ('6m', '過去 6 か月（半期）', 6), ('1m', '過去 1 か月', 1))

SYSTEM_PROMPT = """あなたは放課後等デイサービス・児童発達支援の児童発達支援管理責任者を手伝うAIです。
ある子どもの、決まった期間の記録（日誌・療育記録）と、5領域アセスメント・個別支援計画の目標を読み、
保護者面談・モニタリング・次の支援計画の見直しに使う「面談資料」の文章を作ります。

【いちばん大事なこと：記録にあることだけ】
- 記録に書いてある事実と言葉だけを使う。記録に無い出来事・子どもの言葉・数字・評価を作らない
- AI の見解・診断・一般論・飾りの言葉（「著しい成長」「意欲的に」など、記録に無い評価）を足さない
- 書けることが記録に無い項目は「この期間の記録には見当たりません」と書く
- 具体的な場面には日付を付ける（例：10/3）。子どもの言葉は記録のとおり「」で残す

【形（見出しは【】、項目は「・」。この順で）】
【この期間の様子（全体）】
（3〜5文）
【5領域ごとの様子】
・健康・生活：
・運動・感覚：
・認知・行動：
・言語・コミュニケーション：
・人間関係・社会性：
【支援計画の目標に対する様子】
・（目標）：（記録にある様子。目標が無いときはこの見出しごと書かない）
【印象に残った場面】
・（日付）：（出来事。3〜6 項目）
【保護者に伝えたいこと】
（2〜4文。です・ます調）
【次の計画に向けて（記録から見えること）】
・（記録の中で、くり返し出てくる苦手・できるようになりつつあること。2〜4 項目）

【書き方】
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・マークダウン（# や **）は使わない
- 返すのは面談資料の文章だけ"""


def period_range(key, today=None, date_from=None, date_to=None):
    """期間の選び方（3m・6m・1m、または from〜to）→ (開始, 終了)"""
    today = today or datetime.date.today()
    if key == 'custom' and date_from and date_to and date_from <= date_to:
        return date_from, date_to
    months = dict((k, m) for k, _, m in PERIODS).get(key, 3)
    y, m = today.year, today.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    day = min(today.day, 28)
    return datetime.date(y, m, day) + datetime.timedelta(days=1), today


def _clip(text, n=LINE_MAX):
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n] + '…'


def _sample(items, n):
    """多すぎるときは期間全体から均等に n 件選ぶ（新しい記録に偏らないように）"""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def collect(beneficiary, start, end):
    """期間の記録と数字を集める。戻り値は dict（lines・stats・goals・assessment）"""
    from records.models import DailyRecord
    from therapy.models import TherapyRecord
    lines = []
    journals = list(DailyRecord.objects.filter(beneficiary=beneficiary, facility_id=beneficiary.facility_id,
                                               date__range=(start, end)).order_by('date'))
    for r in journals:
        body = ' / '.join(p for p in (r.observation_text, r.support_text, r.reaction_text) if p) or r.observation_memo
        if body:
            lines.append((r.date, f'日誌{"（" + r.activity_name + "）" if r.activity_name else ""}', body))
    therapy = list(TherapyRecord.objects.filter(beneficiary=beneficiary, date__range=(start, end)).order_by('date', 'time'))
    for r in therapy:
        acts = '・'.join(a for a in (r.activities or []) if isinstance(a, str) and a)
        if r.body or r.situation or acts:
            text = '\n'.join(x for x in (r.body, f'様子：{r.situation}' if r.situation else '') if x)
            lines.append((r.date, f'療育記録{"（" + acts + "）" if acts else ""}', text))
    lines.sort(key=lambda x: x[0])
    stats = {'journals': len(journals), 'therapy': len(therapy), 'days': len({x[0] for x in lines})}
    try:
        from reservations.models import Reservation
        res = Reservation.objects.filter(beneficiary=beneficiary, date__range=(start, end), status=Reservation.STATUS_CONFIRMED)
        stats['reserved'] = res.count()
        stats['attended'] = res.filter(attendance=Reservation.ATT_ATTENDED).count()
        stats['absent'] = res.filter(attendance__in=(Reservation.ATT_ABSENT, Reservation.ATT_CANCELLED)).count()
    except Exception:       # 予約を使わない事業所など
        pass
    try:
        from daily.models import HealthLog
        logs = HealthLog.objects.filter(beneficiary=beneficiary, date__range=(start, end))
        stats['health_days'] = logs.count()
        stats['fever_days'] = sum(1 for h in logs if h.fever)
    except Exception:
        pass
    goals = []
    try:
        from support_plans.models import SupportPlan
        plan = SupportPlan.objects.filter(beneficiary=beneficiary).order_by('-created_at').first()
        if plan:
            goals = [(g.get_goal_type_display(), g.content) for g in plan.goals.order_by('goal_type', 'order', 'pk') if g.content]
    except Exception:
        pass
    assessment = None
    from .models import DevelopmentAssessment
    a = DevelopmentAssessment.objects.filter(beneficiary=beneficiary, date__lte=end).order_by('-date', '-pk').first()
    if a:
        assessment = a
    return {'lines': lines, 'stats': stats, 'goals': goals, 'assessment': assessment}


def build_content(beneficiary, start, end, data):
    parts = [f'【対象】{beneficiary.full_name}さん　【期間】{start:%Y/%m/%d}〜{end:%Y/%m/%d}']
    if data['goals']:
        parts.append('【個別支援計画の目標】\n' + '\n'.join(f'・{t}：{c}' for t, c in data['goals']))
    a = data['assessment']
    if a:
        rows = []
        for row in a.rows():
            text = '　'.join(x for x in (f'評価 {row["rating"]}' if row.get('rating') else '', row.get('now') or '',
                                       f'目標：{row["goal"]}' if row.get('goal') else '') if x)
            if text:
                rows.append(f'・{row["label"]}：{text}')
        if rows:
            parts.append(f'【5領域アセスメント（{a.date:%Y/%m/%d}）】\n' + '\n'.join(rows))
    picked = _sample(data['lines'], MAX_RECORDS)
    parts.append(f'【記録（{len(picked)} 件{"・期間全体から均等に選んだもの" if len(picked) < len(data["lines"]) else ""}）】\n'
                 + '\n'.join(f'- {d.month}/{d.day} {kind}：{_clip(text)}' for d, kind, text in picked))
    return '\n\n'.join(parts)


def summarize(beneficiary, start, end, data):
    """AI で面談資料の文章を作る。戻り値 (文章, エラーの JsonResponse)"""
    raw, error = ask_ai(SYSTEM_PROMPT, build_content(beneficiary, start, end, data), 4096)
    if error:
        return None, error
    return tidy_sections(raw), None
