import datetime
import secrets

from django.conf import settings
from django.conf import settings as django_settings
from django.core.cache import cache
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db import transaction
from django.db.models import F
from django.db.models import Q as models_q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View

from config.utils import home_url, safe_next, to_int

from .middleware import SESSION_KEY as DEV_FACILITY_SESSION_KEY
from . import security
from .models import LoginEvent, SecurityAlert, StaffAccount, StaffInvitation
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
        ctx['signup_open'] = Facility.objects.filter(staff_signup_open=True).exists()
        return ctx

    def post(self, request, *args, **kwargs):
        # 失敗が続いたユーザー名・IP は、しばらくログインを受け付けない（accounts/security.py）。正しいパスワードでも
        from . import security
        username = (request.POST.get('username') or '').strip()
        until = security.locked_until(username, security.client_ip(request))
        if until is not None:
            security.record(LoginEvent.KIND_LOCK, request, username)
            form = self.get_form()
            return self.render_to_response(self.get_context_data(form=form, locked_until=timezone.localtime(until)))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        # 承認待ちの人には、パスワード違いではなく承認待ちであることを伝える
        u = StaffAccount.objects.filter(username__iexact=(form.data.get('username') or '').strip(), signup_pending=True).first()
        pending = u is not None and u.check_password(form.data.get('password') or '')
        return self.render_to_response(self.get_context_data(form=form, signup_pending=pending))


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
            'fg_presets': StaffAccount.FG_PRESETS,
            'font_stacks_json': __import__('json').dumps(StaffAccount.FONT_STACKS),
        })

    def _save_prefs(self, request):
        import re
        u = request.user
        font = request.POST.get('font', 'biz')
        mode = request.POST.get('mode', 'light')
        bg = (request.POST.get('bg_custom') or request.POST.get('bg') or '').strip().lower()
        fg = (request.POST.get('fg_custom') or request.POST.get('fg') or '').strip().lower()
        if request.POST.get('bg_reset'):
            bg = fg = ''
        try:
            scale = int(request.POST.get('scale', 100))
        except ValueError:
            scale = 100
        if font not in StaffAccount.FONT_STACKS or mode not in dict(StaffAccount.MODE_CHOICES) \
                or scale not in dict(StaffAccount.SCALE_CHOICES) or (bg and not re.fullmatch(r'#[0-9a-f]{6}', bg)) \
                or (fg and not re.fullmatch(r'#[0-9a-f]{6}', fg)):
            messages.error(request, '設定の値が正しくありません。')
            return redirect('accounts:theme')
        u.ui_prefs = {'font': font, 'mode': mode, 'scale': scale, 'bg': bg, 'fg': fg}
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


def terms_staff(request):
    return get_terms(request.user.facility)['staff']


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
        staff = (StaffAccount.objects.filter(facility=request.user.facility, signup_pending=False)
                 .order_by('-is_active', 'role', 'username'))
        invitations = StaffInvitation.objects.filter(facility=request.user.facility).select_related('created_by')[:30]
        return render(request, self.template_name, {
            'staff': staff, 'form': StaffCreateForm(), 'roles': StaffAccount.ROLE_CHOICES,
            'themes': StaffAccount.THEME_CHOICES,
            'invitations': invitations, 'invitation_form': InvitationForm(),
            'pending': StaffAccount.objects.filter(facility=request.user.facility, signup_pending=True).order_by('date_joined'),
            'register_url': request.build_absolute_uri(reverse('accounts:register')),
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
        security.admin_action(request, f'{terms_staff(request)}「{u.display_name or u.username}」（{u.get_role_display()}）を追加しました')
        messages.success(request, f'{u.display_name or u.username} さんを追加しました（ログインID: {u.username}）。')
        return redirect('accounts:staff')


class LoginLogView(AdminOnlyMixin, View):
    """ログインの記録（管理者）：この事業所の職員のログイン・失敗・ロック。開発向けユーザーは全事業所と、事業所に結びつかない失敗も"""
    template_name = 'accounts/login_log.html'
    LIMIT = 300

    def get(self, request):
        from . import security
        security.cleanup()
        facility = request.user.facility
        qs = LoginEvent.objects.select_related('user', 'facility')
        if request.user.can_switch_facility:
            scope = 'all'
        else:
            scope = 'facility'
            names = list(StaffAccount.objects.filter(facility=facility).values_list('username', flat=True))
            qs = qs.filter(models_q(facility=facility) | models_q(facility__isnull=True, username__in=names))
        kind = request.GET.get('kind') or ''
        if kind in dict(LoginEvent.KIND_CHOICES):
            qs = qs.filter(kind=kind)
        events = list(qs[:self.LIMIT])
        since = timezone.now() - datetime.timedelta(days=7)
        week = LoginEvent.objects.filter(created_at__gte=since)
        if scope == 'facility':
            week = week.filter(models_q(facility=facility) | models_q(facility__isnull=True, username__in=names))
        summary = {
            'fail': week.filter(kind=LoginEvent.KIND_FAIL).count(),
            'lock': week.filter(kind=LoginEvent.KIND_LOCK).count(),
            'ok': week.filter(kind=LoginEvent.KIND_OK).count(),
            'ips': week.filter(kind=LoginEvent.KIND_FAIL).values('ip').distinct().count(),
        }
        locked = sorted({e.username for e in events if e.kind == LoginEvent.KIND_FAIL and e.created_at >= timezone.now() - datetime.timedelta(minutes=security.WINDOW_MINUTES)
                         and security.locked_until(e.username, '') is not None})
        alerts = SecurityAlert.objects.select_related('actor', 'facility')
        if scope == 'facility':
            alerts = alerts.filter(facility=facility)
        return render(request, self.template_name, {
            'alerts': list(alerts[:30]), 'notify_on': facility.security_notify,
            'events': events, 'kind': kind, 'kinds': LoginEvent.KIND_CHOICES, 'summary': summary, 'scope': scope,
            'locked': locked, 'limit': self.LIMIT, 'policy': {'window': security.WINDOW_MINUTES, 'user': security.USER_TRIES,
                                                               'ip': security.IP_TRIES, 'lock': security.LOCK_MINUTES, 'keep': security.KEEP_DAYS},
        })


class SecurityTestView(AdminOnlyMixin, View):
    """不正アクセスの通知のテスト送信（管理者）：LINE グループとメールに届くかを確かめる"""

    def post(self, request):
        facility = request.user.facility
        who = request.user.display_name or request.user.username
        row = security.alert(facility, SecurityAlert.KIND_TEST, f'{who} さんがテスト送信しました（IP {security.client_ip(request) or "不明"}）。'
                                                                 'この文が届いていれば、不正アクセスの通知はこの宛先に来ます。', actor=request.user)
        if not facility.security_notify:
            messages.warning(request, '施設設定で「不正アクセスの通知を送る」がオフなので、記録だけ残しました。')
        else:
            parts = []
            parts.append('LINE グループ：' + ('送りました' if row.line_sent else '送っていません（予約の「増減を知らせる LINE グループ」とチャネルトークンの設定が要ります）'))
            parts.append('メール：' + ('送りました' if row.mail_sent else '送っていません（施設設定の「不正アクセスの通知」に送信用の Gmail とアプリ パスワードを入れるか、宛先（管理者・児発管のメールか通知のメール宛先）を入れてください）'))
            if row.error:
                parts.append(f'エラー：{row.error}')
            (messages.success if (row.line_sent or row.mail_sent) and not row.error else messages.warning)(request, 'テスト送信：' + '／'.join(parts))
        return redirect('accounts:login_log')


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
        before = StaffAccount.objects.get(pk=u.pk)
        u = form.save()
        pw = form.cleaned_data.get('new_password')
        if pw:
            u.set_password(pw)
            u.save(update_fields=['password'])
            note = '（パスワードを再設定しました）'
        else:
            note = ''
        changes = []
        if before.role != u.role:
            changes.append(f'権限を{before.get_role_display()}→{u.get_role_display()}')
        if before.is_active != u.is_active:
            changes.append('有効にしました' if u.is_active else '無効にしました')
        if pw:
            changes.append('パスワードを再設定')
        if changes:
            security.admin_action(request, f'{terms_staff(request)}「{u.display_name or u.username}」の' + '・'.join(changes))
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
        security.admin_action(request, f'招待リンクを発行しました（{inv.get_role_display()}・{inv.max_uses} 回）')
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


# =============================================
# ログイン画面からの職員登録（職員登録コード＋管理者の承認）
# =============================================
CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # 見間違えやすい 0/O・1/I は使わない
CODE_LENGTH = 8
REGISTER_TRIES = 10                          # 同じ端末から1時間に申し込める回数（コードの当てずっぽうを防ぐ）


def new_signup_code():
    while True:
        code = ''.join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))
        if not Facility.objects.filter(staff_signup_code=code).exists():
            return code


def _client_ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', '') or 'unknown')


class StaffRegisterView(View):
    """ログイン画面の「職員として新しく登録」。作ったアカウントは管理者が承認するまで使えない"""
    template_name = 'accounts/register.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            messages.info(request, 'ログイン中です。新しい職員の登録は、いったんログアウトしてから行ってください。')
            return redirect(home_url())
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from .forms import StaffRegisterForm
        form = StaffRegisterForm()
        return render(request, self.template_name, {'form': form, 'open_any': bool(form.open_facilities)})

    def post(self, request):
        from .forms import StaffRegisterForm
        key = f'staff-register:{_client_ip(request)}'
        tries = cache.get(key, 0)
        if tries >= REGISTER_TRIES:
            return render(request, self.template_name, {'form': StaffRegisterForm(), 'blocked': True})
        cache.set(key, tries + 1, 3600)
        form = StaffRegisterForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form, 'open_any': bool(form.open_facilities)})
        d = form.cleaned_data
        user = StaffAccount.objects.create_user(
            username=d['username'], password=d['password1'], facility=form.facility,
            role=StaffAccount.ROLE_STAFF, display_name=d['display_name'], is_active=False, signup_pending=True,
        )
        return render(request, self.template_name, {'done': True, 'new_user': user, 'facility': form.facility})


class SignupCodeView(AdminOnlyMixin, View):
    """ログイン画面からの職員登録の受け付け方（管理者）：コードを発行し直す／コードなしで受け付ける／止める"""

    def post(self, request):
        facility = request.user.facility
        action = request.POST.get('action')
        if action == 'off':
            facility.staff_signup_code = ''
            facility.staff_signup_open = False
            messages.success(request, 'ログイン画面からの職員登録を止めました。')
        elif action == 'open':
            facility.staff_signup_open = True
            messages.success(request, 'ログイン画面からの職員登録を、職員登録コードなしで受け付けるようにしました。'
                                      '申し込んだ人は、この画面で承認するまでログインできません。')
        elif action == 'require_code':
            facility.staff_signup_open = False
            if not facility.staff_signup_code:
                facility.staff_signup_code = new_signup_code()
            messages.success(request, f'職員登録コードが要るようにしました：{facility.staff_signup_code}')
        else:
            facility.staff_signup_code = new_signup_code()
            messages.success(request, f'職員登録コードを発行しました：{facility.staff_signup_code}（前のコードは使えなくなりました）。')
        facility.save(update_fields=['staff_signup_code', 'staff_signup_open'])
        return redirect(f"{reverse('accounts:staff')}#signup")


class StaffApproveView(AdminOnlyMixin, View):
    """ログイン画面から登録した人を承認する（権限区分を決めて有効にする）／断る（アカウントを消す）"""

    def post(self, request, pk):
        u = get_object_or_404(StaffAccount, pk=pk, facility=request.user.facility, signup_pending=True)
        name = u.display_name or u.username
        if request.POST.get('action') == 'reject':
            u.delete()
            messages.success(request, f'{name} さんの登録を断りました（アカウントを消しました）。')
            return redirect(f"{reverse('accounts:staff')}#signup")
        role = request.POST.get('role')
        if role not in dict(StaffAccount.ROLE_CHOICES):
            role = StaffAccount.ROLE_STAFF
        u.role, u.is_active, u.signup_pending = role, True, False
        u.save(update_fields=['role', 'is_active', 'signup_pending'])
        security.admin_action(request, f'{terms_staff(request)}「{name}」を{u.get_role_display()}として承認しました')
        messages.success(request, f'{name} さんを{u.get_role_display()}として承認しました。ログインID「{u.username}」でログインできます。')
        return redirect('accounts:staff')
