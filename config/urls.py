from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from django.views.static import serve


def healthz(request):
    """コンテナ／ロードバランサーのヘルスチェック用（認証なし・DBに触らない）"""
    return HttpResponse('ok', content_type='text/plain')


urlpatterns = [
    path('healthz/', healthz, name='healthz'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', include('facilities.urls')),
    path('users/', include('beneficiaries.urls')),
    path('schedules/', include('schedules.urls')),
    path('records/', include('records.urls')),
    path('plans/', include('support_plans.urls')),
    path('billing/', include('billing.urls')),
    path('reports/', include('reports.urls')),
    path('esignatures/', include('esignatures.urls')),
    path('line/', include('line_integration.urls')),
    path('ai/', include('ai_assist.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# 前段のプロキシが /media/ を配信しない構成（外部 nginx の共用サーバーなど）では Django が配信する
if settings.SERVE_MEDIA and not settings.DEBUG:
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
