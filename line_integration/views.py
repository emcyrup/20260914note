"""
LINE連携のビュー定義
- LineWebhookView  : LINEからのWebhook（フォロー・テキスト）を受信して保護者と紐づける
- SendLineMessageView : 保護者向けメッセージをLINE Push Messageで配信する
- DeliveryLogView  : LINE配信履歴一覧
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from beneficiaries.models import Beneficiary, Guardian
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

    def post(self, request):
        facility = Facility.objects.first()
        if not facility or not facility.line_channel_secret:
            logger.error('LINE Webhook: 施設設定にチャネルシークレットが未設定')
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
                            '施設からお知らせした6桁の登録コードを送信してください。\n'
                            '例：123456\n\n'
                            '登録コードは施設スタッフにお問い合わせください。'
                        )
                    )]
                ))

        @handler.add(MessageEvent, message=TextMessageContent)
        def handle_text(event):
            """
            テキストメッセージ受信：6桁の登録コードで保護者と自動紐づけ。
            コード方式を採用することで同姓同名・表記ゆれの問題を根本解決する。
            すでに連携済みの場合はスキップする。
            """
            line_user_id = event.source.user_id
            text = event.message.text.strip()

            # 連携済みならスキップ
            if Guardian.objects.filter(line_user_id=line_user_id, line_linked=True).exists():
                return

            with ApiClient(config) as api_client:
                api = MessagingApi(api_client)

                # 登録コード（6桁数字）でGuardianを検索
                guardian = Guardian.objects.filter(
                    beneficiary__facility=facility,
                    line_registration_code=text,
                    line_linked=False,  # 未連携の保護者のみ対象
                ).select_related('beneficiary').first()

                if guardian:
                    guardian.line_user_id = line_user_id
                    guardian.line_linked = True
                    guardian.save()
                    reply_text = (
                        f'{guardian.beneficiary.full_name}様の保護者として登録しました。\n'
                        'これからお子様の様子をお届けします。'
                    )
                else:
                    reply_text = (
                        '登録コードが見つかりませんでした。\n'
                        '施設からお知らせした6桁の数字をそのまま送信してください。'
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

    def get(self, request):
        return HttpResponse(status=405)


class SendLineMessageView(LoginRequiredMixin, View):
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

        # LINEの画像URLはHTTPS公開URLである必要があるため、本番環境でのみ正常に動作する
        # 1回のプッシュで最大5件のため、テキストと写真を別々に送信することで最大5枚全て送れる
        photos = list(record.photos.all())
        photo_messages = [
            LineImageMessage(
                type='image',
                original_content_url=request.build_absolute_uri(photo.photo.url),
                preview_image_url=request.build_absolute_uri(photo.photo.url),
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


class DeliveryLogView(LoginRequiredMixin, View):
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
