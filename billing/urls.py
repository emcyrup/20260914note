from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    # 請求マトリックス（当月 / 指定月）
    path('matrix/',
         views.BillingMatrixView.as_view(), name='matrix'),
    path('matrix/<int:year>/<int:month>/',
         views.BillingMatrixView.as_view(), name='matrix_month'),

    # セルポップアップ（HTMX GET）
    path('cell/<int:beneficiary_pk>/<int:year>/<int:month>/<int:day>/',
         views.CellPopupView.as_view(), name='cell_popup'),

    # セル保存（POST → リダイレクト）
    path('cell/<int:beneficiary_pk>/<int:year>/<int:month>/<int:day>/update/',
         views.CellUpdateView.as_view(), name='cell_update'),

    # 予定を一括読み込み
    path('matrix/<int:year>/<int:month>/load-from-schedule/',
         views.LoadFromScheduleView.as_view(), name='load_from_schedule'),

    # 利用者負担上限額管理
    path('copayment/',
         views.CopaymentListView.as_view(), name='copayment_list'),
    path('copayment/<int:year>/<int:month>/',
         views.CopaymentListView.as_view(), name='copayment_list_month'),
    path('copayment/<int:beneficiary_pk>/<int:year>/<int:month>/edit/',
         views.CopaymentEditView.as_view(), name='copayment_edit'),
    # 上限額管理結果票（送付状つき PDF）
    path('copayment/<int:beneficiary_pk>/<int:year>/<int:month>/sheet/',
         views.CopaymentSheetView.as_view(), name='copayment_sheet'),

    # 月次請求集計 CSV ダウンロード
    path('csv/<int:year>/<int:month>/billing/',
         views.BillingCsvView.as_view(), name='billing_csv'),

    # 請求書・領収書 PDF
    path('invoice/',
         views.InvoiceListView.as_view(), name='invoice_list'),
    path('invoice/<int:year>/<int:month>/',
         views.InvoiceListView.as_view(), name='invoice_list_month'),
    path('invoice/<int:beneficiary_pk>/<int:year>/<int:month>/pdf/',
         views.InvoicePdfView.as_view(), name='invoice_pdf'),
    path('receipt/<int:beneficiary_pk>/<int:year>/<int:month>/pdf/',
         views.ReceiptPdfView.as_view(), name='receipt_pdf'),
]
