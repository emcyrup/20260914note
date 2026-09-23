"""
画面から AI に1回たずねるときの共通部品（留意点の要約・療育記録への追記・議事録の整理）。
"""
import logging
import re

import anthropic
from django.conf import settings
from django.http import JsonResponse

from .text import clean_ai_text, effort_kwargs

logger = logging.getLogger(__name__)


def ask_ai(system, content, max_tokens):
    """AI に1回たずねて (返答の文, None) を返す。使えないときは (None, エラーの JsonResponse)"""
    if not settings.ANTHROPIC_API_KEY:
        return None, JsonResponse({'error': 'AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。管理者に設定を依頼してください。'},
                                  status=500)
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.AI_TEXT_MODEL, max_tokens=max_tokens, **effort_kwargs(settings.AI_TEXT_MODEL),
            system=system, messages=[{'role': 'user', 'content': content}],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception('AI の呼び出しでエラー')
        return None, JsonResponse({'error': f'AIでの作成中にエラーが発生しました: {type(e).__name__}'}, status=500)
    return ''.join(b.text for b in response.content if b.type == 'text'), None


def tidy_sections(raw):
    """
    「詳しくまとめる」の返答を整える：見出しは【】、項目は「・」にそろえ、見出しの前に空行を1つ入れる。
    マークダウンの見出し（# 見出し）や太字（**語**）が混じっても【見出し】に直す。
    """
    lines, out = clean_ai_text(raw).splitlines(), []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        md = re.match(r'^#{1,6}\s*(.+)$', line)
        if md:
            line = f'【{md.group(1).strip("【】 ")}】'
        elif re.match(r'^[-*•●○◦・]\s*', line):
            line = '・' + re.sub(r'^[-*•●○◦・]\s*', '', line)
        if line.startswith('【') and line.endswith('】') and out:
            out.append('')
        out.append(line)
    return '\n'.join(out)
