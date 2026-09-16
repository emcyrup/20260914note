import datetime

from django.conf import settings
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View

from config.utils import home_url, safe_next, to_int

from .middleware import SESSION_KEY as DEV_FACILITY_SESSION_KEY
from .models import StaffAccount, StaffInvitation
from facilities.context_processors import get_terms
from facilities.models import Facility


class StaffLoginView(LoginView):
    """
    職員ログインページ。
    Django標準のLoginViewをカスタマイズしてテンプレートを差し替える。
    """
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True  # ログイン済みならトップページへ

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['signup_enabled'] = settings.ALLOW_FACILITY_SIGNUP
        return ctx


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
            'prefs': request.user.prefs,
            'font_choices': StaffAccount.FONT_CHOICES, 'mode_choices': StaffAccount.MODE_CHOICES,
            'scale_choices': StaffAccount.SCALE_CHOICES, 'bg_presets': StaffAccount.BG_PRESETS,
            'font_stacks_json': __import__('json').dumps(StaffAccount.FONT_STACKS),
        })

    def _save_prefs(self, request):
        import re
        u = request.user
        font = request.POST.get('font', 'biz')
        mode = request.POST.get('mode', 'light')
        bg = (request.POST.get('bg_custom') or request.POST.get('bg') or '').strip().lower()
        if request.POST.get('bg_reset'):
            bg = ''
        try:
            scale = int(request.POST.get('scale', 100))
        except ValueError:
            scale = 100
        if font not in StaffAccount.FONT_STACKS or mode not in dict(StaffAccount.MODE_CHOICES) \
                or scale not in dict(StaffAccount.SCALE_CHOICES) or (bg and not re.fullmatch(r'#[0-9a-f]{6}', bg)):
            messages.error(request, '設定の値が正しくありません。')
            return redirect('accounts:theme')
        u.ui_prefs = {'font': font, 'mode': mode, 'scale': scale, 'bg': bg}
        u.save(update_fields=['ui_prefs'])
        messages.success(request, '表示の設定を保存しました。')
        return redirect('accounts:theme')

    def post(self, request):
        if request.POST.get('form') == 'prefs':
            return self._save_prefs(request)
        theme = request.POST.get('theme', '')
        if theme not in dict(StaffAccount.THEME_CHOICES):
            messages.error(request, '画面の種類が正しくありません。')
            return redirect('accounts:theme')
        staff_id = request.POST.get('staff_id')
        target = request.user
        if staff_id and str(staff_id) != str(request.user.pk):
            if not (request.user.is_admin or request.user.is_superuser):
                messages.error(request, f'他の{get_terms(request.user)["staff"]}の画面を変えられるのは管理者だけです。')
                return redirect('accounts:theme')
            target = get_object_or_404(StaffAccount, pk=staff_id, facility=request.user.facility)
        target.ui_theme = theme
        target.save(update_fields=['ui_theme'])
        label = target.get_ui_theme_display()
        if target == request.user:
            messages.success(request, f'画面を「{label}」に切り替えました。')
            if theme == StaffAccount.THEME_SIMPLE:
                return redirect('records:simple_home')
            return redirect(home_url())
        messages.success(request, f'{target} さんの画面を「{label}」にしました。')
        return redirect('accounts:theme')


class SwitchFacilityView(LoginRequiredMixin, View):
    """
    開発向けユーザーの事業所切り替え。選んだ事業所IDをセッションに入れ、
    以降の画面はその事業所の職員として動く（DBの所属は変えない）。
    """

    def post(self, request):
        if not request.user.can_switch_facility:
            messages.error(request, '事業所の切り替えは開発向けユーザーだけが使えます。')
            return redirect(home_url())
        fid = request.POST.get('facility')
        if fid == 'home' or not fid:
            request.session.pop(DEV_FACILITY_SESSION_KEY, None)
            messages.success(request, '自分の所属事業所に戻しました。')
        else:
            facility = Facility.objects.filter(pk=to_int(fid, -1)).first()
            if facility is None:
                messages.error(request, 'その事業所は存在しません。')
                return redirect(home_url())
            request.session[DEV_FACILITY_SESSION_KEY] = facility.pk
            messages.success(request, f'事業所を「{facility.name}」に切り替えました。')
        return redirect(safe_next(request, request.POST.get('next'), home_url()))


class AdminOnlyMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_admin or request.user.is_superuser):
            messages.error(request, 'この画面は管理者だけが使えます。')
            return redirect(home_url())
        return super().dispatch(request, *args, **kwargs)


class StaffListView(AdminOnlyMixin, View):
    """職員・運用管理：職員の一覧・追加"""
    template_name = 'accounts/staff.html'

    def get(self, request):
        from .forms import InvitationForm, StaffCreateForm
        staff = StaffAccount.objects.filter(facility=request.user.facility).order_by('-is_active', 'role', 'username')
        invitations = StaffInvitation.objects.filter(facility=request.user.facility).select_related('created_by')[:30]
        return render(request, self.template_name, {
            'staff': staff, 'form': StaffCreateForm(), 'roles': StaffAccount.ROLE_CHOICES,
            'themes': StaffAccount.THEME_CHOICES,
            'invitations': invitations, 'invitation_form': InvitationForm(),
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


# =============================================
# 自己登録：招待リンク（職員）／新しい事業所の登録
# =============================================
class InvitationCreateView(AdminOnlyMixin, View):
    """招待リンクを発行する（管理者）"""

    def post(self, request):
        from .forms import InvitationForm
        form = InvitationForm(request.POST)
        if not form.is_valid():
            messages.error(request, '招待リンクの入力内容を確認してください：' + '；'.join(f'{v[0]}' for v in form.errors.values()))
            return redirect('accounts:staff')
        inv = form.save(commit=False)
        inv.facility = request.user.facility
        inv.created_by = request.user
        inv.expires_at = timezone.now() + datetime.timedelta(days=form.cleaned_data['expires_days'])
        inv.save()
        messages.success(request, f'招待リンクを発行しました（{inv.get_role_display()}・{inv.max_uses}回・'
                                  f'{timezone.localtime(inv.expires_at):%-m/%-d %H:%M} まで）。URL をコピーして本人に渡してください。')
        return redirect('accounts:staff')


class InvitationRevokeView(AdminOnlyMixin, View):
    """招待リンクを取り消す（管理者）"""

    def post(self, request, pk):
        inv = get_object_or_404(StaffInvitation, pk=pk, facility=request.user.facility)
        inv.is_active = False
        inv.save(update_fields=['is_active'])
        messages.success(request, '招待リンクを取り消しました。')
        return redirect('accounts:staff')


class JoinView(View):
    """招待リンクから、本人が自分のアカウントを作る（ログイン不要）"""
    template_name = 'accounts/join.html'

    def _invitation(self, token):
        inv = StaffInvitation.objects.filter(token=token).select_related('facility').first()
        if inv is None:
            raise Http404
        return inv

    def get(self, request, token):
        from .forms import JoinForm
        inv = self._invitation(token)
        return render(request, self.template_name, {'inv': inv, 'form': JoinForm(), 'usable': inv.is_usable})

    def post(self, request, token):
        from .forms import JoinForm
        inv = self._invitation(token)
        if not inv.is_usable:
            return render(request, self.template_name, {'inv': inv, 'form': JoinForm(), 'usable': False})
        form = JoinForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'inv': inv, 'form': form, 'usable': True})
        with transaction.atomic():
            # 同時利用で回数を超えないよう、行ロックしてから数える
            inv = StaffInvitation.objects.select_for_update().get(pk=inv.pk)
            if not inv.is_usable:
                return render(request, self.template_name, {'inv': inv, 'form': JoinForm(), 'usable': False})
            user = StaffAccount.objects.create_user(
                username=form.cleaned_data['username'], password=form.cleaned_data['password1'],
                facility=inv.facility, role=inv.role, display_name=form.cleaned_data['display_name'],
            )
            StaffInvitation.objects.filter(pk=inv.pk).update(used_count=F('used_count') + 1)
        login(request, user)
        messages.success(request, f'{user.display_name} さんのアカウントを作りました。「{inv.facility.name}」の{inv.get_role_display()}としてログインしています。')
        return redirect(home_url())


class SignupView(View):
    """新しい事業所として登録する（ALLOW_FACILITY_SIGNUP=True のときだけ。ログイン不要）"""
    template_name = 'accounts/signup.html'

    def dispatch(self, request, *args, **kwargs):
        if not settings.ALLOW_FACILITY_SIGNUP:
            raise Http404
        if request.user.is_authenticated:
            messages.info(request, 'ログイン中は新しい事業所を登録できません。いったんログアウトしてください。')
            return redirect(home_url())
        return super().dispatch(request, *args, **kwargs)

    def _form(self, data=None):
        from .forms import FacilitySignupForm
        return FacilitySignupForm(data, require_code=bool(settings.SIGNUP_CODE))

    def get(self, request):
        return render(request, self.template_name, {'form': self._form()})

    def post(self, request):
        from facilities.services import create_facility
        form = self._form(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        d = form.cleaned_data
        with transaction.atomic():
            facility = create_facility(d['facility_name'], d.get('office_number') or '')
            user = StaffAccount.objects.create_user(
                username=d['username'], password=d['password1'], facility=facility,
                role=StaffAccount.ROLE_ADMIN, display_name=d['display_name'],
            )
        login(request, user)
        messages.success(request, f'事業所「{facility.name}」を登録し、管理者アカウントを作りました。'
                                  '施設設定で呼び方や使う機能を確認し、「職員・運用管理」の招待リンクで職員を招待してください。')
        return redirect(home_url() if django_settings.RESERVATION_ONLY else 'facilities:settings')
