"""
介護施設の記録の AI（2026-10-20 の打ち合わせ）：
- record_draft ：はい／いいえ・キーワード・音声メモ（母国語でもよい）→ 介護記録の本文（200〜300 字）と家族向けの文を下書き
- rewrite      ：外国人スタッフの入力（母国語・単語の並び・くだけた日本語）→ 記録・報告書に使える日本語。元の言語への訳も返して本人が確かめられる
- incident_draft：メモ → 事故報告書の 4 つの欄（状況・原因・対応・再発防止）
文体は「現場の介護職員が自分で書く、自然で飾らない言葉」。数字・名前・薬・時刻は AI に作らせない（材料に無いものは書かない）。
"""
import json

from ai_assist.quick import ask_ai
from ai_assist.text import clean_ai_text

from .models import CHECK_LABELS, LANG_LABELS

STYLE_RULES = """文体のきまり（とても大事）
- 介護施設の職員が自分で書いたような、自然で飾らない日本語にする。上品すぎる・歯が浮くような言い回しは使わない
  （使わない例：「穏やかな笑顔で」「寄り添い」「〜が見受けられた」「温かい雰囲気の中」「丁寧に傾聴」）
- 材料に無いことは書かない。体温・血圧・時刻・回数・薬の名前・人の名前・病名を、材料に無いのに作らない。評価・助言・一般論を足さない
- 見出し・箇条書き・記号・マークダウン・絵文字・英語は使わない"""


def _style(setting):
    note = (setting.style_note or '').strip() if setting else ''
    return STYLE_RULES + (f'\n\n事業所からの文体の希望（これを優先する）\n{note[:600]}' if note else '')


def _parse_json(raw):
    raw = (raw or '').strip()
    start, end = raw.find('{'), raw.rfind('}') + 1
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end], strict=False)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _lang_note(lang):
    if lang and lang != 'ja':
        return f'入力は{LANG_LABELS.get(lang, lang)}（またはその言葉と日本語の混ざったもの）で書かれていることがある。意味を取り違えないように日本語にする。'
    return ''


def material_text(record, vitals=None, insurance=None):
    """介護記録の下書きの材料（はい／いいえ・キーワード・メモ・その日の検温板・要介護度）"""
    lines = []
    if insurance is not None and getattr(insurance, 'care_level', ''):
        lines.append(f'要介護度：{insurance.care_level}')
    checks = [f'{CHECK_LABELS[k]}：{"はい" if a == "yes" else "いいえ"}' for k, _label, a in record.check_rows() if a in ('yes', 'no')]
    if checks:
        lines.append('【はい／いいえ】\n' + '\n'.join(checks))
    if record.keywords.strip():
        lines.append('【キーワード】\n' + record.keywords.strip())
    if record.memo.strip():
        lines.append('【メモ・話した言葉】\n' + record.memo.strip()[:6000])
    if vitals:
        v = []
        for log in vitals:
            parts = [f'{log.get_slot_display()}']
            if log.temp is not None:
                parts.append(f'体温 {log.temp}℃')
            if log.bp_text:
                parts.append(f'血圧 {log.bp_text}')
            if log.pulse is not None:
                parts.append(f'脈拍 {log.pulse}')
            if log.spo2 is not None:
                parts.append(f'SpO2 {log.spo2}%')
            if log.meal_text:
                parts.append(f'食事 主食/副食 {log.meal_text}割')
            if log.water_ml is not None:
                parts.append(f'水分 {log.water_ml}ml')
            if log.urine is not None or log.stool is not None:
                parts.append(f'排尿 {log.urine if log.urine is not None else "—"}回・排便 {log.stool if log.stool is not None else "—"}回')
            if log.bath:
                parts.append('入浴あり')
            if log.note:
                parts.append(f'特記：{log.note}')
            v.append('　'.join(parts))
        lines.append('【検温板（その日の実測）】\n' + '\n'.join(v))
    return '\n\n'.join(lines)


def record_draft(record, setting, vitals=None, insurance=None):
    """戻り値 (dict{'body','family_message','back'}, None) か (None, エラーの JsonResponse)"""
    chars = setting.record_chars if setting else 250
    fam = setting.family_chars if setting else 150
    lang = record.lang or 'ja'
    system = f"""あなたは介護施設（通所・入所）の介護職員の記録を手伝う AI です。
職員が入れた「はい／いいえ」「キーワード」「メモ・話した言葉」と、その日の検温板の実測から、次の 2 つの下書きを作ります。
1. 介護記録（内部の記録。事実を時系列・項目順に。{chars} 字くらい。文末は「〜した」「〜だった」「〜なし」などの短い常体）
2. 家族向けのメッセージ（連絡帳。その日の様子を家族が安心して読める言葉で。{fam} 字くらい。です・ます調。
   内部の注意（他の利用者とのトラブル・職員の反省・介護上の専門的な評価）は書かない。体調の変化・けが・連絡事項があれば必ず入れる）

{_style(setting)}
{_lang_note(lang)}
検温板の数値は実測なので、記録には材料にある値だけをそのまま書く。「はい／いいえ」で「いいえ」のものは、その事実（例：食事は半分ほど残した）が
メモに無ければ「〜はできなかった」と短く書く。キーワードはその場面の様子として自然な文にする。

必ず次の JSON だけを返す（文字列の中で改行するときは \\n）。
{{"body": "介護記録", "family_message": "家族向けのメッセージ"{', "back": "介護記録を' + LANG_LABELS.get(lang, lang) + 'に訳したもの（職員が内容を確かめるため）"' if lang != 'ja' else ''}}}"""
    content = material_text(record, vitals, insurance)
    if not content.strip():
        return None, None
    raw, error = ask_ai(system, content, 2500)
    if error is not None:
        return None, error
    data = _parse_json(raw)
    if data is None:
        return {'body': clean_ai_text(raw), 'family_message': '', 'back': ''}, None
    return {'body': clean_ai_text(str(data.get('body') or '')), 'family_message': clean_ai_text(str(data.get('family_message') or '')),
            'back': clean_ai_text(str(data.get('back') or ''))}, None


def rewrite(text, lang='ja', kind='record', setting=None):
    """
    入力の文（母国語・単語の並び・くだけた日本語）→ 記録や報告書にそのまま使える日本語。
    戻り値 (dict{'text','back'}, None) か (None, エラーの JsonResponse)。back は元の言語への訳（日本語の入力なら空）
    """
    purpose = {'record': '介護記録（内部の記録）の文。常体（〜した）', 'family': '家族向けの連絡帳の文。です・ます調',
               'incident': '事故報告書・ヒヤリハット報告書の文。常体で、事実を順に'}.get(kind, '介護の記録の文')
    system = f"""あなたは介護施設で働く外国人スタッフ・新人スタッフの文章を手伝う AI です。
入力された文を、{purpose}として自然で正式な日本語に書き直します。

{_style(setting)}
{_lang_note(lang)}
- 入力が単語の並びや短い文の列でも、意味を補わずに、つながりのある文にする。事実を足さない・削らない
- 入力に「？」や「〓」など分からないところがあれば、そのまま残す（推測で埋めない）
- 漢字・送りがな・助詞のまちがいを直す。専門用語は介護の現場で使う言い方（例：トイレ誘導・見守り・体位交換）にする

必ず次の JSON だけを返す（文字列の中で改行するときは \\n）。
{{"text": "書き直した日本語"{', "back": "書き直した文を' + LANG_LABELS.get(lang, lang) + 'に訳したもの（本人が内容を確かめるため）"' if lang != 'ja' else ''}}}"""
    raw, error = ask_ai(system, text[:8000], 2000)
    if error is not None:
        return None, error
    data = _parse_json(raw)
    if data is None:
        return {'text': clean_ai_text(raw), 'back': ''}, None
    return {'text': clean_ai_text(str(data.get('text') or '')), 'back': clean_ai_text(str(data.get('back') or ''))}, None


def incident_draft(memo, lang='ja', setting=None):
    """メモ → {'situation','cause','response','prevention','back'}"""
    system = f"""あなたは介護施設のヒヤリハット・事故報告書の作成を手伝う AI です。
職員のメモ・話した言葉から、報告書の 4 つの欄の下書きを作ります。
- 発生の状況：いつ・どこで・誰が・何をしていて・何が起きたか（時刻・場所はメモにあるものだけ）
- 原因・背景：メモから読み取れる原因。分からなければ「要確認」と書く
- 対応（処置・連絡）：行った処置・観察・連絡（誰に）
- 再発防止策：メモにあるものを中心に、現場で実際にできる具体策を 1〜3 つ。一般論は足さない

{_style(setting)}
{_lang_note(lang)}
常体（〜した）で、各欄 1〜4 文。
必ず次の JSON だけを返す（文字列の中で改行するときは \\n）。
{{"situation": "", "cause": "", "response": "", "prevention": ""{', "back": "4 つの欄を' + LANG_LABELS.get(lang, lang) + 'に訳したもの"' if lang != 'ja' else ''}}}"""
    raw, error = ask_ai(system, memo[:8000], 2500)
    if error is not None:
        return None, error
    data = _parse_json(raw) or {}
    out = {k: clean_ai_text(str(data.get(k) or '')) for k in ('situation', 'cause', 'response', 'prevention', 'back')}
    if not any(out[k] for k in ('situation', 'cause', 'response', 'prevention')):
        out['situation'] = clean_ai_text(raw)
    return out, None
