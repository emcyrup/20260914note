"""
手書きのフェイスシート・アセスメントシート（紙の用紙の PDF・写真）を AI で読み取り、用紙のデータ（sheets.py の形）にする。

- read_face(beneficiary, files)   : フェイスシート（基本情報・家族構成・緊急連絡先・18 項目・コミュニケーションの印）
- read_assess(beneficiary, files) : アセスメントシート（項目ごとの 3 段階の○と「本人の状態」）
どちらも 1 回の読み取りに PDF 1 つ、または写真を何枚か（用紙の 1〜3 枚目を別々に撮った写真）を渡せる。
読み取った内容は保存せず、入力画面に流し込んで職員が確かめてから保存する。
"""
import json
import logging

import anthropic
from django.conf import settings

from ai_assist.text import effort_kwargs

from . import sheets
from .knowledge import ReadError, _file_block, is_readable

logger = logging.getLogger(__name__)

MAX_FILES = 6
_DATE_RULE = '用紙の「記入日」（YYYY-MM-DD。令和 7 年は 2025 年。読めなければ空）'
_COMMON_RULES = """【守ること】
- 用紙に書いてあることだけを書き写す。推測で埋めない。読めない字は前後から分かる範囲で、分からなければ unreadable に書く
- 空欄の項目は空文字にする（「なし」「特になし」と書いてあればそのまま書き写す）
- 手書きの文は句読点を補ってよいが、言い換えない。常用漢字とひらがな・カタカナで書く。マークダウンや絵文字は使わない"""


def _face_schema():
    head = {k: {'type': 'string'} for k, _ in sheets.FACE_HEAD}
    items = {k: {'type': 'string'} for k, _, _ in sheets.FACE_ITEMS}
    row = lambda cols: {'type': 'object', 'properties': {c: {'type': 'string'} for c, _ in cols},  # noqa: E731
                        'required': [c for c, _ in cols], 'additionalProperties': False}
    return {
        'type': 'object',
        'properties': {
            'date': {'type': 'string'}, 'filled_by': {'type': 'string'},
            'head': {'type': 'object', 'properties': head, 'required': list(head), 'additionalProperties': False},
            'family': {'type': 'array', 'items': row(sheets.FAMILY_COLS)},
            'contacts': {'type': 'array', 'items': row(sheets.CONTACT_COLS)},
            'comm_means': {'type': 'array', 'items': {'type': 'string', 'enum': [v for v, _ in sheets.COMM_MEANS]}},
            'comm_understand': {'type': 'array', 'items': {'type': 'string', 'enum': [v for v, _ in sheets.COMM_UNDERSTAND]}},
            'items': {'type': 'object', 'properties': items, 'required': list(items), 'additionalProperties': False},
            'unreadable': {'type': 'string'},
        },
        'required': ['date', 'filled_by', 'head', 'family', 'contacts', 'comm_means', 'comm_understand', 'items', 'unreadable'],
        'additionalProperties': False,
    }


def _face_prompt():
    head = '\n'.join(f'  - {k}：{label}' for k, label in sheets.FACE_HEAD)
    items = '\n'.join(f'  - {k}：{label}' + (f'（{paren}）' if paren else '') for k, label, paren in sheets.FACE_ITEMS)
    means = '／'.join(f'{v}={label}' for v, label in sheets.COMM_MEANS)
    under = '／'.join(f'{v}={label}' for v, label in sheets.COMM_UNDERSTAND)
    return f"""あなたは児童発達支援センターの職員を手伝うAIです。
保護者や職員が手書きした「児童発達支援・保育所等訪問支援　フェイスシート」（A4 で 3 枚ほど。PDF か写真）を読み、決まった形（JSON）に書き写します。

【用紙の作り】
1 枚目：記入日・記入者、ふりがな・氏名・愛称・保護者氏名・住所・TEL と、所属・学級・生年月日・性別・血液型・身長体重の表、
家族構成の表（名前・続柄・生年月日（年齢）・職場・学年・備考）、緊急時の連絡確認（勤務先へ TEL してよいか／事業所名を名乗ってよいか）、
緊急時の連絡先の表（連絡順に 父・母・その他／自宅・携帯・勤務先・その他／TEL）、障がい診断・所有手帳・医療機関・平熱と発熱の目安・補助具・服薬・てんかん・アレルギー。
2〜3 枚目：項目ごとの枠（睡眠・食事・排泄・着脱衣・整容・コミュニケーション・行動の特徴・問題行動・身体運動・対人・外出・休日の過ごし方・環境・
本人の長所・本人の願い・伸ばしてあげたい個性や能力・家族の願い・利用している他事業所のサービス）。
コミュニケーションの枠には「意思伝達手段」「意思伝達理解」の ①〜⑤ の印（○や✓）がある。

【取り出すもの】
- date：{_DATE_RULE}。filled_by：記入者（無ければ空）
- head：次の key に、用紙の該当の欄の値（文字のまま）
{head}
  ※ 氏名・ふりがな・生年月日は台帳にあるので取り出さない。emergency_work_ok・emergency_name_ok は「はい」「いいえ」のどちらに印があるか
- family：家族構成の表の行（書いてある行だけ、上から順に。key は name・relation・birth・job・note）
- contacts：緊急時の連絡先の表の行（書いてある行だけ、連絡順。key は who・where・tel）
- comm_means：意思伝達手段で印の付いた番号（{means}）。comm_understand：意思伝達理解で印の付いた番号（{under}）。印が無ければ空の配列
- items：次の key に、項目の枠に書いてある文（複数行はそのまま改行で）
{items}
- unreadable：読めなかった欄・迷ったところ（無ければ空）

{_COMMON_RULES}"""


def _assess_schema():
    items = {k: {'type': 'object', 'properties': {'level': {'type': 'integer', 'enum': [0, 1, 2, 3]}, 'note': {'type': 'string'}},
                 'required': ['level', 'note'], 'additionalProperties': False} for k, _, _, _, _ in sheets.ASSESS_ITEMS}
    return {
        'type': 'object',
        'properties': {'date': {'type': 'string'}, 'filled_by': {'type': 'string'},
                       'items': {'type': 'object', 'properties': items, 'required': list(items), 'additionalProperties': False},
                       'unreadable': {'type': 'string'}},
        'required': ['date', 'filled_by', 'items', 'unreadable'],
        'additionalProperties': False,
    }


def _assess_prompt():
    lines = []
    for gkey, glabel, items in sheets.ASSESS_GROUPS:
        lines.append(f'  【{glabel}】')
        for k, label, levels in items:
            lines.append(f'  - {k}：{label}（1={levels[0]}／2={levels[1]}／3={levels[2]}）')
    return f"""あなたは児童発達支援センターの職員を手伝うAIです。
職員が手書きした「アセスメントシート」（A4 で 2 枚。1 枚目 対人関係、2 枚目 生活面・学習面。PDF か写真）を読み、決まった形（JSON）に書き写します。

【用紙の作り】
上に 利用者氏名・事業所名・記入日・記入者。表は 項目／状況／本人の状態 の 3 列。
「状況」には 3 段階の言い方が縦に並び、当てはまるものに ○（丸）や ✓ が付いている。「本人の状態」は手書きの文。

【取り出すもの】
- date：{_DATE_RULE}。filled_by：記入者（無ければ空）
- items：次の key ごとに level（○の付いた段階。1 が上の段、3 が下の段。○が無い・判断できないときは 0）と note（本人の状態の文。空欄なら空）
{chr(10).join(lines)}
- unreadable：○がどれに付いているか迷った項目・読めなかった字（無ければ空）

{_COMMON_RULES}"""


def _blocks(files):
    """アップロードされたファイル（PDF か写真。何枚か）を AI に渡す形に"""
    if not files:
        raise ReadError('用紙の PDF か写真を選んでください。')
    if len(files) > MAX_FILES:
        raise ReadError(f'一度に読めるのは {MAX_FILES} ファイルまでです。')
    blocks = []
    for f in files:
        if not is_readable(f.name):
            raise ReadError(f'「{f.name}」は読み取れません。PDF か写真（JPEG・PNG など）にしてください。')
        blocks.append(_file_block(f, f.name))
    if sum(1 for b in blocks if b['type'] == 'document') > 1:
        raise ReadError('PDF は 1 回に 1 つだけにしてください（写真なら何枚かまとめて読めます）。')
    return blocks


def _ask(system, schema, blocks, note):
    if not settings.ANTHROPIC_API_KEY:
        raise ReadError('AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。管理者に設定を依頼してください。')
    effort = effort_kwargs(settings.AI_PLAN_MODEL, 'medium')
    output_config = {**effort.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': schema}}
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        res = client.messages.create(
            model=settings.AI_PLAN_MODEL, max_tokens=12000, output_config=output_config, system=system,
            messages=[{'role': 'user', 'content': blocks + [{'type': 'text', 'text': note}]}],
        )
    except anthropic.APIStatusError as e:
        logger.warning('用紙の読み取りで API エラー: %s', e.status_code)
        raise ReadError(f'AI での読み取りに失敗しました（{e.status_code}）。少し待ってからもう一度お試しください。')
    except Exception as e:  # noqa: BLE001
        logger.exception('用紙の読み取りでエラー')
        raise ReadError(f'AI での読み取りに失敗しました（{type(e).__name__}）。もう一度お試しください。')
    if getattr(res, 'stop_reason', '') == 'refusal':
        raise ReadError('AI がこの用紙を読み取れませんでした。画面から直接入れてください。')
    if getattr(res, 'stop_reason', '') == 'max_tokens':
        raise ReadError('読み取りきれませんでした。用紙を分けて（1 枚ずつ）取り込んでください。')
    raw = ''.join(b.text for b in res.content if getattr(b, 'type', '') == 'text')
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise ReadError('AI の返答を読み取れませんでした。もう一度お試しください。')


def _note(beneficiary, files, kind):
    return (f'この{kind}は {beneficiary.full_name}さん（生年月日 {beneficiary.date_of_birth:%Y-%m-%d}）の用紙として取り込まれました。'
            f'ファイル：{"、".join(f.name for f in files)}（{len(files)} 件。写真が複数なら用紙の 1 枚目から順）')


def _s(v, limit=sheets.TEXT_MAX):
    return (v if isinstance(v, str) else '').strip()[:limit]


def read_face(beneficiary, files):
    """戻り値 dict：data（sheets.face_from_post と同じ形）・date（date か None）・filled_by・unreadable"""
    raw = _ask(_face_prompt(), _face_schema(), _blocks(files), _note(beneficiary, files, 'フェイスシート'))
    head_in, items_in = raw.get('head') or {}, raw.get('items') or {}
    data = {'head': {k: _s(head_in.get(k)) for k, _ in sheets.FACE_HEAD},
            'items': {k: _s(items_in.get(k)) for k, _, _ in sheets.FACE_ITEMS},
            'family': [], 'contacts': [], 'comm_means': [], 'comm_understand': []}
    for row in (raw.get('family') or [])[:sheets.FAMILY_ROWS]:
        r = {c: _s((row or {}).get(c), 200) for c, _ in sheets.FAMILY_COLS}
        if any(r.values()):
            data['family'].append(r)
    for row in (raw.get('contacts') or [])[:sheets.CONTACT_ROWS]:
        r = {c: _s((row or {}).get(c), 200) for c, _ in sheets.CONTACT_COLS}
        if any(r.values()):
            data['contacts'].append(r)
    data['comm_means'] = [v for v, _ in sheets.COMM_MEANS if v in (raw.get('comm_means') or [])]
    data['comm_understand'] = [v for v, _ in sheets.COMM_UNDERSTAND if v in (raw.get('comm_understand') or [])]
    # 台帳から入る欄が空なら台帳で補う（住所・電話・所属・保護者）
    prefill = sheets.face_prefill(beneficiary)['head']
    for k, v in prefill.items():
        if v and not data['head'].get(k):
            data['head'][k] = v
    return {'data': data, 'date': _date(raw.get('date')), 'filled_by': _s(raw.get('filled_by'), 50), 'unreadable': _s(raw.get('unreadable'), 1000)}


def read_assess(beneficiary, files):
    """戻り値 dict：items（sheets.assess_from_post と同じ形）・date・filled_by・unreadable"""
    raw = _ask(_assess_prompt(), _assess_schema(), _blocks(files), _note(beneficiary, files, 'アセスメントシート'))
    items_in = raw.get('items') or {}
    items = {}
    for k, _, _, _, _ in sheets.ASSESS_ITEMS:
        v = items_in.get(k) or {}
        level = v.get('level') if isinstance(v, dict) else None
        items[k] = {'level': level if level in sheets.LEVELS else None, 'note': _s(v.get('note') if isinstance(v, dict) else '')}
    return {'items': items, 'date': _date(raw.get('date')), 'filled_by': _s(raw.get('filled_by'), 50), 'unreadable': _s(raw.get('unreadable'), 1000)}


def _date(value):
    import datetime
    try:
        return datetime.date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None
