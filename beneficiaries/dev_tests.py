"""
発達検査の結果（DevelopmentTest）：書類の AI 読み取り・フォームの読み込み・推移のグラフ。
"""
import datetime
import json
import logging
import re

from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import DevelopmentTest

logger = logging.getLogger(__name__)
ROW_MAX = 12

SYSTEM_PROMPT = """あなたは児童発達支援・放課後等デイサービスの職員を手伝うAIです。
発達検査・知能検査の結果の書類（新版K式発達検査・遠城寺式・KIDS・津守稲毛式・田中ビネー・WISC・WPPSI など）を読み、
結果の数値を決まった形で取り出します。

【取り出すもの】
- test：検査の種類（kshiki=新版K式／enjoji=遠城寺式／kids=KIDS／tsumori=津守・稲毛式／tanaka=田中ビネー／wisc=WISC／wppsi=WPPSI／other=その他）
- test_name：書類に書かれた検査名（版も。例：新版K式発達検査2020）
- date：検査日（YYYY-MM-DD。無ければ空）
- examiner：実施した機関・検査者（無ければ空）
- ca_months：生活年齢（か月。「3歳2か月」なら 38。無ければ -1）
- overall_age_months：全領域（全体）の発達年齢・精神年齢（か月。無ければ -1）
- overall_quotient：全領域の発達指数（DQ）・知能指数（IQ・FSIQ）（無ければ -1）
- results：領域ごとの結果。label は書類の領域名のまま、age_months は発達年齢（か月。無ければ -1）、quotient は指数・指標得点（無ければ -1）
- note：所見の要点（2〜4文。書類に書いてあることだけ）

【守ること】
- 書類に書いてある数値だけを取り出す。計算したり推測したりしない（発達年齢から指数を計算しない）
- 「1:6」「1歳6か月」は 18 か月にする
- 氏名・住所などの個人情報は note に書かない"""

SCHEMA = {
    'type': 'object',
    'properties': {
        'test': {'type': 'string', 'enum': [k for k, _ in DevelopmentTest.TESTS]},
        'test_name': {'type': 'string'},
        'date': {'type': 'string'},
        'examiner': {'type': 'string'},
        'ca_months': {'type': 'integer'},
        'overall_age_months': {'type': 'integer'},
        'overall_quotient': {'type': 'integer'},
        'results': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'label': {'type': 'string'}, 'age_months': {'type': 'integer'}, 'quotient': {'type': 'integer'}},
            'required': ['label', 'age_months', 'quotient'], 'additionalProperties': False}},
        'note': {'type': 'string'},
    },
    'required': ['test', 'test_name', 'date', 'examiner', 'ca_months', 'overall_age_months', 'overall_quotient', 'results', 'note'],
    'additionalProperties': False,
}


def read_test(beneficiary, file_field, name):
    """書類を AI に読ませて、フォームの初期値（dict）を返す。読めないときは knowledge.ReadError"""
    import anthropic
    from ai_assist.text import effort_kwargs
    from . import knowledge
    if not settings.ANTHROPIC_API_KEY:
        raise knowledge.ReadError('AIを使う設定（ANTHROPIC_API_KEY）がサーバーにありません。管理者に設定を依頼してください。')
    block = knowledge._file_block(file_field, name)
    effort = effort_kwargs(settings.AI_PLAN_MODEL, 'medium')
    output_config = {**effort.get('output_config', {}), 'format': {'type': 'json_schema', 'schema': SCHEMA}}
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        res = client.messages.create(
            model=settings.AI_PLAN_MODEL, max_tokens=4000, output_config=output_config, system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': [block, {'type': 'text', 'text':
                       f'{beneficiary.full_name}さん（生年月日 {beneficiary.date_of_birth:%Y-%m-%d}）の検査結果として取り込まれた書類です。ファイル名：{name}'}]}],
        )
    except anthropic.APIStatusError as e:
        raise knowledge.ReadError(f'AI での読み取りに失敗しました（{e.status_code}）。少し待ってからもう一度お試しください。')
    except Exception as e:  # noqa: BLE001
        logger.exception('検査結果の読み取りでエラー')
        raise knowledge.ReadError(f'AI での読み取りに失敗しました（{type(e).__name__}）。もう一度お試しください。')
    if getattr(res, 'stop_reason', '') == 'refusal':
        raise knowledge.ReadError('AI がこの書類を読み取れませんでした。数値を手で入れてください。')
    raw = ''.join(b.text for b in res.content if getattr(b, 'type', '') == 'text')
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise knowledge.ReadError('AI の返答を読み取れませんでした。もう一度お試しください。')
    return normalize(data)


def _pos(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 999 else None


def normalize(data):
    """AI の返答 → フォームの初期値（-1 は空に）"""
    test = data.get('test') if data.get('test') in DevelopmentTest.TEST_LABELS else 'other'
    try:
        date = datetime.date.fromisoformat((data.get('date') or '')[:10]).isoformat()
    except ValueError:
        date = ''
    rows = [{'label': (r.get('label') or '').strip()[:40], 'age_months': _pos(r.get('age_months')), 'quotient': _pos(r.get('quotient'))}
            for r in (data.get('results') or []) if (r.get('label') or '').strip()][:ROW_MAX]
    return {
        'test': test, 'test_other': (data.get('test_name') or '')[:60] if test == 'other' else '',
        'date': date, 'examiner': (data.get('examiner') or '')[:100],
        'ca_months': _pos(data.get('ca_months')), 'overall_age_months': _pos(data.get('overall_age_months')),
        'overall_quotient': _pos(data.get('overall_quotient')), 'results': rows,
        'note': (data.get('note') or '')[:4000], 'test_name': (data.get('test_name') or '')[:60],
    }


def months_from(years, months):
    """「歳」「か月」の 2 つの入力 → か月（両方空なら None）"""
    y, m = (years or '').strip(), (months or '').strip()
    if not y and not m:
        return None
    try:
        total = int(y or 0) * 12 + int(m or 0)
    except ValueError:
        return None
    return total if 0 <= total <= 999 else None


def read_form(test, post):
    """フォーム → DevelopmentTest に入れる。問題があれば文を返す"""
    if post.get('test') in DevelopmentTest.TEST_LABELS:
        test.test = post['test']
    test.test_other = (post.get('test_other') or '').strip()[:60]
    try:
        test.date = datetime.date.fromisoformat(post.get('date') or '')
    except ValueError:
        return '検査日を入れてください。'
    test.examiner = (post.get('examiner') or '').strip()[:100]
    test.ca_months = months_from(post.get('ca_y'), post.get('ca_m'))
    test.overall_age_months = months_from(post.get('oa_y'), post.get('oa_m'))
    test.overall_quotient = _pos(post.get('overall_quotient')) if (post.get('overall_quotient') or '').strip() else None
    rows = []
    for i in range(ROW_MAX):
        label = (post.get(f'r{i}_label') or '').strip()[:40]
        if not label:
            continue
        q = (post.get(f'r{i}_q') or '').strip()
        rows.append({'label': label, 'age_months': months_from(post.get(f'r{i}_y'), post.get(f'r{i}_m')),
                     'quotient': _pos(q) if q else None})
    test.results = rows
    test.note = (post.get('note') or '').strip()[:4000]
    test.source_label = (post.get('source_label') or test.source_label or '')[:200]
    return None


PALETTE = ['#4e7d89', '#c0703b', '#6a8f3c', '#8a5fa8', '#b3475f', '#3d6fb0', '#8a7a3b', '#2f8f8a', '#9a5b3c']


def trend_svg(tests, width=640, height=200):
    """
    指数の推移：同じ種類の検査ごとに、全体の指数（太線）と領域ごとの指数（細線）を検査日の順に結ぶ。
    検査が 1 回だけでも点を打つ。指数が 1 つも無ければ空
    """
    pts = [t for t in sorted(tests, key=lambda t: (t.date, t.pk)) if t.overall_quotient or any(r.get('quotient') for r in t.results or [])]
    if not pts:
        return ''
    series = {}
    for t in pts:
        if t.overall_quotient:
            series.setdefault(f'{t.test_name}：全体', []).append((t.date, t.overall_quotient))
        for r in t.results or []:
            if r.get('quotient'):
                series.setdefault(f'{t.test_name}：{r["label"]}', []).append((t.date, r['quotient']))
    if len({t.test_name for t in pts}) == 1:          # 検査が 1 種類なら、凡例に検査名を繰り返さない
        series = {name.split('：', 1)[1]: v for name, v in series.items()}
    dates = sorted({t.date for t in pts})
    vals = [v for s in series.values() for _, v in s]
    lo, hi = min(40, (min(vals) // 10) * 10), max(120, -(-max(vals) // 10) * 10)
    left, right, top, bottom = 34, 10, 10, 22
    w, h = width - left - right, height - top - bottom

    def x_of(d):
        if len(dates) == 1:
            return left + w / 2
        span = (dates[-1] - dates[0]).days or 1
        return left + w * (d - dates[0]).days / span

    def y_of(v):
        return top + h * (hi - v) / (hi - lo)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" role="img" aria-label="発達検査の指数の推移">']
    for v in range(int(lo), int(hi) + 1, 20):
        y = y_of(v)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="{"#c9c3b8" if v == 100 else "#eee9df"}"/>'
                   f'<text x="{left - 4}" y="{y + 3:.1f}" font-size="9" text-anchor="end" fill="#8a8378">{v}</text>')
    for k, d in enumerate(dates):
        anchor = 'start' if k == 0 and len(dates) > 1 else ('end' if k == len(dates) - 1 and len(dates) > 1 else 'middle')
        out.append(f'<text x="{x_of(d):.1f}" y="{height - 6}" font-size="9" text-anchor="{anchor}" fill="#8a8378">{d:%Y/%-m}</text>')
    legend = []
    for i, (name, s) in enumerate(series.items()):
        color = PALETTE[i % len(PALETTE)]
        main = name.endswith('全体')
        if len(s) > 1:
            pts_s = ' '.join(f'{x_of(d):.1f},{y_of(v):.1f}' for d, v in s)
            dash = '' if main else ' stroke-dasharray="5 3"'
            out.append(f'<polyline points="{pts_s}" fill="none" stroke="{color}" stroke-width="{3 if main else 1.5}"{dash}/>')
        for d, v in s:
            out.append(f'<circle cx="{x_of(d):.1f}" cy="{y_of(v):.1f}" r="{4 if main else 3}" fill="{color}"><title>{escape(name)} {d:%Y/%-m/%-d} {v}</title></circle>')
        legend.append((name, color, main))
    out.append('</svg>')
    leg = ''.join(f'<span class="me-3 text-nowrap"><span style="display:inline-block;width:14px;height:{4 if m else 2}px;background:{c};vertical-align:middle;"></span> {escape(n)}</span>' for n, c, m in legend)
    return mark_safe(''.join(out) + f'<div class="small text-muted mt-1">{leg}</div>')
