"""
請求管理のビュー
- BillingMatrixView : 請求マトリックス画面（利用者×日付グリッド）
- CellPopupView     : セルタップ時のポップアップ内容を返す（HTMX partial）
- CellUpdateView    : セルの状態・加算を保存する
- LoadFromScheduleView : 月の予定を一括読み込み
"""

import calendar
import csv
import urllib.parse
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from beneficiaries.models import Beneficiary
from facilities.models import SupportContentTag, AddonMaster, REGION_UNIT_PRICE, facility_addon_rows
from schedules.models import ScheduledVisit

from .forms import CopaymentManagementForm, CopaymentOfficeRecordForm
from .models import BillingMatrixAddon, BillingMatrixEntry, CopaymentManagement, CopaymentOfficeRecord
from config.utils import date_or_404, month_or_404, to_int
from django.urls import reverse
from config.concurrency import check_conflict


class BillingEnabledMixin:
    """施設設定で請求機能を使わない場合はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and facility is not None and not facility.use_billing:
            messages.info(request, '請求機能はこの事業所では使わない設定です（施設設定 → 使う機能 で変更できます）。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


class BillingMatrixView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    請求マトリックス画面（S-07）
    縦軸＝利用者、横軸＝日付で、月ごとの利用状況を一覧表示する。
    ScheduledVisit（確認済み来所）をベースに表示し、
    BillingMatrixEntry が保存済みのセルは確定済みとして扱う。
    """

    def get(self, request, year=None, month=None):
        today = date.today()
        if year is None:
            year = today.year
        if month is None:
            month = today.month

        # 前月・翌月の計算
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        year, month = month_or_404(year, month)
        _, days_in_month = calendar.monthrange(year, month)
        days = list(range(1, days_in_month + 1))
        day_headers = [
            {'day': d, 'weekday': date(year, month, d).weekday()}
            for d in days
        ]

        facility = request.user.facility

        # 在籍中の利用者（50音順）
        beneficiaries = Beneficiary.objects.filter(
            facility=facility,
            status='active',
        ).order_by('last_name_kana', 'first_name_kana')

        # 該当月のScheduledVisitを取得
        # 「来所済み（attended）」と「欠席（absent）」のみ対象とする
        # ・scheduled（予定のみ）：実際に来所したか未確認 → 請求対象外
        # ・canceled（施設都合キャンセル）：請求対象外
        # ・transferred（振替）：BillingMatrixEntryで直接管理
        visits = ScheduledVisit.objects.filter(
            facility=facility,
            date__year=year,
            date__month=month,
            status__in=['attended', 'absent'],
        )

        # visit_map: {(beneficiary_id, day): visit_status}
        visit_map = {
            (v.beneficiary_id, v.date.day): v.status
            for v in visits
        }

        # BillingMatrixEntry の取得（加算数・確認済みフラグ用）
        entries = BillingMatrixEntry.objects.filter(
            facility=facility,
            date__year=year,
            date__month=month,
        ).prefetch_related('addons')

        # addon_count_map:  {(beneficiary_id, day): addon_count}
        # saved_entry_map:  {(beneficiary_id, day): entry_status}
        # confirmed_set:    ポップアップで手動確認済みのセル集合（is_finalized=True）
        addon_count_map = {}
        saved_entry_map = {}
        confirmed_set   = set()
        for entry in entries:
            key = (entry.beneficiary_id, entry.date.day)
            saved_entry_map[key] = entry.status
            if entry.is_finalized:
                confirmed_set.add(key)
            count = entry.addons.filter(is_applied=True).count()
            if count > 0:
                addon_count_map[key] = count

        # マトリックスデータを組み立て
        matrix = []
        for beneficiary in beneficiaries:
            cells = []
            attended_count = 0
            for day in days:
                key = (beneficiary.pk, day)
                visit_status  = visit_map.get(key)        # ScheduledVisit のステータス
                entry_status  = saved_entry_map.get(key)  # BillingMatrixEntry のステータス
                addon_count   = addon_count_map.get(key, 0)

                # 表示ステータス：BillingMatrixEntry 優先、なければ ScheduledVisit
                display_status = entry_status if entry_status is not None else visit_status
                # ポップアップで手動確認済み（is_finalized=True）のときのみ is_saved=True
                # 一括確定（LoadFromScheduleView）で作成されたエントリーは is_finalized=False → ◆表示
                is_saved       = key in confirmed_set
                is_clickable   = display_status is not None  # 予定または確定済みがある

                cells.append({
                    'day':            day,
                    'display_status': display_status,
                    'addon_count':    addon_count,
                    'is_saved':       is_saved,
                    'is_clickable':   is_clickable,
                })
                if display_status == 'attended':
                    attended_count += 1

            matrix.append({
                'beneficiary':   beneficiary,
                'cells':         cells,
                'attended_count': attended_count,
            })

        total_attended_days = sum(row['attended_count'] for row in matrix)

        # 未確認セル（ScheduledVisitはあるがBillingMatrixEntryがないセル）の件数
        unsaved_count = sum(
            1
            for row in matrix
            for cell in row['cells']
            if cell['display_status'] is not None and not cell['is_saved']
        )

        return render(request, 'billing/matrix.html', {
            'year':               year,
            'month':              month,
            'days':               days,
            'day_headers':        day_headers,
            'matrix':             matrix,
            'total_attended_days': total_attended_days,
            'prev_year':          prev_year,
            'prev_month':         prev_month,
            'next_year':          next_year,
            'next_month':         next_month,
            'today':              today,
            'unsaved_count':      unsaved_count,
        })


class CellPopupView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    セルタップ時のポップアップ内容を返す（HTMX partial）。
    利用者×日付の状態・加算を確認・編集するフォームを表示する。
    """

    def get(self, request, beneficiary_pk, year, month, day):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        target_date = date_or_404(year, month, day)

        # ScheduledVisit から現在の状態を取得（「予定」は除外）
        visit = ScheduledVisit.objects.filter(
            facility=facility,
            beneficiary=beneficiary,
            date=target_date,
        ).exclude(status='scheduled').first()

        # BillingMatrixEntry を取得（なければ visit の状態を初期値として使用）
        entry = BillingMatrixEntry.objects.filter(
            facility=facility,
            beneficiary=beneficiary,
            date=target_date,
        ).first()

        current_status = entry.status if entry else (visit.status if visit else 'attended')

        # 現在適用されている加算のIDセット
        applied_addon_ids = set()
        if entry:
            applied_addon_ids = set(
                BillingMatrixAddon.objects.filter(entry=entry, is_applied=True)
                .values_list('addon_id', flat=True)
            )

        # 個別加算の一覧
        individual_addons = AddonMaster.objects.filter(is_active=True, addon_type='individual')

        # 手動保存済みの加算がない場合のみ自動提案を実施
        is_auto_suggested = not bool(applied_addon_ids)
        if is_auto_suggested:
            applied_addon_ids = _suggest_addons(visit, facility, target_date, beneficiary, individual_addons)

        rows = {r.pk: r for r in facility_addon_rows(facility, addon_type='individual')}
        addon_list = [
            {'addon': a, 'is_applied': a.pk in applied_addon_ids, 'row': rows.get(a.pk)}
            for a in individual_addons
        ]

        # 日次記録が存在するか確認（リンク用）
        from records.models import DailyRecord
        daily_record = DailyRecord.objects.filter(
            facility=facility,
            beneficiary=beneficiary,
            date=target_date,
        ).first()

        return render(request, 'billing/cell_popup.html', {
            'beneficiary':    beneficiary,
            'target_date':    target_date,
            'year':           year,
            'month':          month,
            'day':            day,
            'current_status': current_status,
            'addon_list':     addon_list,
            'is_auto_suggested': is_auto_suggested,
            'daily_record':   daily_record,
        })


def _suggest_addons(visit, facility, target_date, beneficiary, individual_addons):
    """
    日次記録・予定データをもとに加算の候補IDセットを返す（自動チェック用）。
    手動保存済みのケースでは呼ばれない。
    """
    import datetime
    from records.models import DailyRecord

    suggested_ids = set()

    addon_map = {a.name: a.pk for a in individual_addons}

    def find_addon_id(keyword):
        for name, pk in addon_map.items():
            if keyword in name:
                return pk
        return None

    # 欠席時対応加算：欠席ステータスから判定
    if visit and visit.status == 'absent':
        pk = find_addon_id('欠席時対応加算')
        if pk:
            suggested_ids.add(pk)

    # 送迎加算：ScheduledVisit の has_pickup / has_dropoff チェックボックスで判定
    # （日誌の支援タグより優先。予定で明示的にチェックされた情報を使う）
    if visit:
        if visit.has_pickup:
            pk = find_addon_id('送迎加算（往')
            if pk:
                suggested_ids.add(pk)
        if visit.has_dropoff:
            pk = find_addon_id('送迎加算（復')
            if pk:
                suggested_ids.add(pk)

    # 日次記録データが必要な加算（DailyRecord を取得）
    try:
        record = DailyRecord.objects.get(
            facility=facility,
            beneficiary=beneficiary,
            date=target_date,
        )

        # 延長支援加算：退室時間と施設の通常終了時刻を比較
        if record.exit_time and facility.standard_close_time:
            close_dt  = datetime.datetime.combine(target_date, facility.standard_close_time)
            depart_dt = datetime.datetime.combine(target_date, record.exit_time)
            diff_minutes = (depart_dt - close_dt).total_seconds() / 60

            if 30 <= diff_minutes < 60:
                pk = find_addon_id('延長支援加算（30分以上1時間未満）')
                if pk:
                    suggested_ids.add(pk)
            elif 60 <= diff_minutes < 120:
                pk = find_addon_id('延長支援加算（1時間以上2時間未満）')
                if pk:
                    suggested_ids.add(pk)
            elif diff_minutes >= 120:
                pk = find_addon_id('延長支援加算（2時間以上）')
                if pk:
                    suggested_ids.add(pk)

        # 送迎加算（補完）：日誌の支援タグ「迎え支援」「送り支援」でも判定する
        # 予定チェックと日誌タグのどちらかに入っていれば加算を提案する
        if record.support_tags.filter(name__icontains='迎え').exists():
            pk = find_addon_id('送迎加算（往')
            if pk:
                suggested_ids.add(pk)
        if record.support_tags.filter(name__icontains='送り').exists():
            pk = find_addon_id('送迎加算（復')
            if pk:
                suggested_ids.add(pk)

        # 入浴支援加算：支援内容タグ名に「入浴」が含まれるか確認
        if record.support_tags.filter(name__icontains='入浴').exists():
            pk = find_addon_id('入浴支援加算')
            if pk:
                suggested_ids.add(pk)

    except DailyRecord.DoesNotExist:
        pass

    return suggested_ids


class CellUpdateView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    セルの状態・加算を保存する。
    BillingMatrixEntry と ScheduledVisit を両方更新して整合性を保つ。
    """

    def post(self, request, beneficiary_pk, year, month, day):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        target_date = date_or_404(year, month, day)

        new_status = request.POST.get('status')
        if new_status not in ('attended', 'absent', 'transferred'):
            new_status = 'attended'

        # BillingMatrixEntry を更新または作成
        # is_finalized=True = ポップアップで手動確認済み（◆を消す）
        entry, _ = BillingMatrixEntry.objects.update_or_create(
            facility    = facility,
            beneficiary = beneficiary,
            date        = target_date,
            defaults    = {'status': new_status, 'is_finalized': True},
        )

        # ScheduledVisit の状態も同期更新（整合性のため）
        ScheduledVisit.objects.filter(
            facility    = facility,
            beneficiary = beneficiary,
            date        = target_date,
        ).exclude(status='scheduled').update(status=new_status)

        # 加算を更新（いったん全削除して選択されたものを再登録）
        BillingMatrixAddon.objects.filter(entry=entry).delete()
        selected_addon_ids = request.POST.getlist('addon_ids')
        for addon_id in selected_addon_ids:
            try:
                addon = AddonMaster.objects.get(pk=addon_id, is_active=True, addon_type='individual')
                BillingMatrixAddon.objects.create(entry=entry, addon=addon, is_applied=True)
            except AddonMaster.DoesNotExist:
                pass

        addon_count = len(selected_addon_ids)
        messages.success(
            request,
            f'{beneficiary.full_name}（{month}月{day}日）を保存しました。'
            + (f'　加算 {addon_count}件' if addon_count else ''),
        )
        return redirect('billing:matrix_month', year=year, month=month)


class LoadFromScheduleView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    指定年月の ScheduledVisit を一括読み込みして BillingMatrixEntry を作成する。
    すでにエントリーが存在するセルは上書きしない。
    """

    STATUS_MAP = {
        'attended': 'attended',
        'absent':   'absent',
        # scheduled / canceled はスキップ
    }

    def post(self, request, year, month):
        facility = request.user.facility
        year, month = month_or_404(year, month)

        visits = ScheduledVisit.objects.filter(
            beneficiary__facility=facility,
            date__year=year,
            date__month=month,
        ).filter(status__in=['attended', 'absent']).select_related('beneficiary')

        created = 0
        skipped = 0
        for visit in visits:
            billing_status = self.STATUS_MAP.get(visit.status)
            if not billing_status:
                continue
            _, is_new = BillingMatrixEntry.objects.get_or_create(
                facility    = facility,
                beneficiary = visit.beneficiary,
                date        = visit.date,
                defaults    = {'status': billing_status},
            )
            if is_new:
                created += 1
            else:
                skipped += 1

        if created:
            msg = f'{year}年{month}月の予定を {created} 件一括確定しました。'
            if skipped:
                msg += f'（{skipped} 件は既入力のためスキップ）'
            messages.success(request, msg)
        else:
            messages.info(request, f'新たに読み込める予定はありませんでした。（既入力 {skipped} 件）')

        return redirect('billing:matrix_month', year=year, month=month)


class CopaymentListView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    利用者負担上限額管理 一覧（月次）。
    在籍中の利用者ごとに、当月の上限額管理レコードの有無を表示する。
    """

    def get(self, request, year=None, month=None):
        today = date.today()
        if year is None:
            year = today.year
        if month is None:
            month = today.month
        year, month = month_or_404(year, month)

        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        facility = request.user.facility
        year_month = f'{year}-{month:02d}'

        beneficiaries = Beneficiary.objects.filter(
            facility=facility,
            status='active',
        ).order_by('last_name_kana', 'first_name_kana')

        mgmt_map = {
            m.beneficiary_id: m
            for m in CopaymentManagement.objects.filter(
                facility=facility,
                year_month=year_month,
            )
        }

        rows = []
        for b in beneficiaries:
            m = mgmt_map.get(b.pk)
            rows.append({
                'beneficiary': b,
                'management':  m,
                'can_print_sheet': bool(m and m.is_upper_limit_manager),
                'is_manager_here': b.is_copayment_manager_here,
            })

        return render(request, 'billing/copayment_list.html', {
            'year':       year,
            'month':      month,
            'prev_year':  prev_year,
            'prev_month': prev_month,
            'next_year':  next_year,
            'next_month': next_month,
            'rows':       rows,
            'today':      today,
        })


class CopaymentEditView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    利用者負担上限額管理 編集画面。
    CopaymentManagement 本体＋事業所別実績（formset）を同時に保存する。
    """

    def _get_formset_class(self, extra=3):
        return inlineformset_factory(
            CopaymentManagement,
            CopaymentOfficeRecord,
            form=CopaymentOfficeRecordForm,
            extra=extra,
            can_delete=True,
        )

    @staticmethod
    def _office_initials(facility, beneficiary):
        """利用者に登録した利用事業所から、実績行の初期値を作る（当施設を先頭に）"""
        offices = list(beneficiary.offices.all())
        rows = []
        if not any(o.is_this_office for o in offices):
            rows.append({'is_this_office': True, 'office_name': facility.name, 'office_number': facility.office_number})
        for o in offices:
            rows.append({'is_this_office': o.is_this_office,
                         'office_name': facility.name if o.is_this_office else o.name,
                         'office_number': (facility.office_number or o.office_number) if o.is_this_office else o.office_number})
        return rows

    def get(self, request, beneficiary_pk, year, month):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        year_month  = f'{year}-{month:02d}'

        management = CopaymentManagement.objects.filter(
            facility=facility,
            beneficiary=beneficiary,
            year_month=year_month,
        ).first()

        if management:
            form = CopaymentManagementForm(instance=management)
            formset = self._get_formset_class()(instance=management)
        else:
            # 利用者に登録した利用事業所を初期値にする（上限管理事業所のフラグも）
            initials = self._office_initials(facility, beneficiary)
            form = CopaymentManagementForm(instance=CopaymentManagement(
                is_upper_limit_manager=beneficiary.is_copayment_manager_here or not beneficiary.offices.exists(),
            ))
            formset = self._get_formset_class(extra=max(3, len(initials) + 1))(instance=CopaymentManagement())
            for f, init in zip(formset.forms, initials):
                f.initial = init

        return render(request, 'billing/copayment_edit.html', {
            'beneficiary': beneficiary,
            'year':        year,
            'month':       month,
            'year_month':  year_month,
            'form':        form,
            'formset':     formset,
            'conflict':    None,
            'management':  management,
            'can_print_sheet': bool(management and management.is_upper_limit_manager),
        })

    def post(self, request, beneficiary_pk, year, month):
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        year_month  = f'{year}-{month:02d}'

        management, _ = CopaymentManagement.objects.get_or_create(
            facility    = facility,
            beneficiary = beneficiary,
            year_month  = year_month,
            defaults    = {'is_upper_limit_manager': True, 'management_result': '2'},
        )

        conflict = check_conflict(request, management)
        form    = CopaymentManagementForm(request.POST, instance=management)
        FormSet = self._get_formset_class()
        formset = FormSet(request.POST, instance=management)

        if conflict:
            messages.error(request, conflict)
        elif form.is_valid() and formset.is_valid():
            form.save()
            records = formset.save()
            # 「当施設」の行を1つだけ立てる（事業所番号か名前が当施設と一致する行、なければ先頭行）
            self._mark_this_office(management, facility)
            messages.success(
                request,
                f'{beneficiary.full_name}（{month}月）の上限額管理を保存しました。',
            )
            return redirect('billing:copayment_list_month', year=year, month=month)

        return render(request, 'billing/copayment_edit.html', {
            'beneficiary': beneficiary,
            'year':        year,
            'month':       month,
            'year_month':  year_month,
            'form':        form,
            'formset':     formset,
            'conflict':    conflict,
            'management':  management,
        })


    @staticmethod
    def _mark_this_office(management, facility):
        rows = list(management.office_records.all())
        if not rows:
            return
        mine = next((r for r in rows if facility.office_number and r.office_number == facility.office_number), None) \
            or next((r for r in rows if r.office_name == facility.name), None) \
            or next((r for r in rows if r.is_this_office), None) \
            or rows[0]
        for r in rows:
            flag = r.pk == mine.pk
            if r.is_this_office != flag:
                r.is_this_office = flag
                r.save(update_fields=['is_this_office'])


class CopaymentSheetView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    利用者負担上限額管理結果票（他事業所への送付用）を PDF で出す。
    当施設が上限管理事業所のときだけ。送付状（他事業所ごと）を先頭に付ける。
    ?fmt=html で画面表示、?office=<pk> で送付先を1事業所に絞る。
    """

    def get(self, request, beneficiary_pk, year, month):
        from config.pdf import pdf_or_html
        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)
        year, month = month_or_404(year, month)
        management  = get_object_or_404(CopaymentManagement, facility=facility, beneficiary=beneficiary,
                                        year_month=f'{year}-{month:02d}')
        if not management.is_upper_limit_manager:
            messages.error(request, '当施設が上限額管理事業所のときだけ管理結果票を出せます。')
            return redirect('billing:copayment_edit', beneficiary_pk=beneficiary_pk, year=year, month=month)

        records = list(management.office_records.all())
        others  = [r for r in records if not r.is_this_office]
        office_pk = to_int(request.GET.get('office'))
        if office_pk is not None:
            others = [r for r in others if r.pk == office_pk]
        # 送付先の連絡先（利用者の利用事業所に登録があれば）
        offices = {o.office_number: o for o in beneficiary.offices.filter(is_this_office=False) if o.office_number}
        offices_by_name = {o.name: o for o in beneficiary.offices.filter(is_this_office=False)}
        letters = []
        for r in others:
            o = offices.get(r.office_number) or offices_by_name.get(r.office_name)
            letters.append({'record': r, 'office': o})

        cert = beneficiary.recipient_certificates.order_by('-valid_until').first()
        ctx = {
            'facility':    facility,
            'beneficiary': beneficiary,
            'management':  management,
            'records':     records,
            'letters':     letters,
            'year':        year,
            'month':       month,
            'cert':        cert,
            'total_cost':  sum(r.total_cost for r in records),
            'total_original': sum(r.original_copayment for r in records),
            'total_adjusted': sum(r.adjusted_copayment for r in records),
            'issued_date': date.today(),
            'back_url':    reverse('billing:copayment_edit', args=[beneficiary_pk, year, month]),
        }
        return pdf_or_html(request, 'billing/pdf/copayment_sheet.html', ctx,
                           f'上限額管理結果票_{beneficiary.full_name}_{year}{month:02d}')


class BillingCsvView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    月次請求集計 CSV ダウンロード（国保連請求作成・請求ソフト入力の参照資料）。
    利用者ごとの利用日数・欠席日数・加算件数などをまとめて出力する。
    Excel で開けるよう UTF-8 BOM 付き（utf-8-sig）で出力する。
    """

    def get(self, request, year, month):
        import urllib.parse
        from beneficiaries.models import RecipientCertificate

        today = date.today()
        facility = request.user.facility
        year, month = month_or_404(year, month)
        month_start = date(year, month, 1)

        # 個別加算マスタ（CSV列ヘッダーの順番を固定するためリスト化）
        individual_addons = list(
            AddonMaster.objects.filter(is_active=True, addon_type='individual').order_by('pk')
        )

        # 当月の BillingMatrixEntry から利用状態別カウントを集計
        # {beneficiary_id: {'attended': N, 'absent': N, 'transferred': N}}
        entries = BillingMatrixEntry.objects.filter(
            facility=facility,
            date__year=year,
            date__month=month,
        ).prefetch_related('addons__addon')

        status_count = {}
        addon_count  = {}
        for entry in entries:
            bid = entry.beneficiary_id
            if bid not in status_count:
                status_count[bid] = {'attended': 0, 'absent': 0, 'transferred': 0}
            if entry.status in status_count[bid]:
                status_count[bid][entry.status] += 1

            if bid not in addon_count:
                addon_count[bid] = {}
            for ba in entry.addons.filter(is_applied=True):
                aid = ba.addon_id
                addon_count[bid][aid] = addon_count[bid].get(aid, 0) + 1

        # 当月有効な受給者証を利用者IDごとに取得: {beneficiary_id: RecipientCertificate}
        certs = RecipientCertificate.objects.filter(
            beneficiary__facility=facility,
            valid_from__lte=month_start,
            valid_until__gte=month_start,
        ).order_by('beneficiary_id', '-valid_until')
        cert_map = {}
        for c in certs:
            if c.beneficiary_id not in cert_map:
                cert_map[c.beneficiary_id] = c

        # 在籍中の利用者一覧（50音順）
        beneficiaries = Beneficiary.objects.filter(
            facility=facility,
            status='active',
        ).order_by('last_name_kana', 'first_name_kana')

        # CSV 出力（BOM付きUTF-8）
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)

        # 1行目：施設名・対象月・出力日
        writer.writerow([
            f'施設名：{facility.name}',
            f'対象月：{year}年{month}月',
            f'出力日：{today}',
        ])

        # 2行目：列ヘッダー
        headers = [
            '利用者名', 'ふりがな', '受給者証番号', '上限月額（円）',
            '利用日数', '欠席日数', '振替日数',
        ]
        headers += [f'{a.name}（件）' for a in individual_addons]
        headers += ['加算合計単位数（参考）']
        writer.writerow(headers)

        # データ行（利用者ごとに1行）
        for b in beneficiaries:
            cert = cert_map.get(b.pk)
            sc   = status_count.get(b.pk, {})
            ac   = addon_count.get(b.pk, {})

            addon_counts = [ac.get(a.pk, 0) for a in individual_addons]
            total_units  = sum(
                a.unit_count * ac.get(a.pk, 0)
                for a in individual_addons
                if a.unit_count and ac.get(a.pk, 0)
            )

            writer.writerow([
                b.full_name,
                b.full_name_kana,
                cert.certificate_number if cert else '',
                cert.monthly_cap if cert else '',
                sc.get('attended', 0),
                sc.get('absent', 0),
                sc.get('transferred', 0),
            ] + addon_counts + [total_units or ''])

        # BOM は先頭に1つだけ（行ごとに付かないよう、まとめて encode する）
        response = HttpResponse(buf.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        ascii_filename = f'billing_{year}{month:02d}.csv'
        utf8_filename  = urllib.parse.quote(f'請求集計_{year}年{month:02d}月.csv')
        response['Content-Disposition'] = (
            f'attachment; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{utf8_filename}"
        )
        return response


def _build_invoice_context(facility, beneficiary, year, month):
    """
    請求書・領収書に共通して必要なデータを収集してdictで返す。
    利用日数は BillingMatrixEntry（確定済み来所）から集計する。
    実費負担は ActivityTag の price フィールドを持つタグを DailyRecord から集計する。
    """
    from beneficiaries.models import RecipientCertificate
    from records.models import ActivityTag, DailyRecord

    month_start = date(year, month, 1)

    # 利用日数（BillingMatrixEntry で status='attended' のもの）
    attended_days = BillingMatrixEntry.objects.filter(
        facility=facility,
        beneficiary=beneficiary,
        date__year=year,
        date__month=month,
        status='attended',
    ).count()

    # 当月有効な受給者証（最新）
    cert = (
        RecipientCertificate.objects
        .filter(
            beneficiary=beneficiary,
            valid_from__lte=month_start,
            valid_until__gte=month_start,
        )
        .order_by('-valid_until')
        .first()
    )
    monthly_cap  = cert.monthly_cap if cert else None
    cert_number  = cert.certificate_number if cert else ''

    # 公費自己負担額の計算（重身の利用者は重身用の基本報酬単位数）
    base_unit_count = facility.base_units_for(beneficiary)
    unit_count_available = base_unit_count is not None
    if unit_count_available and monthly_cap is not None:
        base_units = Decimal(str(base_unit_count))
        # 令和8年6月以降新規指定事業所は 982/1000 の減算
        if facility.is_new_facility_r8:
            base_units = (base_units * Decimal('982') / Decimal('1000')).to_integral_value()
        unit_price = REGION_UNIT_PRICE.get(facility.region_category, Decimal('10.00'))
        total_cost = int(base_units * unit_price * attended_days)
        # 利用者負担額 = min(総費用×10%、上限月額)
        user_burden = min(int(Decimal(str(total_cost)) * Decimal('0.1')), monthly_cap)
    elif monthly_cap is not None:
        total_cost  = None
        user_burden = monthly_cap  # 単位数未設定のため上限月額を概算として使用
    else:
        total_cost  = None
        user_burden = None

    # 実費負担の集計（price > 0 の ActivityTag を DailyRecord から集計）
    expense_tags = ActivityTag.objects.filter(
        facility=facility,
        is_active=True,
        price__gt=0,
    ).order_by('display_order', 'name')

    # 支援内容タグにも単価を設定できる（教材費など）
    expense_support_tags = SupportContentTag.objects.filter(
        facility=facility,
        is_active=True,
        price__gt=0,
    ).order_by('order', 'name')

    expense_items = []
    expense_total = 0
    for tag, field in [(t, 'activity_tags') for t in expense_tags] + [(t, 'support_tags') for t in expense_support_tags]:
        count = DailyRecord.objects.filter(
            facility=facility,
            beneficiary=beneficiary,
            date__year=year,
            date__month=month,
            **{field: tag},
        ).count()
        if count > 0:
            subtotal = tag.price * count
            expense_items.append({
                'name':     tag.name,
                'price':    tag.price,
                'count':    count,
                'subtotal': subtotal,
            })
            expense_total += subtotal

    grand_total = (user_burden or 0) + expense_total

    return {
        'facility':           facility,
        'beneficiary':        beneficiary,
        'year':               year,
        'month':              month,
        'cert_number':        cert_number,
        'monthly_cap':        monthly_cap,
        'attended_days':      attended_days,
        'unit_count_available': unit_count_available,
        'total_cost':         total_cost,
        'user_burden':        user_burden,
        'expense_items':      expense_items,
        'expense_total':      expense_total,
        'grand_total':        grand_total,
        'issued_date':        date.today(),
    }


class InvoiceListView(LoginRequiredMixin, BillingEnabledMixin, View):
    """
    請求書・領収書 一覧（月次）。
    在籍中の利用者ごとに請求書・領収書PDFのダウンロードリンクを表示する。
    """

    def get(self, request, year=None, month=None):
        today = date.today()
        if year is None:
            year = today.year
        if month is None:
            month = today.month
        year, month = month_or_404(year, month)

        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        facility = request.user.facility

        beneficiaries = Beneficiary.objects.filter(
            facility=facility,
            status='active',
        ).order_by('last_name_kana', 'first_name_kana')

        # 利用日数を一括取得
        from django.db.models import Count as DjCount
        attended_map = {}
        entries = (
            BillingMatrixEntry.objects
            .filter(facility=facility, date__year=year, date__month=month, status='attended')
            .values('beneficiary_id')
            .annotate(count=DjCount('pk'))
        )
        for e in entries:
            attended_map[e['beneficiary_id']] = e['count']

        rows = []
        for b in beneficiaries:
            rows.append({
                'beneficiary':   b,
                'attended_days': attended_map.get(b.pk, 0),
            })

        return render(request, 'billing/invoice_list.html', {
            'year':       year,
            'month':      month,
            'prev_year':  prev_year,
            'prev_month': prev_month,
            'next_year':  next_year,
            'next_month': next_month,
            'rows':       rows,
            'today':      today,
        })


class InvoicePdfView(LoginRequiredMixin, BillingEnabledMixin, View):
    """請求書 PDF ダウンロード（WeasyPrint）。"""

    def get(self, request, beneficiary_pk, year, month):
        try:
            from weasyprint import HTML  # noqa: F401  （使えるかの確認）
            from config.pdf import render_pdf
        except ImportError:
            return HttpResponse('WeasyPrintがインストールされていません。', status=500)

        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)

        ctx = _build_invoice_context(facility, beneficiary, year, month)
        ctx['doc_type'] = 'invoice'

        html_string = render(request, 'billing/invoice_pdf.html', ctx).content.decode('utf-8')
        pdf_bytes   = render_pdf(html_string)

        ascii_filename = f'invoice_{beneficiary_pk}_{year}{month:02d}.pdf'
        utf8_filename  = urllib.parse.quote(
            f'請求書_{beneficiary.full_name}_{year}年{month:02d}月.pdf'
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{utf8_filename}"
        )
        return response


class ReceiptPdfView(LoginRequiredMixin, BillingEnabledMixin, View):
    """領収書 PDF ダウンロード（WeasyPrint）。"""

    def get(self, request, beneficiary_pk, year, month):
        try:
            from weasyprint import HTML  # noqa: F401  （使えるかの確認）
            from config.pdf import render_pdf
        except ImportError:
            return HttpResponse('WeasyPrintがインストールされていません。', status=500)

        facility    = request.user.facility
        beneficiary = get_object_or_404(Beneficiary, pk=beneficiary_pk, facility=facility)

        ctx = _build_invoice_context(facility, beneficiary, year, month)
        ctx['doc_type'] = 'receipt'

        html_string = render(request, 'billing/invoice_pdf.html', ctx).content.decode('utf-8')
        pdf_bytes   = render_pdf(html_string)

        ascii_filename = f'receipt_{beneficiary_pk}_{year}{month:02d}.pdf'
        utf8_filename  = urllib.parse.quote(
            f'領収書_{beneficiary.full_name}_{year}年{month:02d}月.pdf'
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{ascii_filename}"; '
            f"filename*=UTF-8''{utf8_filename}"
        )
        return response
