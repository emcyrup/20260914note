"""療育記録の画面（りょういく）"""
import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from config.utils import to_int

from .models import ACTIVITY_MAX, TherapyProfile, TherapyRecord

PAGE_ENTRIES = 5     # 用紙1枚に入る回数


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
        })

    def post(self, request, pk):
        facility = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=pk, facility=facility)
        back = redirect('therapy:child', pk=pk)
        p = request.POST
        action = p.get('action', 'add')

        if action == 'cautions':
            profile, _ = TherapyProfile.objects.get_or_create(beneficiary=beneficiary)
            profile.cautions = p.get('cautions', '').strip()[:2000]
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
