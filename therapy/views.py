"""療育記録の画面（発達支援ルーム　ゆあーず）"""
import datetime
import logging

import anthropic
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from accounts.models import StaffAccount
from ai_assist.text import clean_ai_text, effort_kwargs
from beneficiaries.models import Beneficiary
from config.utils import to_int

from .models import ACTIVITY_MAX, TherapyProfile, TherapyRecord

PAGE_ENTRIES = 5     # 用紙1枚に入る回数
CAUTIONS_MAX = 2000  # 留意点の長さ

logger = logging.getLogger(__name__)


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


class ChildView(TherapyEnabledMixin, View):
    """利用者1人の療育記録：留意点・記録の一覧・追加・修正・削除"""
    template_name = 'therapy/child.html'

    def get(self, request, pk):
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        profile = TherapyProfile.objects.filter(beneficiary=beneficiary).first()
        records = TherapyRecord.objects.filter(beneficiary=beneficiary).select_related('staff')
        ym = request.GET.get('ym', '')
        months = [d for d in records.dates('date', 'month', order='DESC')]
        if ym:
            try:
                y, m = (int(x) for x in ym.split('-'))
                records = records.filter(date__year=y, date__month=m)
            except ValueError:
                ym = ''
        records = list(records[:200] if not ym else records)
        default_date = _parse_date(request.GET.get('date'), datetime.date.today())
        default_time = request.GET.get('time', '')
        return render(request, self.template_name, {
            'beneficiary': beneficiary, 'profile': profile, 'records': records, 'ym': ym, 'months': months,
            'staff_list': StaffAccount.objects.filter(facility=facility, is_active=True).order_by('display_name', 'username'),
            'default_date': default_date, 'default_time': default_time,
            'activity_range': range(1, ACTIVITY_MAX + 1), 'edit_pk': to_int(request.GET.get('edit')),
            'cautions_rows': min(max(len((profile.cautions if profile else '').splitlines()) + 1, 4), 12),
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
        ym = request.GET.get('ym', '')
        records = []
        if not blank:
            qs = TherapyRecord.objects.filter(beneficiary=beneficiary).select_related('staff').order_by('date', 'time', 'pk')
            if ym:
                try:
                    y, m = (int(x) for x in ym.split('-'))
                    qs = qs.filter(date__year=y, date__month=m)
                except ValueError:
                    pass
            records = list(qs)
        pages = []
        for i in range(0, max(len(records), 1), PAGE_ENTRIES):
            chunk = records[i:i + PAGE_ENTRIES]
            pages.append(chunk + [None] * (PAGE_ENTRIES - len(chunk)))
        ctx = {'beneficiary': beneficiary, 'profile': profile, 'pages': pages, 'facility': facility,
               'blank': blank, 'ym': ym, 'line_range': range(6)}
        name = f'療育記録_{beneficiary.full_name}' + (f'_{ym}' if ym else '')
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

    def post(self, request, pk):
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=request.user.facility)
        text = request.POST.get('text', '').strip()
        if not text:
            return JsonResponse({'error': '留意点が空です。先に音声入力か文字で入れてください。'}, status=400)
        if not settings.ANTHROPIC_API_KEY:
            return JsonResponse({'error': 'AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。管理者に設定を依頼してください。'},
                                status=500)
        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.AI_TEXT_MODEL, max_tokens=1024, **effort_kwargs(settings.AI_TEXT_MODEL),
                system=self.SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': f'【{beneficiary.full_name}さんについてのメモ】\n{text[:6000]}'}],
            )
            raw = ''.join(b.text for b in response.content if b.type == 'text')
        except Exception as e:  # noqa: BLE001
            logger.exception('留意点の要約でエラー')
            return JsonResponse({'error': f'AIでの要約中にエラーが発生しました: {type(e).__name__}'}, status=500)
        result = self.tidy(raw)
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
