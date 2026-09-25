"""療育記録の画面（発達支援ルーム　ゆあーず）"""
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views import View

from accounts.models import StaffAccount
from ai_assist.quick import ask_ai as _ask_ai, tidy_sections
from ai_assist.text import clean_ai_text
from beneficiaries.models import Beneficiary
from config.utils import to_int

from . import figure
from .models import ACTIVITY_MAX, TherapyProfile, TherapyRecord

PAGE_ENTRIES = 5     # 用紙1枚に入る回数
LONG_CAUTIONS = 300  # これより長い留意点は、用紙の2枚目からは「1枚目のとおり」にする
CAUTIONS_MAX = 6000  # 留意点の長さ（「詳しくまとめる」で場面ごとに書くので長め）
SEARCH_MAX = 500     # 記録の検索で一度に出す件数


class TherapyEnabledMixin(LoginRequiredMixin):
    """施設設定で療育記録を使わない場合はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and not (facility is not None and facility.use_therapy_record):
            messages.info(request, 'この事業所では療育記録を使わない設定になっています。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


def _parse_date(value, default=None):
    try:
        return datetime.date.fromisoformat(value or '')
    except ValueError:
        return default


def _parse_time(value):
    value = (value or '').strip().replace('：', ':')
    if not value:
        return None
    try:
        h, _, m = value.partition(':')
        return datetime.time(int(h), int(m or 0))
    except ValueError:
        return None


def _activities_from_post(post):
    return [post.get(f'activity_{i}', '').strip()[:100] for i in range(1, ACTIVITY_MAX + 1)]


ACTIVITY_SUGGEST_MAX = 200   # 候補（datalist）に出す数
ACTIVITY_CHIP_MAX = 12       # 「よく使う」のボタンに出す数


def activity_suggestions(facility):
    """
    この事業所でこれまでに入れた「やったこと」。よく使う順（同じ回数なら最近の順）。
    だれの記録で入れたものでも、ほかの利用者の記録で候補に出す。
    """
    count, last = {}, {}
    rows = (TherapyRecord.objects.filter(facility=facility).order_by('-date', '-pk')
            .values_list('activities', flat=True)[:3000])
    for n, acts in enumerate(rows):
        for a in acts or []:
            a = a.strip() if isinstance(a, str) else ''
            if not a:
                continue
            count[a] = count.get(a, 0) + 1
            last.setdefault(a, n)
    return sorted(count, key=lambda a: (-count[a], last[a]))[:ACTIVITY_SUGGEST_MAX]


def filter_records(records, params):
    """
    療育記録の絞り込み。?ym=YYYY-MM（その月）と ?from=YYYY-MM-DD・?to=YYYY-MM-DD（日付の範囲）。
    戻り値は (絞った QuerySet, ym, 開始日, 終了日)。正しくない値は無視する
    """
    ym = params.get('ym', '')
    if ym:
        try:
            y, m = (int(x) for x in ym.split('-'))
            records = records.filter(date__year=y, date__month=m)
        except ValueError:
            ym = ''
    start, end = _parse_date(params.get('from')), _parse_date(params.get('to'))
    if start and end and start > end:
        start, end = end, start
    if start:
        records = records.filter(date__gte=start)
    if end:
        records = records.filter(date__lte=end)
    return records, ym, start, end


def search_records(facility, params):
    """
    事業所内の療育記録を横断して探す（ほかの利用者の記録を参考にするため）。
    child=名前（姓・名・かなの一部）、staff=担当の名前の一部（アカウントの表示名・ユーザー名・名前欄）、
    q=やったこと・記録の本文の言葉、ym / from / to=日付。空の条件は無視する
    """
    records = TherapyRecord.objects.filter(facility=facility).select_related('beneficiary', 'staff')
    child = params.get('child', '').strip()[:50]
    if child:
        for word in child.replace('　', ' ').split():
            records = records.filter(Q(beneficiary__last_name__icontains=word) | Q(beneficiary__first_name__icontains=word)
                                     | Q(beneficiary__last_name_kana__icontains=word)
                                     | Q(beneficiary__first_name_kana__icontains=word))
    staff = params.get('staff', '').strip()[:50]
    if staff:
        records = records.filter(Q(staff__display_name__icontains=staff) | Q(staff__username__icontains=staff)
                                 | Q(staff_name__icontains=staff))
    q = params.get('q', '').strip()[:100]
    records, ym, start, end = filter_records(records, params)
    if q:
        # 「やったこと」は JSON で保存されている（SQLite では日本語が \uXXXX になる）ので、DB ではなく Python で照らす
        words = [w.lower() for w in q.replace('　', ' ').split()]
        records = [r for r in records
                   if all(w in (r.body + ' ' + ' '.join(a for a in (r.activities or []) if isinstance(a, str))).lower()
                          for w in words)]
    return records, {'child': child, 'staff': staff, 'q': q, 'ym': ym, 'from': start, 'to': end}


def _todays_reservations(facility, day):
    """その日の予約（予約管理を使う事業所）。療育記録をすぐ書けるように並べる"""
    if not facility.use_reservation:
        return []
    from reservations.models import Reservation
    return list(Reservation.objects.filter(facility=facility, date=day, status=Reservation.STATUS_CONFIRMED,
                                           beneficiary__isnull=False)
                .select_related('beneficiary').order_by('start_time', 'created_at'))


class IndexView(TherapyEnabledMixin, View):
    """療育記録のホーム：今日の予約から書く／利用者ごとの記録へ"""
    template_name = 'therapy/index.html'

    def get(self, request):
        facility = request.user.facility
        day = _parse_date(request.GET.get('date'), datetime.date.today())
        written = set(TherapyRecord.objects.filter(facility=facility, date=day).values_list('beneficiary_id', flat=True))
        reservations = _todays_reservations(facility, day)
        for r in reservations:
            r.written = r.beneficiary_id in written
        stats = {row['beneficiary']: row for row in
                 TherapyRecord.objects.filter(facility=facility).values('beneficiary')
                 .annotate(n=Count('pk'), last=Max('date'))}
        children = []
        for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE):
            st = stats.get(b.pk, {})
            children.append({'beneficiary': b, 'count': st.get('n', 0), 'last': st.get('last')})
        return render(request, self.template_name, {
            'day': day, 'prev_day': day - datetime.timedelta(days=1), 'next_day': day + datetime.timedelta(days=1),
            'reservations': reservations, 'children': children,
            'written_count': sum(1 for r in reservations if r.written),
            'today_records': (TherapyRecord.objects.filter(facility=facility, date=day)
                              .select_related('beneficiary', 'staff').order_by('time', 'pk')),
        })


class SearchView(TherapyEnabledMixin, View):
    """療育記録の検索：日付・利用者名・職員名・言葉で事業所内の記録を横断して探し、ほかの利用者の記録に使う"""
    template_name = 'therapy/search.html'

    def get(self, request):
        facility = request.user.facility
        records, cond = search_records(facility, request.GET)
        searched = any(cond.values())
        records = list(records[:SEARCH_MAX + 1]) if searched else []
        total = len(records)      # 上限を超えたぶんは「もっとある」印だけ出す
        records = records[:SEARCH_MAX]
        months = TherapyRecord.objects.filter(facility=facility).dates('date', 'month', order='DESC')
        return render(request, self.template_name, {
            'records': records, 'cond': cond, 'searched': searched, 'total': total, 'search_max': SEARCH_MAX,
            'months': months,
            'children': Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE),
            'staff_list': StaffAccount.objects.filter(facility=facility, is_active=True).order_by('display_name', 'username'),
        })


class ChildView(TherapyEnabledMixin, View):
    """利用者1人の療育記録：留意点・記録の一覧・追加・修正・削除"""
    template_name = 'therapy/child.html'

    def get(self, request, pk):
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
        records = TherapyRecord.objects.filter(beneficiary=beneficiary).select_related('staff')
        months = [d for d in records.dates('date', 'month', order='DESC')]
        records, ym, date_from, date_to = filter_records(records, request.GET)
        filtered = bool(ym or date_from or date_to)
        records = list(records)      # 履歴は残したぶんすべて出す（件数の上限なし）
        suggestions = activity_suggestions(facility)
        copy_rec = None              # ほかの利用者の記録を「この内容で書く」で開いたとき
        if request.GET.get('copy'):
            copy_rec = TherapyRecord.objects.filter(facility=facility, pk=to_int(request.GET.get('copy'), -1)).first()
        filter_query = '&'.join(f'{k}={v}' for k, v in (('ym', ym), ('from', date_from and date_from.isoformat()),
                                                           ('to', date_to and date_to.isoformat())) if v)
        default_date = _parse_date(request.GET.get('date'), datetime.date.today())
        default_time = request.GET.get('time', '')
        return render(request, self.template_name, {
            'beneficiary': beneficiary, 'profile': profile, 'records': records, 'ym': ym, 'months': months,
            'date_from': date_from, 'date_to': date_to, 'filtered': filtered, 'filter_query': filter_query,
            'activity_suggestions': suggestions, 'activity_chips': suggestions[:ACTIVITY_CHIP_MAX],
            'staff_list': StaffAccount.objects.filter(facility=facility, is_active=True).order_by('display_name', 'username'),
            'default_date': default_date, 'default_time': default_time,
            'default_activities': list(copy_rec.activities or []) if copy_rec else [],
            'default_body': (copy_rec.body if copy_rec else ''), 'copy_rec': copy_rec,
            'activity_range': range(1, ACTIVITY_MAX + 1), 'edit_pk': to_int(request.GET.get('edit')),
            'cautions_rows': min(max(len((profile.cautions if profile else '').splitlines()) + 1, 4), 24),
            'fig': figure.build(profile.cautions if profile else '', beneficiary.full_name),
        })

    def post(self, request, pk):
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        back = redirect('therapy:child', pk=pk)
        p = request.POST
        action = p.get('action', 'add')

        if action == 'cautions':
            profile, _ = TherapyProfile.objects.get_or_create(beneficiary=beneficiary)
            profile.cautions = p.get('cautions', '').strip()[:CAUTIONS_MAX]
            profile.save()
            messages.success(request, '留意点を保存しました。')
            return back

        if action == 'delete':
            rec = get_object_or_404(TherapyRecord, pk=to_int(p.get('record'), -1), beneficiary=beneficiary)
            rec.delete()
            messages.success(request, f'{rec.date:%-m/%-d} の療育記録を削除しました。')
            return back

        day = _parse_date(p.get('date'))
        if day is None:
            messages.error(request, '日付を入れてください。')
            return back
        staff = StaffAccount.objects.filter(facility=facility, pk=to_int(p.get('staff'), -1)).first()
        fields = {
            'date': day, 'time': _parse_time(p.get('time')), 'staff': staff,
            'staff_name': p.get('staff_name', '').strip()[:50] if staff is None else '',
            'activities': _activities_from_post(p), 'body': p.get('body', '').strip()[:4000],
        }
        if action == 'edit':
            rec = get_object_or_404(TherapyRecord, pk=to_int(p.get('record'), -1), beneficiary=beneficiary)
            for k, v in fields.items():
                setattr(rec, k, v)
            rec.save()
            messages.success(request, f'{day:%-m/%-d} の療育記録を保存しました。')
            return back

        reservation = None
        if facility.use_reservation:
            from reservations.models import Reservation
            reservation = Reservation.objects.filter(facility=facility, beneficiary=beneficiary, date=day,
                                                     status=Reservation.STATUS_CONFIRMED).first()
        TherapyRecord.objects.create(facility=facility, beneficiary=beneficiary, reservation=reservation,
                                     created_by=request.user, **fields)
        messages.success(request, f'{day:%-m/%-d} の療育記録を追加しました。')
        return back


class PdfView(TherapyEnabledMixin, View):
    """療育記録の用紙（A4 縦・1枚に5回）。?ym=YYYY-MM でその月、?blank=1 で空の用紙"""

    def get(self, request, pk):
        from config.pdf import pdf_or_html
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
        blank = request.GET.get('blank') == '1'
        records, ym, date_from, date_to = [], '', None, None
        if not blank:
            qs = TherapyRecord.objects.filter(beneficiary=beneficiary).select_related('staff').order_by('date', 'time', 'pk')
            qs, ym, date_from, date_to = filter_records(qs, request.GET)
            records = list(qs)
        pages = []
        for i in range(0, max(len(records), 1), PAGE_ENTRIES):
            chunk = records[i:i + PAGE_ENTRIES]
            pages.append(chunk + [None] * (PAGE_ENTRIES - len(chunk)))
        ctx = {'beneficiary': beneficiary, 'profile': profile, 'pages': pages, 'facility': facility,
               'long_cautions': len(profile.cautions if profile else '') > LONG_CAUTIONS,
               'blank': blank, 'ym': ym, 'line_range': range(6)}
        name = f'療育記録_{beneficiary.full_name}' + (f'_{ym}' if ym else '')
        if date_from or date_to:
            name += f'_{date_from or ""}〜{date_to or ""}'
        return pdf_or_html(request, 'therapy/pdf/record.html', ctx, name)


class CautionsSummaryView(TherapyEnabledMixin, View):
    """
    留意点の要約。音声入力などで話し言葉のまま入った文を、用紙に載せる短い箇条書きに整えて返す。
    画面ではテキスト欄を書き換えるだけで、保存は職員が「留意点を保存」を押して行う。
    """

    SYSTEM_PROMPT = """あなたは放課後等デイサービス（療育）の職員を手伝うAIです。
職員が話した言葉や走り書きのメモから、その子の「留意点」（療育のときに職員が気をつけること）を作ります。

【書き方（必ず守る）】
- 1行に1項目の箇条書きにする。各行の先頭は「・」。3〜7項目、1項目は40字程度まで
- 体言止めか「〜する」で短く書く。敬語やあいさつ、前置き、まとめの文は書かない
- 入力にある事実だけを書く。推測や一般論、入力に無い対応方法を足さない
- 同じ内容は1つにまとめ、「えー」「あの」などの言いよどみや言い直しは除く
- 苦手なこと・危険につながること・配慮のしかたを先に、好きなこと・得意なことをあとに並べる
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・記号（★ ※ → など）・マークダウンは使わない
- 人名・物の名前は入力の表記のまま
- 返すのは箇条書きだけ"""

    DETAIL_PROMPT = """あなたは放課後等デイサービス（療育）の職員を手伝うAIです。
職員が話した言葉（療育の場面のようす・気づき・その子について気をつけること）を、
あとから読んだ職員が場面を思い浮かべられるように、**詳しく**整理して書き直します。

【形（必ず守る）】
【概要】
（全体を1〜2文で）

【場面や活動の名前】
・小見出し：内容を1〜3文で
・小見出し：内容
（場面・活動・部屋ごとに【】の見出しを分ける。話に出てきた順に並べる）

【全体のまとめ】
（全体の経過・変化を1〜3文で）

【気をつけること】
・（話の中で職員が「気をつける」「〜するとよい」「苦手」などと言ったことだけ。無ければこの見出しごと書かない）

【書き方】
- 子どもの言葉・職員の声かけは「」で、話したとおりに残す（例：「出して」と模倣した）
- 何をしたら・どうなったか（促し → 反応）が分かるように書く。回数や順番も話にあれば残す
- 常体（〜した。〜する。）で書く。敬語・あいさつ・前置きは書かない
- 話に無いことは足さない。推測や評価のことば（「成長が見られた」など）は、話した人が言ったときだけ書く
- 「えー」「あの」などの言いよどみ、言い直し、同じ話のくり返しは除く
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・マークダウン（# や ** ）は使わない。見出しは【】、項目は「・」だけを使う
- 返すのは整理した文だけ"""

    def post(self, request, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        text = request.POST.get('text', '').strip()
        detail = request.POST.get('mode') == 'detail'
        if not text:
            return JsonResponse({'error': '留意点が空です。先に音声入力か文字で入れてください。'}, status=400)
        if detail:
            raw, error = _ask_ai(self.DETAIL_PROMPT, f'【{beneficiary.full_name}さんについて職員が話したこと】\n{text[:CAUTIONS_MAX]}', 4096)
        else:
            raw, error = _ask_ai(self.SYSTEM_PROMPT, f'【{beneficiary.full_name}さんについてのメモ】\n{text[:CAUTIONS_MAX]}', 1024)
        if error:
            return error
        result = tidy_sections(raw) if detail else self.tidy(raw)
        if not result:
            return JsonResponse({'error': 'AIの返答が空でした。もう一度お試しください。'}, status=500)
        return JsonResponse({'result': result[:CAUTIONS_MAX]})

    @staticmethod
    def tidy(raw):
        """行頭の記号をそろえ、空行を除く"""
        lines = []
        for line in clean_ai_text(raw).splitlines():
            line = line.strip().lstrip('・-*•●○◦').strip()
            if line:
                lines.append(f'・{line}')
        return '\n'.join(lines)


class CautionsFigureView(TherapyEnabledMixin, View):
    """留意点の文（画面の欄の内容）から見取り図と表を作って返す。保存はしない（AI も使わない）"""

    def post(self, request, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        text = request.POST.get('text', '')[:CAUTIONS_MAX]
        fig = figure.build(text, beneficiary.full_name)
        html_ = render_to_string('therapy/_cautions_figure.html', {'fig': fig}, request=request)
        return JsonResponse({'html': html_, 'empty': fig['empty']})


class CautionsFigurePrintView(TherapyEnabledMixin, View):
    """保存されている留意点の見取り図と表を、A4 で印刷できる画面にする"""

    def get(self, request, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
        fig = figure.build(profile.cautions if profile else '', beneficiary.full_name)
        return render(request, 'therapy/cautions_print.html',
                      {'beneficiary': beneficiary, 'profile': profile, 'fig': fig, 'facility': request.user.facility})


RECORD_SUMMARY_MIN = 100   # 「記録」に足す文の長さ（文字）
RECORD_SUMMARY_MAX = 500


class RecordSummaryView(TherapyEnabledMixin, View):
    """
    「記録を追加する」の補助。留意点を、その日のやったこと（①〜⑤）に照らして 100〜500 字の文にまとめて返す。
    画面では「記録」の欄の末尾に足すだけで、保存は職員が「追加する」を押して行う。
    """

    SYSTEM_PROMPT = f"""あなたは放課後等デイサービス（療育）の職員を手伝うAIです。
その子の「留意点」（療育のときに職員が気をつけること）を、今日の「やったこと（活動）」に照らしてまとめ、
療育記録の「記録」の欄に書く文を作ります。

【内容】
- 留意点のうち、今日の活動に関係するものを選び、どの活動でどう気をつけるかが分かるように書く
- 活動の名前は入力の表記のまま使う（①②などの番号は付けない）
- 今日の活動に関係しない留意点は、大事なもの（安全・体調にかかわること）だけ短く添える
- 「記録（書きかけ）」があれば読んで、そこに書いてあることは繰り返さない

【書き方（必ず守る）】
- {RECORD_SUMMARY_MIN}字以上{RECORD_SUMMARY_MAX}字以内の、です・ます を使わない文（「〜に留意して取り組む。」「〜のため、〜する。」のような常体）
- 箇条書き・見出し・前置き・あいさつは書かない。段落は1〜3つ
- 入力にある事実だけを使う。子どものようす・反応・できたこと・結果は入力に無いので書かない（推測しない）
- 入力に無い対応方法や一般論を足さない。留意点が短いときは、無理に長くせず{RECORD_SUMMARY_MIN}字に近い長さでよい
- 常用漢字とひらがな・カタカナで書く。英語・絵文字・記号（★ ※ → など）・マークダウンは使わない
- 返すのは記録に書く文だけ"""

    def post(self, request, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        p = request.POST
        cautions = p.get('cautions', '').strip()
        if not cautions:
            profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
            cautions = profile.cautions.strip() if profile else ''
        if not cautions:
            return JsonResponse({'error': '留意点が空です。先に上の「留意点」を入れてください。'}, status=400)
        activities = [a for a in _activities_from_post(p) if a]
        if not activities:
            return JsonResponse({'error': '「やったこと（①〜⑤）」を1つ以上入れてから押してください。'}, status=400)
        day = _parse_date(p.get('date'))
        body = p.get('body', '').strip()
        content = (
            f'【{beneficiary.full_name}さんの留意点】\n{cautions[:CAUTIONS_MAX]}\n\n'
            + (f'【日付】{day.year}年{day.month}月{day.day}日\n' if day else '')
            + '【今日のやったこと】\n' + '\n'.join(f'・{a}' for a in activities)
            + (f'\n\n【記録（書きかけ）】\n{body[:2000]}' if body else '')
        )
        raw, error = _ask_ai(self.SYSTEM_PROMPT, content, 2048)
        if error:
            return error
        result = self.tidy(raw)
        if not result:
            return JsonResponse({'error': 'AIの返答が空でした。もう一度お試しください。'}, status=500)
        return JsonResponse({'result': result, 'length': len(result.replace('\n', ''))})

    @staticmethod
    def tidy(raw):
        """空行を1つにそろえ、500字を超えたら文の区切り（。）で切る"""
        paras = [''.join(x.strip() for x in block.splitlines() if x.strip())
                 for block in clean_ai_text(raw).split('\n\n')]
        text = '\n'.join(x for x in paras if x)
        if len(text.replace('\n', '')) <= RECORD_SUMMARY_MAX:
            return text
        cut, count = '', 0
        for sentence in text.replace('。', '。\x00').split('\x00'):
            n = len(sentence.replace('\n', ''))
            if count + n > RECORD_SUMMARY_MAX:
                break
            cut, count = cut + sentence, count + n
        return (cut or text[:RECORD_SUMMARY_MAX]).strip()
