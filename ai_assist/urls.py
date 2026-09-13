from django.urls import path

from . import views

app_name = 'ai_assist'

urlpatterns = [
    path('chat/', views.ChatView.as_view(), name='chat'),
    path('suggestions/record/<int:record_pk>/refresh/', views.SuggestionRefreshView.as_view(), name='suggestion_refresh'),
    path('suggestions/<int:pk>/<str:action>/', views.SuggestionDecideView.as_view(), name='suggestion_decide'),
    path('documents/upload/', views.DocumentUploadView.as_view(), name='document_upload'),
    path('documents/<int:pk>/index/', views.DocumentIndexView.as_view(), name='document_index'),
    path('documents/<int:pk>/delete/', views.DocumentDeleteView.as_view(), name='document_delete'),
]
