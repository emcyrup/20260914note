"""回答ページ（ログインなし）。アドレスには署名が入っているので、1 字でも違えば開けない"""
from django.urls import path

from . import public_views

app_name = 'surveys_public'

urlpatterns = [
    path('<str:token>/', public_views.AnswerView.as_view(), name='answer'),
]
