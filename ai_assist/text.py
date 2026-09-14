"""
AI が生成した日本語文章の品質を揃えるための共通部品

- JAPANESE_RULES : すべての文章生成プロンプトに付ける「丁寧な日本語」のルール
- clean_ai_text  : 返ってきた文章から、文字化けの原因になる要素を取り除く
- effort_kwargs  : モデルに応じて output_config（思考の深さ）を付ける
"""
import re
import unicodedata

JAPANESE_RULES = """【日本語の書き方（必ず守る）】
- 自然で丁寧な日本語（です・ます調）で書く。文章として読める形にし、箇条書きや見出しにしない
- 常用漢字とひらがな・カタカナだけを使う。中国語（簡体字・繁体字）、韓国語、英語の単語や文を混ぜない
- 半角カタカナ、機種依存文字、絵文字、顔文字、装飾記号（★ ■ → ※ など）、マークダウン（** # ` -）は使わない
- 人名・活動名などの固有名詞は、入力にある表記をそのまま使う。入力にない事実を作らない
- 句読点は「、」「。」を使い、文の途中で改行しない。1文は60字程度までにする"""

# 制御文字（改行・タブ以外）とゼロ幅文字
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ﻿�]')
_HALF_KANA_RE = re.compile(r'[｡-ﾟ]+')
_UNICODE_ESCAPE_RE = re.compile(r'\\u([0-9a-fA-F]{4})')
_MD_FENCE_RE = re.compile(r'^```[a-zA-Z]*\s*|\s*```$', re.M)
_LABEL_RE = re.compile(r'^(観察記録|観察・活動内容|活動内容・観察記録|支援内容|支援内容の記録|支援記録|本人の反応|本人の反応・変化|反応記録|'
                       r'保護者向けメッセージ|考察|めあて|回答|文章)\s*[:：]\s*')


def _unescape(text):
    """「\\u3042」「\\n」が文字のまま残っている場合に戻す"""
    if '\\u' in text:
        text = _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    if '\\n' in text:
        text = text.replace('\\r\\n', '\n').replace('\\n', '\n')
    return text


def clean_ai_text(text, keep_newlines=True):
    """AI の返答を記録に入れられる形に整える（内容は変えない）"""
    if not text:
        return ''
    text = str(text)
    text = _unescape(text)
    text = unicodedata.normalize('NFC', text)
    text = _CONTROL_RE.sub('', text)
    # 半角カタカナは全角に
    text = _HALF_KANA_RE.sub(lambda m: unicodedata.normalize('NFKC', m.group(0)), text)
    # コードフェンス・マークダウンの強調・先頭のラベルを除く
    text = _MD_FENCE_RE.sub('', text)
    text = text.replace('**', '').replace('__', '')
    text = text.strip()
    for _ in range(2):
        text = _LABEL_RE.sub('', text).strip()
        if len(text) >= 2 and text[0] in '「"“' and text[-1] in '」"”':
            text = text[1:-1].strip()
    # 空白の整理
    text = re.sub(r'[ \t　]+', lambda m: '　' if '　' in m.group(0) else ' ', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    if keep_newlines:
        text = re.sub(r'\n{3,}', '\n\n', text)
    else:
        text = re.sub(r'\s*\n\s*', '', text)
    return text.strip()


def clean_ai_dict(data, keys):
    """JSON で返った複数の文章をまとめて整える"""
    return {k: clean_ai_text(data.get(k, '')) for k in keys}


_EFFORT_MODELS = re.compile(r'^claude-(opus-(4-[5-9]|5)|sonnet-(4-[6-9]|5)|fable|mythos)')


def effort_kwargs(model, effort='low'):
    """短い文章の生成は思考を浅くして応答を速くする（対応モデルのみ）"""
    if _EFFORT_MODELS.match(model or ''):
        return {'output_config': {'effort': effort}}
    return {}
