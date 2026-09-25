"""
留意点の「見取り図」と「表」。

「詳しくまとめる」で整えた留意点（【場面】の見出しと「・小見出し：内容」の項目）を読み取り、
- 見取り図：子どもを真ん中に、場面ごとの枝と項目の葉を並べたマインドマップ風の SVG
- 表：場面 × ようす・できたこと × 気をつけること・対応
にする。AI は使わず、保存されている文からそのつど作る（文を直せば図も変わる）。
見出しの無い箇条書きだけの留意点は、1つの枝にまとめる。
"""
import html
import re

OVERVIEW_TITLES = ('概要',)
SUMMARY_TITLES = ('全体のまとめ', 'まとめ')
CAUTION_TITLES = ('気をつけること', '留意点', '配慮')
CAUTION_WORDS = re.compile(r'気をつけ|注意|苦手|避け|見守|声かけ|促す|促し|配慮|危|無理|落ち着|パニック|嫌が|するとよい|しないよう|'
                           r'先に|ゆっくり|そばに|離れ|待つ|様子を見|休憩|安全|けが|ケガ|手が出|かみつ|飛び出')
LABEL_MAX = 18       # 図の葉に出す文字数
LEAVES_MAX = 6       # 1つの枝に出す葉の数
BRANCHES_MAX = 10


def parse_cautions(text):
    """
    留意点の文 → {'overview': 文, 'summary': 文, 'scenes': [{'title', 'items': [{'label','text'}], 'notes': [文]}],
                  'cautions': [{'label','text'}]}
    """
    scenes, cautions, overview, summary = [], [], [], []
    cur = None
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^【(.+?)】\s*(.*)$', line)
        if m:
            title, rest = m.group(1).strip(), m.group(2).strip()
            cur = {'title': title, 'items': [], 'notes': []}
            kind = ('overview' if title in OVERVIEW_TITLES else 'summary' if title in SUMMARY_TITLES
                    else 'caution' if title in CAUTION_TITLES else 'scene')
            cur['kind'] = kind
            if kind == 'scene':
                scenes.append(cur)
            if rest:
                _add_line(cur, rest, overview, summary, cautions)
            continue
        if cur is None:                                   # 見出しの無い箇条書き
            cur = {'title': '留意点', 'items': [], 'notes': [], 'kind': 'scene'}
            scenes.append(cur)
        _add_line(cur, line, overview, summary, cautions)
    return {'overview': ' '.join(overview), 'summary': ' '.join(summary), 'scenes': scenes, 'cautions': cautions}


def _add_line(cur, line, overview, summary, cautions):
    kind = cur.get('kind', 'scene')
    if kind == 'overview':
        overview.append(line.lstrip('・-*•●○◦ '))
        return
    if kind == 'summary':
        summary.append(line.lstrip('・-*•●○◦ '))
        return
    if re.match(r'^[・\-*•●○◦]', line):
        item = _split_item(line.lstrip('・-*•●○◦ ').strip())
        (cautions if kind == 'caution' else cur['items']).append(item)
    elif kind == 'caution':
        cautions.append(_split_item(line))
    else:
        cur['notes'].append(line)


def _split_item(text):
    m = re.match(r'^(.{1,24}?)[：:]\s*(.+)$', text)
    if m:
        return {'label': m.group(1).strip(), 'text': m.group(2).strip()}
    return {'label': '', 'text': text}       # 小見出しの無い項目（表では文だけ、図では文の先頭を葉にする）


def is_caution(item):
    return bool(CAUTION_WORDS.search(item['label'] + item['text']))


def table_rows(parsed):
    """表の行：場面ごとに（ようす・できたこと）と（気をつけること・対応）に分ける。最後に全体の行"""
    rows = []
    for sc in parsed['scenes']:
        care = [i for i in sc['items'] if is_caution(i)]
        rest = [i for i in sc['items'] if not is_caution(i)]
        rows.append({'title': sc['title'], 'rest': rest, 'care': care, 'notes': sc['notes']})
    if parsed['cautions'] or parsed['summary']:
        rows.append({'title': '全体', 'rest': [{'label': '', 'text': parsed['summary']}] if parsed['summary'] else [],
                     'care': parsed['cautions'], 'notes': []})
    return rows


def _cut(s, n):
    s = (s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def mindmap_svg(parsed, root_label):
    """マインドマップ風の SVG（横に広がる木）。枝＝場面、葉＝項目の小見出し"""
    def leaf(i):
        return i['label'] or i['text']
    branches = [{'title': sc['title'], 'leaves': [leaf(i) for i in sc['items']][:LEAVES_MAX], 'care': False}
                for sc in parsed['scenes'][:BRANCHES_MAX]]
    if parsed['cautions']:
        branches.append({'title': '気をつけること', 'leaves': [leaf(i) for i in parsed['cautions']][:LEAVES_MAX], 'care': True})
    if not branches:
        return ''
    leaf_h, pad = 24, 10
    heights = [max(1, len(b['leaves'])) * leaf_h + pad for b in branches]
    total = sum(heights)
    W, root_x, branch_x, leaf_x = 760, 20, 200, 420
    root_w, branch_w, leaf_w = 140, 180, 320
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {total + 20}" width="100%" '
           f'role="img" aria-label="留意点の見取り図" style="max-height:{min(total + 20, 900)}px;font-family:sans-serif;">']
    root_cy = (total + 20) / 2
    out.append(f'<rect x="{root_x}" y="{root_cy - 20:.0f}" width="{root_w}" height="40" rx="20" fill="#2f5d50"/>')
    out.append(f'<text x="{root_x + root_w / 2:.0f}" y="{root_cy + 5:.0f}" text-anchor="middle" font-size="13" font-weight="bold" fill="#fff">{html.escape(_cut(root_label, 10))}</text>')
    y = 10
    for b, h in zip(branches, heights):
        cy = y + h / 2
        color = '#c2703a' if b['care'] else '#3b7a68'
        out.append(f'<path d="M{root_x + root_w} {root_cy:.0f} C {root_x + root_w + 30} {root_cy:.0f}, {branch_x - 30} {cy:.0f}, {branch_x} {cy:.0f}" stroke="{color}" stroke-width="2" fill="none"/>')
        out.append(f'<rect x="{branch_x}" y="{cy - 15:.0f}" width="{branch_w}" height="30" rx="8" fill="{color}"/>')
        out.append(f'<text x="{branch_x + branch_w / 2:.0f}" y="{cy + 4:.0f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#fff">{html.escape(_cut(b["title"], 13))}</text>')
        ly = y + pad / 2 + leaf_h / 2
        for leaf in b['leaves']:
            out.append(f'<path d="M{branch_x + branch_w} {cy:.0f} C {branch_x + branch_w + 30} {cy:.0f}, {leaf_x - 30} {ly:.0f}, {leaf_x} {ly:.0f}" stroke="{color}" stroke-width="1.5" fill="none"/>')
            out.append(f'<rect x="{leaf_x}" y="{ly - 10:.0f}" width="{leaf_w}" height="20" rx="10" fill="#fff" stroke="{color}"/>')
            out.append(f'<text x="{leaf_x + 10}" y="{ly + 4:.0f}" font-size="11" fill="#222">{html.escape(_cut(leaf, LABEL_MAX + 8))}</text>')
            ly += leaf_h
        y += h
    out.append('</svg>')
    return '\n'.join(out)


def build(text, root_label):
    """画面・印刷で使うひとまとまり：解析結果・表の行・SVG。項目が何も無ければ empty=True"""
    parsed = parse_cautions(text)
    has_items = any(sc['items'] or sc['notes'] for sc in parsed['scenes']) or parsed['cautions']
    return {'parsed': parsed, 'rows': table_rows(parsed), 'svg': mindmap_svg(parsed, root_label) if has_items else '',
            'empty': not has_items}
