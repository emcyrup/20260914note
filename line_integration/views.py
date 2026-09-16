"""
LINE連携のビュー定義
- LineWebhookView  : LINEからのWebhook（フォロー・テキスト）を受信して保護者と紐づける
- SendLineMessageView : 保護者向けメッセージをLINE Push Messageで配信する
- DeliveryLogView  : LINE配信履歴一覧
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from beneficiaries.models import Beneficiary, Guardian
from config.utils import home_url, reservation_enabled
from facilities.models import Facility
from records.models import DailyRecord

from .models import LineDeliveryLog

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class LineWebhookView(View):
    """
    LINE Webhookエンドポイント。
    保護者がLINE公式アカウントをフォローした際・テキストを送った際に呼ばれる。

    - FollowEvent   : 登録案内メッセージを返す
    - TextMessage   : お子様のお名前を送ってもらい、保護者とLINE User IDを紐づける
    """

    LINK_FAIL_LIMIT = 5          # この回数を超えて間違えた LINE ユーザーは
    LINK_FAIL_WINDOW = 60 * 60   # この秒数の間、照合しない
    RECEIVED_REPLY = ('承りました。事業所で内容を確認のうえ、あらためてご連絡します。\n'
                      'この返信は自動でお送りしています。')

    @staticmethod
    def _inbox(facility, event, text, source_type, line_user_id):
        """受信箱に積む（自動では反映しない。職員が画面で確かめてから反映する）"""
        if not reservation_enabled(facility):
            return None
        from reservations.services import receive
        source = getattr(event.source, 'type', 'user')
        group_id = getattr(event.source, 'group_id', '') or getattr(event.source, 'room_id', '') or ''
        entry, _ = receive(
            facility, message_id=getattr(event.message, 'id', '') or f'{event.timestamp}-{line_user_id}',
            text=text, source_type=source_type, line_user_id=line_user_id, group_id=group_id,
        )
        return entry

    @staticmethod
    def _auto_reply_for_customer(facility, raw_text, line_user_id, base=''):
        """
        公式LINEに届いた文を、設定が許すときだけその場で予約に反映する。
        反映できたら返事の文、できなければ None（受信箱に積んで職員が確かめる）。
        """
        if not reservation_enabled(facility) or not line_user_id:
            return None
        from reservations.models import Customer
        from reservations.services import apply_message, get_setting
        setting = get_setting(facility)
        if not setting.line_auto_apply:
            return None
        customer = Customer.objects.filter(facility=facility, line_user_id=line_user_id).first()
        if customer is None:
            return None   # 顧客台帳にない方は、職員が確かめてから
        handled, text = apply_message(facility, raw_text, customer=customer, setting=setting, base=base)
        return text if handled else None

    @staticmethod
    def _page_reply(facility, raw_text, line_user_id, base=''):
        """
        「予約」などの問い合わせに、顧客向け予定表のアドレスを返す。
        戻り値は (返事の文, 受信箱に積むか)。案内を出せないときは ('', True)。
        """
        if not reservation_enabled(facility):
            return '', True
        from reservations.models import Customer
        from reservations.services import get_setting, page_reply
        customer = (Customer.objects.filter(facility=facility, line_user_id=line_user_id).first()
                    if line_user_id else None)
        return page_reply(facility, raw_text, customer=customer, setting=get_setting(facility), base=base)

    @staticmethod
    def _auto_reply_for_group(facility, raw_text, group_id):
        """
        スタッフのグループの投稿を予約に反映する。
        お知らせ先として登録ずみのグループからの投稿だけを見る（ほかのグループは受信箱へ）。
        """
        if not reservation_enabled(facility) or not group_id:
            return None
        from reservations.services import apply_message, get_setting
        setting = get_setting(facility)
        if not setting.group_auto_apply or setting.notify_group_id != group_id:
            return None
        handled, text = apply_message(facility, raw_text, staff=True, setting=setting)
        return text if handled else None

    @staticmethod
    def resolve_facility(facility_pk):
        """URL の事業所ID から事業所を決める。旧 URL（ID なし）は LINE を設定した事業所が1つだけのときに限り許す"""
        if facility_pk is not None:
            return Facility.objects.filter(pk=facility_pk).exclude(line_channel_secret='').first()
        configured = list(Facility.objects.exclude(line_channel_secret='')[:2])
        return configured[0] if len(configured) == 1 else None

    def post(self, request, facility_pk=None):
        facility = self.resolve_facility(facility_pk)
        if facility is None:
            logger.error('LINE Webhook: 事業所を特定できないか、チャネルシークレットが未設定（facility_pk=%s）', facility_pk)
            return HttpResponse(status=200)  # LINEには常に200を返す（500だとLINEが無限リトライする）

        from linebot.v3 import WebhookHandler
        from linebot.v3.exceptions import InvalidSignatureError
        from linebot.v3.messaging import (
            ApiClient, Configuration, MessagingApi,
            ReplyMessageRequest, TextMessage as LineTextMessage,
        )
        from linebot.v3.webhooks import FollowEvent, MessageEvent, TextMessageContent

        handler = WebhookHandler(facility.line_channel_secret)
        signature = request.META.get('HTTP_X_LINE_SIGNATURE', '')
        body = request.body.decode('utf-8')

        config = Configuration(access_token=facility.line_channel_access_token)

        @handler.add(FollowEvent)
        def handle_follow(event):
            """フォロー時：登録案内メッセージを返す"""
            with ApiClient(config) as api_client:
                api = MessagingApi(api_client)
                api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineTextMessage(
                        type='text',
                        text=(
                            '友だち追加ありがとうございます。\n\n'
                            '施設からお知らせした登録コード（8文字）を送信してください。\n'
                            '例：ABCD2345\n\n'
                            '登録コードは施設スタッフにお問い合わせください。'
                        )
                    )]
                ))

        @handler.add(MessageEvent, message=TextMessageContent)
        def handle_text(event):
            """
            テキストメッセージ受信：登録コード（8文字・72時間有効・1回限り）で保護者と自動紐づけ。
            コード方式を採用することで同姓同名・表記ゆれの問題を根本解決する。
            すでに連携済みの場合はスキップする。間違いが続いた LINE ユーザーは一定時間照合しない。
            """
            source_type = getattr(event.source, 'type', 'user')
            line_user_id = getattr(event.source, 'user_id', '') or ''
            group_id = getattr(event.source, 'group_id', '') or getattr(event.source, 'room_id', '') or ''
            raw_text = event.message.text
            text = raw_text.strip().upper().replace(' ', '')

            base = request.build_absolute_uri('/')

            def reply(reply_text):
                with ApiClient(config) as api_client:
                    MessagingApi(api_client).reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[LineTextMessage(type='text', text=reply_text)],
                    ))

            # スタッフのグループ・複数人トークからの投稿
            # お知らせ先に登録ずみのグループなら、書かれたとおりに予約を入れる／取り消す。
            # 読み取れない投稿と、ほかのグループからの投稿は受信箱に積むだけ（自動では反映しない）。
            if source_type in ('group', 'room'):
                staff_reply = self._auto_reply_for_group(facility, raw_text, group_id)
                if staff_reply:
                    reply(staff_reply)
                    return
                self._inbox(facility, event, raw_text, source_type, line_user_id)
                return

            # 連携済みなら、登録コードの照合はしない（予約管理を使う事業所では受信箱に積む）
            if Guardian.objects.filter(line_user_id=line_user_id, line_linked=True).exists():
                if reservation_enabled(facility):
                    auto = self._auto_reply_for_customer(facility, raw_text, line_user_id, base)
                    if auto:
                        reply(auto)
                        return
                    guide, to_inbox = self._page_reply(facility, raw_text, line_user_id, base)
                    if to_inbox or not guide:
                        self._inbox(facility, event, raw_text, source_type, line_user_id)
                    reply(f'{self.RECEIVED_REPLY}\n\n{guide}' if guide and to_inbox
                          else (guide or self.RECEIVED_REPLY))
                return

            with ApiClient(config) as api_client:
                api = MessagingApi(api_client)

                fail_key = f'line_link_fail:{line_user_id}'
                fails = cache.get(fail_key, 0)
                if fails >= self.LINK_FAIL_LIMIT:
                    reply_text = '登録コードの間違いが続いたため、しばらく受け付けられません。1時間ほどしてからもう一度お試しください。'
                    api.reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[LineTextMessage(type='text', text=reply_text)]
                    ))
                    return

                # この事業所の、未連携で期限内の登録コードだけを照合する
                guardian = Guardian.objects.filter(
                    beneficiary__facility=facility,
                    line_registration_code=text,
                    line_linked=False,
                    line_code_expires_at__gt=timezone.now(),
                ).select_related('beneficiary').first()

                if guardian:
                    guardian.mark_line_linked(line_user_id)
                    guardian.save()
                    cache.delete(fail_key)
                    reply_text = (
                        f'{guardian.beneficiary.full_name}様の保護者として登録しました。\n'
                        'これからお子様の様子をお届けします。'
                    )
                elif reservation_enabled(facility):
                    # 予約管理を使う事業所では、登録コード以外の文は
                    # （顧客台帳にいて設定が許すときは）その場で反映し、そうでなければ受信箱へ積む。
                    # 「予約」などの問い合わせには、顧客向け予定表のアドレスを返す
                    auto = self._auto_reply_for_customer(facility, raw_text, line_user_id, base)
                    if auto:
                        api.reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[LineTextMessage(type='text', text=auto)],
                        ))
                        return
                    guide, to_inbox = self._page_reply(facility, raw_text, line_user_id, base)
                    if to_inbox or not guide:
                        self._inbox(facility, event, raw_text, source_type, line_user_id)
                    reply_text = (f'{self.RECEIVED_REPLY}\n\n{guide}' if guide and to_inbox
                                  else (guide or self.RECEIVED_REPLY))
                else:
                    cache.set(fail_key, fails + 1, self.LINK_FAIL_WINDOW)
                    reply_text = (
                        '登録コードが見つからないか、期限が切れています。\n'
                        '施設からお知らせした8文字の登録コードをそのまま送信してください。'
                        '期限（発行から72時間）が過ぎている場合は、施設で再発行してもらってください。'
                    )

                api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[LineTextMessage(type='text', text=reply_text)]
                ))

        try:
            handler.handle(body, signature)
        except InvalidSignatureError:
            logger.warning('LINE Webhook: 署名が不正')
            return HttpResponse(status=400)

        return HttpResponse(status=200)

    def get(self, request, facility_pk=None):
        return HttpResponse(status=405)



class LineEnabledMixin:
    """施設設定で LINE 連携を使わない場合はホームへ戻す"""

    def dispatch(self, request, *args, **kwargs):
        facility = getattr(request.user, 'facility', None)
        if request.user.is_authenticated and facility is not None and not facility.use_line:
            messages.info(request, 'LINE連携はこの事業所では使わない設定です（施設設定 → 使う機能 で変更できます）。')
            return redirect(home_url())
        return super().dispatch(request, *args, **kwargs)


class SendLineMessageView(LoginRequiredMixin, LineEnabledMixin, View):
    """
    保護者向けメッセージをLINE Push Messageで送信する。
    日次記録の parent_message_draft を送信し、LineDeliveryLog に結果を記録する。
    """

    def post(self, request, record_pk):
        from linebot.v3.messaging import (
            ApiClient, Configuration, MessagingApi,
            PushMessageRequest, TextMessage as LineTextMessage,
            ImageMessage as LineImageMessage,
        )

        facility = request.user.facility
        record = get_object_or_404(DailyRecord, pk=record_pk, facility=facility)

        if not record.parent_message_draft:
            messages.error(request, 'メッセージがありません。先に保護者向けメッセージを入力してください。')
            return redirect('records:list', beneficiary_pk=record.beneficiary_id)

        # LINE連携済みの保護者を取得（主保護者優先）
        guardian = (
            record.beneficiary.guardians.filter(line_linked=True, is_primary=True).first()
            or record.beneficiary.guardians.filter(line_linked=True).first()
        )

        if not guardian:
            messages.error(request, f'{record.beneficiary.full_name}様の保護者がLINE未連携です。')
            return redirect('records:list', beneficiary_pk=record.beneficiary_id)

        if not facility.line_channel_access_token:
            messages.error(request, 'LINEチャネルアクセストークンが設定されていません。施設設定で確認してください。')
            return redirect('records:list', beneficiary_pk=record.beneficiary_id)

        # LINE Push Message を送信
        is_success = False
        error_message = ''
        config = Configuration(access_token=facility.line_channel_access_token)

        # 挨拶と締めの文言を追加して送信
        send_text = (
            f'{record.beneficiary.full_name}さんの本日の様子をご報告します。\n\n'
            f'{record.parent_message_draft}\n\n'
            f'次回もお待ちしております。'
        )

        # LINE の画像 URL は HTTPS の公開 URL が必要。/media/ はログイン必須なので、
        # 有効期限つきの署名 URL（既定 30 分。LINE が取得してキャッシュするまでの間だけ有効）を渡す
        # 1回のプッシュで最大5件のため、テキストと写真を別々に送信することで最大5枚全て送れる
        from facilities.media import signed_media_url
        photos = list(record.photos.all())
        photo_messages = [
            LineImageMessage(
                type='image',
                original_content_url=request.build_absolute_uri(signed_media_url(photo.photo.name)),
                preview_image_url=request.build_absolute_uri(signed_media_url(photo.photo.name)),
            )
            for photo in photos
        ]

        try:
            with ApiClient(config) as api_client:
                api = MessagingApi(api_client)
                # 1回目：テキストメッセージ
                api.push_message(PushMessageRequest(
                    to=guardian.line_user_id,
                    messages=[LineTextMessage(type='text', text=send_text)],
                ))
                # 2回目：写真（あれば）。1回のプッシュで最大5件まで送れる
                if photo_messages:
                    api.push_message(PushMessageRequest(
                        to=guardian.line_user_id,
                        messages=photo_messages,
                    ))
            is_success = True
        except Exception as e:
            error_message = str(e)
            logger.error(f'LINE送信エラー (record_pk={record_pk}): {e}')

        # 配信ログを保存（成功・失敗ともに記録）
        LineDeliveryLog.objects.create(
            facility=facility,
            guardian=guardian,
            daily_record=record,
            content=send_text,
            is_success=is_success,
            error_message=error_message,
        )

        if is_success:
            messages.success(request, f'{guardian}へLINEメッセージを送信しました。')
        else:
            messages.error(request, f'LINE送信に失敗しました：{error_message}')

        return redirect('records:list', beneficiary_pk=record.beneficiary_id)


class DeliveryLogView(LoginRequiredMixin, LineEnabledMixin, View):
    """
    LINE配信履歴一覧。施設の全配信ログを新しい順に表示する。
    """

    def get(self, request):
        facility = request.user.facility
        logs = LineDeliveryLog.objects.filter(
            facility=facility,
        ).select_related('guardian', 'guardian__beneficiary', 'daily_record')

        return render(request, 'line_integration/delivery_log.html', {
            'logs': logs,
        })
