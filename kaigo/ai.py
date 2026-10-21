"""
居宅介護支援の記録の AI（2026-10-20 の打ち合わせ）：
- summarize   ：話した言葉・メモを、支援経過に残す文に指定の文字数（200〜1,000 字）でまとめる
- read_image  ：紙の書類・メモの写真（PDF も）から、書いてある文字をそのまま起こす
- meeting     ：会議のメモ・話した言葉から、担当者会議の要点（検討した項目・検討内容・結論・残された課題）の下書きを作る
文体は「地方の現場のケアマネが自分で書く、自然で飾らない言葉」。上品すぎる・歯が浮くような言い回しは使わない。
事業所ごとの文体の希望（KaigoSetting.style_note。例：もう少し締める）を毎回そえる。数字や事実は AI に作らせない。
"""
import base64
import json

from ai_assist.quick import ask_ai
from ai_assist.text import clean_ai_text

STYLE_RULES = """文体のきまり（とても大事）
- 地方の居宅介護支援事業所のケアマネジャーが、自分で書いたような自然で飾らない言葉にする
- 上品すぎる・かしこまりすぎる・歯が浮くような言い回しは使わない
  （使わない例：「〜されておられる」「穏やかな笑顔で」「寄り添い」「〜が見受けられた」「〜と存じます」「温かい」「丁寧に傾聴」）
- 敬語は最小限。文末は「〜した」「〜だった」「〜とのこと」「〜を確認」などの短い形
- 話に無いことは書かない。ケアマネの評価・助言・一般論・感想を足さない
- 事実・数字・日付・回数・薬や病名・事業所名・人の名前は、元のとおりに残す。本人・家族の言葉の「」の中は話したとおり
- 見出し・箇条書き・記号・マークダウン・絵文字・英語は使わない。内容の区切りで改行する"""

KIND_HINTS = {
    'monitoring': 'モニタリングの記録。元にあるものだけを、本人の様子・体調 → サービスの利用状況 → 本人・家族の意向 → 今後のこと の順に書く。',
    'meeting': 'サービス担当者会議の記録。誰が集まり、何を話し、何が決まったかを書く。',
    'hospital': '入院・退院の記録。日付・病院・状態・退院後の見通しを落とさない。',
    'medical': '医療機関との連携の記録。相手・伝えたこと・聞いたことを落とさない。',
}


def _style(setting):
    note = (setting.style_note or '').strip() if setting else ''
    return STYLE_RULES + (f'\n\n事業所からの文体の希望（これを優先する）\n{note[:600]}' if note else '')


def summarize(text, chars, kind_key='', kind_label='', setting=None):
    """(まとめた文, None) か (None, エラーの JsonResponse)"""
    system = f"""あなたは居宅介護支援事業所のケアマネジャーの記録を手伝う AI です。
ケアマネが話した言葉・メモを、居宅介護支援経過（第5表）に残す文にまとめます。

{_style(setting)}

まとめ方
- 長さは {chars} 文字くらいをめやすにする（元が短ければ短いままでよい。水増ししない。大きく超えない）
- 大事な事実を落とさない。同じことのくり返し・言いよどみ・関係のない雑談は省く
- 返すのはまとめた文だけ（前置き・説明は書かない）"""
    head = f'【記録の種別】{kind_label}\n' if kind_label else ''
    hint = KIND_HINTS.get(kind_key, '')
    raw, error = ask_ai(system, f'{head}{hint}\n【話した言葉・メモ】\n{text}', max(1200, chars * 3))
    if error is not None:
        return None, error
    return clean_ai_text(raw), None


READ_PROMPT = """これは居宅介護支援事業所の仕事で使う紙の書類・メモ（手書きを含む）の写真です。
書いてある文字を、そのまま書き起こしてください。
- 読めた文字だけを、書いてある順に。要約・言いかえ・補足・説明はしない
- 読めない文字は「〓」にする。推測で埋めない
- 表は1行ずつ「項目：内容」の形で書く。チェック欄は印の付いたものだけ「☑項目」と書く
- 返すのは書き起こした文字だけ"""


def image_block(uploaded):
    """アップロードされた写真（JPEG・PNG など）か PDF → AI に渡す形。読めないときは ValueError"""
    name = (uploaded.name or '').lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'pdf':
        data = uploaded.read()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError('PDF が大きすぎます（20MB まで）。')
        return {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf',
                                               'data': base64.standard_b64encode(data).decode('ascii')}}
    if ext == 'heic':
        raise ValueError('HEIC（iPhone の写真）は読めません。カメラの設定で「互換性優先」にするか、この画面の「カメラで撮る」から撮ってください。')
    from records.paper import image_payload
    try:
        payload = image_payload(uploaded, max_side=2000)
    except Exception:  # noqa: BLE001 - 壊れた画像など
        raise ValueError('写真を開けませんでした。撮り直してください。')
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': payload}}


def read_image(uploaded):
    """(書き起こした文字, None) か (None, エラーの JsonResponse)。写真が読めないときは ValueError"""
    block = image_block(uploaded)
    raw, error = ask_ai(READ_PROMPT, [block, {'type': 'text', 'text': '書いてある文字を書き起こしてください。'}], 4000)
    if error is not None:
        return None, error
    return clean_ai_text(raw), None


MEETING_FIELDS = ('topics', 'discussion', 'conclusion', 'remaining')


def meeting(text, setting=None):
    """会議のメモ → ({'topics', 'discussion', 'conclusion', 'remaining'}, None) か (None, エラー)"""
    system = f"""あなたは居宅介護支援事業所のケアマネジャーの記録を手伝う AI です。
サービス担当者会議のメモ・話した言葉から、「サービス担当者会議の要点」（第4表）の下書きを作ります。

{_style(setting)}

作り方
- 次の4つの欄に分けて、JSON だけを返す：{{"topics": "検討した項目", "discussion": "検討内容", "conclusion": "結論", "remaining": "残された課題（次回の開催時期）"}}
- 検討した項目は短く（1行に1つ。番号を付けてよい）。検討内容は出席者ごとの発言・確認したことを中心に
- メモに無い欄は空の文字列にする。決まっていないことを決まったように書かない"""
    raw, error = ask_ai(system, f'【会議のメモ・話した言葉】\n{text}', 3000)
    if error is not None:
        return None, error
    body = (raw or '').strip()
    if body.startswith('```'):
        body = body.strip('`')
        body = body[body.find('{'):]
    try:
        data = json.loads(body[body.find('{'):body.rfind('}') + 1])
    except (ValueError, TypeError):
        return {'topics': '', 'discussion': clean_ai_text(raw), 'conclusion': '', 'remaining': ''}, None
    return {k: clean_ai_text(str(data.get(k) or '')) for k in MEETING_FIELDS}, None
