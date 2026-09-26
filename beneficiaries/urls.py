from django.urls import path
from . import views

app_name = 'beneficiaries'

urlpatterns = [
    path('', views.BeneficiaryListView.as_view(), name='list'),
    path('new/', views.BeneficiaryCreateView.as_view(), name='create'),
    path('<int:pk>/', views.BeneficiaryDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.BeneficiaryUpdateView.as_view(), name='update'),
    path('<int:beneficiary_pk>/guardians/new/', views.GuardianCreateView.as_view(), name='guardian_create'),
    path('<int:beneficiary_pk>/guardians/<int:guardian_pk>/edit/', views.GuardianUpdateView.as_view(), name='guardian_update'),
    path('<int:beneficiary_pk>/certificates/new/', views.RecipientCertificateCreateView.as_view(), name='certificate_create'),
    path('<int:beneficiary_pk>/certificates/<int:cert_pk>/edit/', views.RecipientCertificateUpdateView.as_view(), name='certificate_update'),
    path('<int:beneficiary_pk>/certificates/ocr/', views.RecipientCertificateOcrView.as_view(), name='certificate_ocr'),
    path('<int:beneficiary_pk>/offices/new/', views.BeneficiaryOfficeCreateView.as_view(), name='office_create'),
    path('<int:beneficiary_pk>/offices/<int:office_pk>/edit/', views.BeneficiaryOfficeUpdateView.as_view(), name='office_update'),
    path('<int:beneficiary_pk>/offices/<int:office_pk>/delete/', views.BeneficiaryOfficeDeleteView.as_view(), name='office_delete'),
    path('<int:beneficiary_pk>/documents/upload/', views.DocumentUploadView.as_view(), name='document_upload'),
    path('<int:beneficiary_pk>/documents/<int:document_pk>/delete/', views.DocumentDeleteView.as_view(), name='document_delete'),
    path('<int:beneficiary_pk>/assessments/new/', views.AssessmentCreateView.as_view(), name='assessment_create'),
    path('<int:beneficiary_pk>/assessments/<int:assessment_pk>/edit/', views.AssessmentUpdateView.as_view(), name='assessment_update'),
    path('<int:beneficiary_pk>/assessments/<int:assessment_pk>/delete/', views.AssessmentDeleteView.as_view(), name='assessment_delete'),
    path('guardians/<int:guardian_pk>/regenerate-line-code/', views.RegenerateLineCodeView.as_view(), name='regenerate_line_code'),
]
