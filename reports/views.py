"""
帳票出力：一覧画面と、CSV／PDF／画面表示のエクスポート
"""
import csv
import io
import urllib.parse
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from beneficiaries.models import Beneficiary
from facilities.context_processors import get_terms

from . import services
from .services import REPORT_KINDS
from config.pdf import media_url_fetcher
from config.utils import to_int


class ReportIndexView(LoginRequiredMixin, View):
    def get(self, request):
        facility = request.user.facility
        today = date.today()
        terms = get_terms(request.user)
        kinds = [dict(key=k, title=v['title'].format(beneficiary=terms['beneficiary']),
                      desc=v['desc'].format(beneficiary=terms['beneficiary']), icon=v['icon'], filters=v['filters'])
                 for k, v in REPORT_KINDS.items()]
        return render(request, 'reports/index.html', {
            'kinds': kinds,
            'beneficiaries': Beneficiary.objects.filter(facility=facility).order_by('status', 'last_name_kana', 'first_name_kana'),
            'today': today, 'month_start': today.replace(day=1),
            'range_start': today - timedelta(days=90),
            'plan_statuses': [('all', 'すべて'), ('open', '作成中・実施中'), ('active', '実施中'), ('closed', '終了')],
        })


def _parse_date(s, fallback):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return fallback


class ReportExportView(LoginRequiredMixin, View):
    def get(self, request):
        facility = request.user.facility
        p = request.GET
        kind = p.get('kind', '')
        fmt = p.get('fmt', 'pdf')
        if kind not in REPORT_KINDS:
            messages.error(request, '帳票の種類が正しくありません。')
            return redirect('reports:index')

        today = date.today()
        try:
            year, month = int(p.get('year', today.year)), int(p.get('month', today.month))
            if not 1 <= month <= 12 or not 2000 <= year <= 2100:
                raise ValueError
        except ValueError:
            messages.error(request, '対象月が正しくありません。')
            return redirect('reports:index')

        beneficiary = None
        if 'beneficiary' in REPORT_KINDS[kind]['filters']:
            beneficiary = get_object_or_404(Beneficiary, pk=to_int(p.get('beneficiary'), -1), facility=facility)

        if kind == 'service_record':
            report = services.service_record(facility, beneficiary, year, month)
        elif kind == 'attendance_summary':
            report = services.attendance_summary(facility, year, month)
        elif kind == 'daily_journal':
            report = services.daily_journal(facility, _parse_date(p.get('date'), today))
        elif kind == 'beneficiary_records':
            start = _parse_date(p.get('start'), today - timedelta(days=90))
            end = _parse_date(p.get('end'), today)
            if start > end:
                start, end = end, start
            report = services.beneficiary_records(facility, beneficiary, start, end)
        elif kind == 'beneficiary_roster':
            report = services.beneficiary_roster(facility, include_inactive=p.get('include_inactive') == '1')
        else:
            report = services.plan_list(facility, status=p.get('plan_status', 'all'))

        if fmt == 'csv':
            return self._csv(report)
        ctx = {'report': report, 'facility': facility, 'printed_at': timezone.localtime(), 'pdf': fmt == 'pdf', 'user': request.user}
        if fmt == 'pdf':
            return self._pdf(request, report, ctx)
        return render(request, 'reports/print.html', ctx)

    @staticmethod
    def _content_disposition(response, ascii_name, utf8_name):
        response['Content-Disposition'] = (f'attachment; filename="{ascii_name}"; '
                                           f"filename*=UTF-8''{urllib.parse.quote(utf8_name)}")

    def _csv(self, report):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([report.title, report.subtitle])
        for label, value in report.meta:
            writer.writerow([label, value])
        writer.writerow([])
        writer.writerow(report.columns)
        for row in report.rows:
            writer.writerow(row)
        # BOM は先頭に1つだけ（行ごとに付かないよう、まとめて encode する）
        response = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        self._content_disposition(response, f'{report.key}.csv', f'{report.filename}.csv')
        return response

    def _pdf(self, request, report, ctx):
        html = render(request, 'reports/print.html', ctx).content.decode('utf-8')
        try:
            from weasyprint import HTML
            pdf = HTML(string=html, base_url=request.build_absolute_uri('/'), url_fetcher=media_url_fetcher).write_pdf()
        except (ImportError, OSError) as e:  # サーバーに WeasyPrint の共有ライブラリ（pango 等）が無い
            return HttpResponse(f'PDF を作成できません（サーバーに PDF 用ライブラリがありません）: {e}\n「画面で見る」から印刷してください。',
                                status=500, content_type='text/plain; charset=utf-8')
        response = HttpResponse(pdf, content_type='application/pdf')
        self._content_disposition(response, f'{report.key}.pdf', f'{report.filename}.pdf')
        return response
