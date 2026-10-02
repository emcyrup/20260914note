"""
診断書・意見書・検査結果などの書類を AI で読み取り、台帳への反映案と「書類から分かっていること」を作る。
保存したものは利用者ごとに小片（KnowledgeChunk）に分けて持ち、療育記録の AI が検索して参考にする（RAG）。

- read_file()      : PDF・写真を AI に読ませて、決まった形（JSON）で返してもらう
- make_draft()     : 読み取り結果から BeneficiaryKnowledge（確認待ち）と台帳への反映案を作る
- save_reviewed()  : 確認画面で直した内容を保存し、印を付けた反映案だけを台帳に入れる
- context_for()    : 療育記録の AI に渡す「書類から分かっていること（参考）」の文を作る
"""
import base64
import datetime
import json
import logging
import math
import re
from collections import Counter

import anthropic
from django.conf import settings
from django.db import transaction

from ai_assist.retrieval import split_chunks, term_counts, tokenize
from ai_assist.text import clean_ai_text, effort_kwargs

from .models import BeneficiaryKnowledge, KnowledgeChunk

logger = logging.getLogger(__name__)

READABLE_IMAGES = ('jpg', 'jpeg', 'png', 'webp', 'gif')
READABLE_EXTS = READABLE_IMAGES + ('pdf',)
MAX_PDF_BYTES = 20 * 1024 * 1024
TEXT_MAX = 12000          # 保存する書類の文字（検索用）
CONTEXT_MAX = 2400        # 療育記録の AI に渡す参考の長さ（文字）
CONTEXT_ITEMS = 5         # 参考にする書類の数（新しい順）
CONTEXT_PASSAGES = 3      # 検索で足す関係しそうな記載の数

KINDS = ['診断書', '意見書', '発達検査・評価', '紹介状・情報提供書', '医療的ケアの指示書', '個別支援計画', 'その他']

SYSTEM_PROMPT = """あなたは放課後等デイサービス（療育）の職員を手伝うAIです。
職員が取り込んだ、お子さまについての書類（診断書・意見書・発達検査の結果・医療機関からの紹介状や情報提供書・医療的ケアの指示書など）を読み、
療育で支援するときに役立つ情報を、決まった形で取り出します。

【取り出すもの】
- kind：書類の種類（診断書／意見書／発達検査・評価／紹介状・情報提供書／医療的ケアの指示書／個別支援計画／その他 のどれか）
- title：書類の表題（無ければ種類と発行元から短く）。doc_date：書類の日付（YYYY-MM-DD。無ければ空）。issuer：発行した病院・機関・医師（無ければ空）
- name_on_doc・birth_on_doc：書類に書かれたお子さまの氏名と生年月日（YYYY-MM-DD）。無ければ空
- diagnosis：診断名・障害名（書類の表記のまま。複数なら「、」で区切る。無ければ空）
- severe：重症心身障害児・重症心身障害と書いてあれば yes、そうでないと分かれば no、書いていなければ unknown
- summary：書類の内容の要約（3〜6文。検査なら結果と所見、意見書なら医師の意見の要点）
- support_points：療育の場で職員が気をつけること・配慮・支援の方法として書類に書いてあること（1項目40字程度、0〜8項目）
- medical_notes：服薬・アレルギー・てんかん発作・医療的ケア・運動の制限・禁忌など、安全にかかわること（0〜8項目）
- full_text：書類に書いてある文章を、読みやすく書き写したもの（表は「項目：値」の行に。長い書類は大事なところを中心に8000字まで）
- unreadable：読めなかったところ・判断に迷ったところ（無ければ空）

【守ること】
- 書類に書いてあることだけを取り出す。推測や一般論、書類に無い支援方法を足さない
- 保険証の番号・マイナンバー・住所・電話番号は full_text にも書かない
- 医学的な判断や診断を新しく付け加えない。数値（IQ・DQ など）は書類の表記のまま
- 常用漢字とひらがな・カタカナで書く。マークダウン（# ** など）や絵文字は使わない"""

SCHEMA = {
    'type': 'object',
    'properties': {
        'kind': {'type': 'string', 'enum': KINDS},
        'title': {'type': 'string'},
        'doc_date': {'type': 'string'},
        'issuer': {'type': 'string'},
        'name_on_doc': {'type': 'string'},
        'birth_on_doc': {'type': 'string'},
        'diagnosis': {'type': 'string'},
        'severe': {'type': 'string', 'enum': ['yes', 'no', 'unknown']},
        'summary': {'type': 'string'},
        'support_points': {'type': 'array', 'items': {'type': 'string'}},
        'medical_notes': {'type': 'array', 'items': {'type': 'string'}},
        'full_text': {'type': 'string'},
        'unreadable': {'type': 'string'},
    },
    'required': ['kind', 'title', 'doc_date', 'issuer', 'name_on_doc', 'birth_on_doc', 'diagnosis', 'severe',
                 'summary', 'support_points', 'medical_notes', 'full_text', 'unreadable'],
    'additionalProperties': False,
}


class ReadError(Exception):
    """読み取れなかった理由（画面にそのまま出す）"""


def file_ext(name):
    name = (name or '').lower()
    return name.rsplit('.', 1)[-1] if '.' in name else ''


def is_readable(name):
    return file_ext(name) in READABLE_EXTS


def _file_block(file_field, name):
    """PDF は document、写真は縮小した JPEG の image にする"""
    ext = file_ext(name) or file_ext(file_field.name)
    if ext == 'pdf':
        file_field.open('rb')
        try:
            data = file_field.read()
        finally:
            file_field.close()
        if len(data) > MAX_PDF_BYTES:
            raise ReadError('PDF が大きすぎます（20MB まで）。ページを分けてください。')
        return {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf',
                                               'data': base64.standard_b64encode(data).decode('ascii')}}
    if ext in READABLE_IMAGES:
        from records.paper import image_payload
        try:
            payload = image_payload(file_field, max_side=2000)
        except Exception:  # noqa: BLE001 - 壊れた画像など
            raise ReadError('写真を開けませんでした。撮り直すか、PDF にしてください。')
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': payload}}
    if ext == 'heic':
        raise ReadError('HEIC（iPhone の写真）は読み取れません。カメラの設定で「互換性優先」にするか、PDF にしてください。')
    raise ReadError('読み取れるのは PDF と写真（JPEG・PNG など）です。')


def read_file(beneficiary, file_field, name, hint=''):
    """書類を AI に読ませて、SCHEMA の形の dict を返す。読めないときは ReadError"""
    if not settings.ANTHROPIC_API_KEY:
        raise ReadError('AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。管理者に設定を依頼してください。')
    block = _file_block(file_field, name)
    effort = effort_kwargs(settings.AI_PLAN_MODEL, 'medium')
    output_config = {**effort.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': SCHEMA}}
    note = (f'この書類は {beneficiary.full_name}さん（生年月日 {beneficiary.date_of_birth:%Y-%m-%d}）の書類として取り込まれました。'
            f'ファイル名：{name}')
    if hint:
        note += f'\n職員のメモ：{hint[:500]}'
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        res = client.messages.create(
            model=settings.AI_PLAN_MODEL, max_tokens=16000, output_config=output_config, system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': [block, {'type': 'text', 'text': note}]}],
        )
    except anthropic.APIStatusError as e:
        logger.warning('書類の読み取りで API エラー: %s', e.status_code)
        raise ReadError(f'AI での読み取りに失敗しました（{e.status_code}）。少し待ってからもう一度お試しください。')
    except Exception as e:  # noqa: BLE001
        logger.exception('書類の読み取りでエラー')
        raise ReadError(f'AI での読み取りに失敗しました（{type(e).__name__}）。もう一度お試しください。')
    if getattr(res, 'stop_reason', '') == 'refusal':
        raise ReadError('AI がこの書類を読み取れませんでした。内容を職員が確かめて、アセスメント・資料に文章で残してください。')
    if getattr(res, 'stop_reason', '') == 'max_tokens':
        raise ReadError('書類が長すぎて読み取りきれませんでした。ページを分けて取り込んでください。')
    raw = ''.join(b.text for b in res.content if getattr(b, 'type', '') == 'text')
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ReadError('AI の返答を読み取れませんでした。もう一度お試しください。')
    return data


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None


def _ymd(d):
    return f'{d.year}/{d.month}/{d.day}' if d else ''


def _kata_to_hira(s):
    return ''.join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in (s or ''))


def _squash(s):
    return _kata_to_hira(re.sub(r'[\s　・]+', '', s or ''))


def _lines(items, limit=8):
    out = []
    for x in items or []:
        x = clean_ai_text(str(x), keep_newlines=False).lstrip('・-*•').strip()
        if x and x not in out:
            out.append(x[:120])
    return out[:limit]


def _new_lines(lines, existing):
    """すでに書いてある行は除く（空白を無視して比べる）"""
    have = _squash(existing)
    return [ln for ln in lines if _squash(ln) not in have]


def make_draft(beneficiary, data, user=None, document=None, assessment=None):
    """読み取り結果 → 確認待ちの BeneficiaryKnowledge（台帳への反映案つき）"""
    b = beneficiary
    kind = data.get('kind') if data.get('kind') in KINDS else 'その他'
    title = clean_ai_text(data.get('title', ''), keep_newlines=False)[:100]
    doc_date = _date(data.get('doc_date'))
    points = _lines(data.get('support_points'))
    medical = _lines(data.get('medical_notes'))
    warnings = []
    name_on_doc = clean_ai_text(data.get('name_on_doc', ''), keep_newlines=False)[:50]
    if name_on_doc and _squash(name_on_doc) not in (_squash(b.last_name + b.first_name),
                                                    _squash((b.last_name_kana or '') + (b.first_name_kana or ''))):
        warnings.append(f'書類の氏名「{name_on_doc}」が台帳（{b.full_name}）と違います。別のお子さまの書類でないか確かめてください。')
    birth = _date(data.get('birth_on_doc'))
    if birth and birth != b.date_of_birth:
        warnings.append(f'書類の生年月日（{_ymd(birth)}）が台帳（{_ymd(b.date_of_birth)}）と違います。')
    unreadable = clean_ai_text(data.get('unreadable', ''), keep_newlines=False)[:300]
    proposals = {'warnings': warnings, 'unreadable': unreadable}
    diagnosis = clean_ai_text(data.get('diagnosis', ''), keep_newlines=False)[:100]
    if diagnosis and _squash(diagnosis) != _squash(b.disability_type):
        proposals['disability_type'] = {'current': b.disability_type, 'new': diagnosis, 'checked': not b.disability_type}
    if data.get('severe') == 'yes' and not b.is_severe:
        proposals['is_severe'] = {'checked': True}
    head = f'【{title or kind}{f"（{_ymd(doc_date)}）" if doc_date else ""}より】'
    notes_new = _new_lines(medical, b.notes)
    if notes_new:
        proposals['notes_add'] = {'text': head + '\n' + '\n'.join(f'・{x}' for x in notes_new), 'checked': True}
    if getattr(b.facility, 'use_therapy_record', False):
        from therapy.models import TherapyProfile
        profile = TherapyProfile.objects.filter(beneficiary=b).first()
        cautions_new = _new_lines(points + [m for m in medical if m not in points], profile.cautions if profile else '')
        if cautions_new:
            proposals['cautions_add'] = {'text': '\n'.join(f'・{x}' for x in cautions_new), 'checked': True}
    return BeneficiaryKnowledge.objects.create(
        beneficiary=b, document=document, assessment=assessment, kind=kind, title=title, doc_date=doc_date,
        issuer=clean_ai_text(data.get('issuer', ''), keep_newlines=False)[:100],
        summary=clean_ai_text(data.get('summary', ''))[:2000],
        points='\n'.join(f'・{x}' for x in points),
        text=clean_ai_text(data.get('full_text', ''))[:TEXT_MAX],
        proposals=proposals, created_by=user,
    )


def reindex(knowledge):
    """検索用の小片を作り直す（要約・支援で気をつけること・書類の文字）"""
    knowledge.chunks.all().delete()
    rows, idx = [], 0
    head = f'{knowledge.kind} {knowledge.title} {knowledge.issuer}'.strip()
    for part, body in ((KnowledgeChunk.PART_SUMMARY, knowledge.summary), (KnowledgeChunk.PART_POINTS, knowledge.points)):
        if body.strip():
            text = f'{head}\n{body.strip()}'
            rows.append(KnowledgeChunk(knowledge=knowledge, part=part, index=idx, text=text,
                                       terms=term_counts(text), length=len(tokenize(text))))
            idx += 1
    for piece in split_chunks(knowledge.text, size=400, overlap=80):
        rows.append(KnowledgeChunk(knowledge=knowledge, part=KnowledgeChunk.PART_TEXT, index=idx, text=piece,
                                   terms=term_counts(piece), length=len(tokenize(piece))))
        idx += 1
    KnowledgeChunk.objects.bulk_create(rows)
    return len(rows)


def _append(current, add):
    current = (current or '').rstrip()
    return f'{current}\n{add}' if current else add


@transaction.atomic
def save_reviewed(knowledge, post, user=None):
    """
    確認画面の内容で保存する。印を付けた反映案だけ台帳に入れ、反映したものの名前のリストを返す。
    同じ書類を前に読み取ったものがあれば、新しいものに入れ替える。
    """
    from therapy.models import TherapyProfile

    k = knowledge
    b = k.beneficiary
    k.kind = (post.get('kind') or k.kind)[:50]
    k.title = (post.get('title') or '').strip()[:100]
    k.doc_date = _date(post.get('doc_date'))
    k.issuer = (post.get('issuer') or '').strip()[:100]
    k.summary = (post.get('summary') or '').strip()[:2000]
    k.points = '\n'.join(f'・{x}' for x in _lines((post.get('points') or '').splitlines(), limit=20))
    k.text = (post.get('text') or '').strip()[:TEXT_MAX]
    k.use_in_ai = post.get('use_in_ai') == '1'
    applied = []
    if k.status == BeneficiaryKnowledge.STATUS_DRAFT:
        fields = []
        if post.get('apply_disability_type') and (post.get('disability_type') or '').strip():
            b.disability_type = post['disability_type'].strip()[:100]
            fields.append('disability_type')
            applied.append('障害種別')
        if post.get('apply_is_severe'):
            b.is_severe = True
            fields.append('is_severe')
            applied.append('重症心身障害児')
        if post.get('apply_notes') and (post.get('notes_add') or '').strip():
            b.notes = _append(b.notes, post['notes_add'].strip())
            fields.append('notes')
            applied.append('備考')
        if fields:
            b.save(update_fields=fields + ['updated_at'])
        if post.get('apply_cautions') and (post.get('cautions_add') or '').strip() and getattr(b.facility, 'use_therapy_record', False):
            profile, _ = TherapyProfile.objects.get_or_create(beneficiary=b)
            profile.cautions = _append(profile.cautions, post['cautions_add'].strip())
            profile.save()
            applied.append('留意点')
        if post.get('apply_assessment') and k.document_id:
            add_assessment(k, user)
            applied.append('アセスメント・資料')
        k.applied = applied
        k.status = BeneficiaryKnowledge.STATUS_SAVED
        old = BeneficiaryKnowledge.objects.filter(beneficiary=b).exclude(pk=k.pk)
        if k.document_id:
            old.filter(document_id=k.document_id).delete()
        elif k.assessment_id:
            old.filter(assessment_id=k.assessment_id).delete()
    k.save()
    reindex(k)
    return applied


ASSESSMENT_KIND = {'発達検査・評価': 'test', '個別支援計画': 'assessment', 'その他': 'other'}   # それ以外（診断書など）は medical


def add_assessment(k, user=None):
    """
    書類・画像から読み取った内容を「アセスメント・資料」にも 1 件登録する（書類のファイルを写して付ける）。
    種類は書類の種類から決め、内容は要約と「支援で気をつけること」。
    """
    from django.core.files.base import ContentFile
    from .models import BeneficiaryAssessment
    body = k.summary.strip()
    if k.points.strip():
        body += ('\n\n' if body else '') + '【支援で気をつけること】\n' + k.points.strip()
    if k.issuer:
        body += ('\n\n' if body else '') + f'発行元：{k.issuer}'
    a = BeneficiaryAssessment(
        beneficiary=k.beneficiary, created_by=user, date=k.doc_date or datetime.date.today(),
        kind=ASSESSMENT_KIND.get(k.kind, BeneficiaryAssessment.KIND_MEDICAL),
        title=(k.title or k.kind or '読み取った書類')[:100], content=body[:20000],
    )
    doc = k.document
    if doc and doc.file:
        try:
            with doc.file.open('rb') as f:
                a.file.save(doc.file_name or doc.file.name.rsplit('/', 1)[-1], ContentFile(f.read()), save=False)
            a.file_name = (doc.file_name or '')[:200]
        except Exception:       # noqa: BLE001 元のファイルが無いときは文だけ登録する
            logger.exception('書類のファイルをアセスメントに写せない')
    a.save()
    return a


def _bm25(chunks, query, top_k, k1=1.5, b=0.75):
    q_terms = set(tokenize(query))
    if not q_terms or not chunks:
        return []
    n = len(chunks)
    avg = sum(c.length for c in chunks) / n or 1
    df = Counter(t for c in chunks for t in q_terms if t in c.terms)
    scored = []
    for c in chunks:
        score = 0.0
        for t in q_terms:
            tf = c.terms.get(t)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * c.length / avg))
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


def usable(beneficiary):
    return BeneficiaryKnowledge.objects.filter(beneficiary=beneficiary, status=BeneficiaryKnowledge.STATUS_SAVED,
                                               use_in_ai=True)


def context_for(beneficiary, query, max_chars=CONTEXT_MAX):
    """
    療育記録の AI に渡す参考の文と、使った書類の数。
    新しい書類から順に「要約・支援で気をつけること」を入れ、残りの長さで、query（今日の活動・留意点など）に
    関係しそうな書類の文字を検索して足す。その子の書類だけを使う（ほかの子・ほかの事業所のものは見ない）。
    """
    items = list(usable(beneficiary).order_by('-doc_date', '-created_at')[:CONTEXT_ITEMS])
    if not items:
        return '', 0
    parts, used = [], 0
    for k in items:
        meta = '・'.join(x for x in (k.issuer, _ymd(k.doc_date)) if x)
        body = [f'■ {k.kind}「{k.label}」' + (f'（{meta}）' if meta else '')]
        if k.summary:
            body.append('要約：' + k.summary.replace('\n', '')[:300])
        if k.points_list:
            body.append('支援で気をつけること：\n' + '\n'.join(f'・{p}' for p in k.points_list[:8]))
        piece = '\n'.join(body)
        if used + len(piece) > max_chars * 0.7 and parts:
            break
        parts.append(piece)
        used += len(piece)
    chunks = list(KnowledgeChunk.objects.filter(knowledge__in=items, part=KnowledgeChunk.PART_TEXT).select_related('knowledge'))
    passages = []
    for c in _bm25(chunks, query, CONTEXT_PASSAGES):
        piece = f'［{c.knowledge.label}］{c.text[:400]}'
        if used + len(piece) > max_chars:
            break
        passages.append(piece)
        used += len(piece)
    if passages:
        parts.append('関係しそうな記載：\n' + '\n'.join(passages))
    return '【書類から分かっていること（参考。職員が確かめて登録したもの）】\n' + '\n\n'.join(parts), len(items)
