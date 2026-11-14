"""
音声入力 → AI の療育記録で、話した内容と意味がずれていないかを確かめる（2026-11-04 ゆあーず打ち合わせ）。

- 「できなかった」が「できた」になる（否定 → 肯定の反転）
- 否定の言葉が落ちる（話した否定の数より、整えた文の否定が少ない）
- 長く話したのに、整えた文がとても短い（要約しすぎ）

AI には「否定を変えない・要約しない」と頼んでいるが、守られないことがあるので、ここで機械的に照らし合わせ、
ずれがあれば注意書きを付けて 1 回だけ作り直す（therapy/views.py の record）。それでも残るときは画面で知らせる。
返すのは話した言葉の切れ端（その職員が話したもの）だけで、ログには中身を出さない。
"""
import re

# 否定・できなかったことを表す言い方（方言・話し言葉も）。書き言葉に直ると「なかった」「ず」などになる
_NEG = (r'(?:なかった|なくて|ないで|なく(?=[、。\s]|$)|ない(?![かがはでとよ]|ように|方)|ません(?:でした)?|ず(?:に|、|。|$)|'
        r'(?:へ|ひ|せ)ん(?:かった)?|んかった|んくて|れへん|られへん|でけへん|'
        r'難しかった|むずかしかった|難しく|むずかしく|苦手|嫌が|いやが|拒否|拒ん|できず)')
_NEG_RE = re.compile(_NEG)
_LEXICAL_RE = re.compile(r'難し|むずかし|苦手|嫌が|いやが|拒|できず')
# 否定の前の言葉（動詞などの部分）：かな・漢字の続き 1〜4 字
_STEM_RE = re.compile(r'([一-龥ぁ-んァ-ヶー]{1,4})$')
# 肯定の「できた」系（反転の手がかり）
_POS_AFTER = r'(?:た|ました|ていた|てた|るようになった|ている)'
_FILLERS = re.compile(r'えーと|えっと|えー|あのー|あの|なんか|まあ|みたいな|って感じ|ちょっと|[、。\s「」]')

SHORT_MIN_SPOKEN = 150   # これより短い話は「短すぎ」を見ない
SHORT_RATIO = 0.5        # 言いよどみを除いた話の長さに対して、整えた文がこれより短いと知らせる
LONG_RATIO = 1.6         # 整えた文が話の長さのこれ倍＋LONG_MARGIN 字より長いと「話していないことが入った」おそれ
LONG_MARGIN = 60


def _clean_len(text):
    return len(_FILLERS.sub('', text or ''))


def negatives(text):
    """否定の言い方の一覧 [(語幹, 否定の言い方, 前後の切れ端)]。同じもののくり返しは 1 つにする"""
    text = text or ''
    out, seen = [], set()
    for m in _NEG_RE.finditer(text):
        before = text[max(0, m.start() - 12):m.start()]
        stem_m = _STEM_RE.search(before)
        # 語幹は否定のすぐ前の 2 字（「あいさつができ」→「でき」「泣か」「はし」）。「難しかった」などの言葉そのものの否定は語幹なし
        stem = stem_m.group(1)[-2:] if stem_m and not _LEXICAL_RE.match(m.group(0)) else ''
        key = (stem, m.group(0))
        if key in seen:
            continue
        seen.add(key)
        start = max(text.rfind('。', 0, m.start()), text.rfind('\n', 0, m.start())) + 1
        end_candidates = [i for i in (text.find('。', m.end()), text.find('\n', m.end())) if i >= 0]
        end = min(end_candidates) if end_candidates else len(text)
        snippet = text[start:end + 1].strip()
        if len(snippet) > 60:
            a = max(0, m.start() - start - 30)
            snippet = '…' + snippet[a:a + 60].strip() + '…'
        out.append((stem, m.group(0), snippet))
    return out


def check(spoken, body):
    """
    話した言葉と整えた文を照らし合わせる。
    返り値 {'flips': [切れ端...], 'dropped': 数, 'neg_spoken': 数, 'neg_body': 数, 'short': bool, 'spoken_len', 'body_len'}
    flips：話した言葉では否定なのに、整えた文では同じ言葉が肯定だけになっているもの（反転のおそれ）
    """
    spoken, body = spoken or '', body or ''
    src = negatives(spoken)
    body_negs = len(_NEG_RE.findall(body))
    flips = []
    for stem, _neg, snippet in src:
        if len(stem) < 2:
            continue
        has_neg = re.search(re.escape(stem) + r'[^。\n]{0,4}?' + _NEG, body)
        has_pos = re.search(re.escape(stem) + _POS_AFTER, body)
        if has_pos and not has_neg and snippet not in flips:
            flips.append(snippet)
    sl, bl = _clean_len(spoken), _clean_len(body)
    return {
        'flips': flips,
        'dropped': max(0, len(src) - body_negs),
        'neg_spoken': len(src), 'neg_body': body_negs,
        'short': sl >= SHORT_MIN_SPOKEN and bl < sl * SHORT_RATIO,
        'long': bl > sl * LONG_RATIO + LONG_MARGIN,
        'spoken_len': sl, 'body_len': bl,
    }


def has_problem(result):
    return bool(result['flips'] or result['dropped'] or result['short'] or result.get('long'))


def score(result):
    """小さいほどよい（作り直したときにどちらを使うか）"""
    return len(result['flips']) * 3 + result['dropped'] * 2 + (1 if result['short'] else 0) + (3 if result.get('long') else 0)


def retry_note(spoken, result):
    """作り直すときに AI に付ける注意書き"""
    lines = ['【前に作った記録で直すところ】']
    if result['flips'] or result['dropped']:
        lines.append('前に作った記録では、話した言葉の否定（できなかった・しなかった・難しかった など）が消えたか、肯定に変わっていた。'
                     '次の話した言葉は、否定のまま記録に残すこと（「できなかった」を「できた」にしない）：')
        for _stem, _neg, snippet in negatives(spoken)[:12]:
            lines.append(f'・{snippet}')
    if result['short']:
        lines.append(f'前に作った記録は {result["body_len"]} 字で、話した内容（言いよどみを除いて約 {result["spoken_len"]} 字）より短すぎた。'
                     '要約せず、話した内容（したこと・ようす・言葉・回数・順番）を 1 つも落とさずに書くこと。')
    if result.get('long'):
        lines.append(f'前に作った記録は {result["body_len"]} 字で、話した内容（約 {result["spoken_len"]} 字）よりずっと長く、話していないことが入っていた。'
                     '話に無いこと・ほかの子の話・続きの会話は書かず、JSON を 1 つだけ返すこと。')
    return '\n'.join(lines)


def notice(result):
    """画面に出す形（問題が無ければ空）"""
    if not has_problem(result):
        return {}
    return {'fidelity': {'flips': result['flips'][:8], 'dropped': result['dropped'], 'short': result['short'], 'long': bool(result.get('long')),
                         'spoken_len': result['spoken_len'], 'body_len': result['body_len']}}
