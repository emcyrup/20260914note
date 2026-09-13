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


class AdminOnlyMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'この画面は管理者だけが使えます。')
            return redirect('facilities:dashboard')
        return super().dispatch(request, *args, **kwargs)


class StaffListView(AdminOnlyMixin, View):
    """職員・運用管理：職員の一覧・追加"""
    template_name = 'accounts/staff.html'

    def get(self, request):
        from .forms import StaffCreateForm
        staff = StaffAccount.objects.filter(facility=request.user.facility).order_by('-is_active', 'role', 'username')
        return render(request, self.template_name, {
            'staff': staff, 'form': StaffCreateForm(), 'roles': StaffAccount.ROLE_CHOICES,
            'themes': StaffAccount.THEME_CHOICES,
        })

    def post(self, request):
        from .forms import StaffCreateForm
        form = StaffCreateForm(request.POST)
        if not form.is_valid():
            staff = StaffAccount.objects.filter(facility=request.user.facility).order_by('-is_active', 'role', 'username')
            messages.error(request, '入力内容を確認してください。')
            return render(request, self.template_name, {
                'staff': staff, 'form': form, 'roles': StaffAccount.ROLE_CHOICES, 'themes': StaffAccount.THEME_CHOICES,
                'show_add': True,
            })
        u = form.save(commit=False)
        u.facility = request.user.facility
        u.set_password(form.cleaned_data['password'])
        u.save()
        messages.success(request, f'{u.display_name or u.username} さんを追加しました（ログインID: {u.username}）。')
        return redirect('accounts:staff')


class StaffUpdateView(AdminOnlyMixin, View):
    """職員の編集：表示名・権限・画面・有効／無効・パスワード再設定"""

    def post(self, request, pk):
        from .forms import StaffUpdateForm
        u = get_object_or_404(StaffAccount, pk=pk, facility=request.user.facility)
        form = StaffUpdateForm(request.POST, instance=u)
        if not form.is_valid():
            messages.error(request, '入力内容を確認してください：' + '；'.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
            return redirect('accounts:staff')
        if u == request.user and not form.cleaned_data['is_active']:
            messages.error(request, '自分自身を無効にはできません。')
            return redirect('accounts:staff')
        if u == request.user and form.cleaned_data['role'] != StaffAccount.ROLE_ADMIN and not request.user.is_superuser:
            messages.error(request, '自分の権限区分を管理者以外に変えることはできません。')
            return redirect('accounts:staff')
        u = form.save()
        pw = form.cleaned_data.get('new_password')
        if pw:
            u.set_password(pw)
            u.save(update_fields=['password'])
            note = '（パスワードを再設定しました）'
        else:
            note = ''
        messages.success(request, f'{u.display_name or u.username} さんの情報を保存しました{note}。')
        return redirect('accounts:staff')
