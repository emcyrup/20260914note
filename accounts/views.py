from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from .models import StaffAccount


class StaffLoginView(LoginView):
    """
    職員ログインページ。
    Django標準のLoginViewをカスタマイズしてテンプレートを差し替える。
    """
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True  # ログイン済みならトップページへ


class StaffLogoutView(LogoutView):
    """
    ログアウト処理。ログアウト後はログインページへリダイレクト。
    """
    next_page = reverse_lazy('accounts:login')


class ThemeView(LoginRequiredMixin, View):
    """
    画面の着せ替え。自分の見た目はだれでも変えられ、
    管理者は職員ごとに割り当てられる（記録は共有のまま）。
    """
    template_name = 'accounts/theme.html'

    def get(self, request):
        themes = [
            {'key': key, 'label': label, **StaffAccount.THEME_INFO[key]}
            for key, label in StaffAccount.THEME_CHOICES
        ]
        staff = []
        if request.user.is_admin or request.user.is_superuser:
            staff = StaffAccount.objects.filter(facility=request.user.facility, is_active=True).order_by('username')
        return render(request, self.template_name, {
            'themes': themes, 'staff': staff, 'current': request.user.ui_theme,
        })

    def post(self, request):
        theme = request.POST.get('theme', '')
        if theme not in dict(StaffAccount.THEME_CHOICES):
            messages.error(request, '画面の種類が正しくありません。')
            return redirect('accounts:theme')
        staff_id = request.POST.get('staff_id')
        target = request.user
        if staff_id and str(staff_id) != str(request.user.pk):
            if not (request.user.is_admin or request.user.is_superuser):
                messages.error(request, '他の職員の画面を変えられるのは管理者だけです。')
                return redirect('accounts:theme')
            target = get_object_or_404(StaffAccount, pk=staff_id, facility=request.user.facility)
        target.ui_theme = theme
        target.save(update_fields=['ui_theme'])
        label = target.get_ui_theme_display()
        if target == request.user:
            messages.success(request, f'画面を「{label}」に切り替えました。')
            if theme == StaffAccount.THEME_SIMPLE:
                return redirect('records:simple_home')
            return redirect('facilities:dashboard')
        messages.success(request, f'{target} さんの画面を「{label}」にしました。')
        return redirect('accounts:theme')
