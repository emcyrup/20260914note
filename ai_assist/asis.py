"""
AI の返答に、原文に無い「助言・指導・評価・飾り」の言い回しが入っていないかを見る（ありのまま方針の後処理）

- find_added(source, result)  : 返答にあって原文に無い言い回しを、出てきた順に返す
- strip_added(result, found)   : それを含む文（。まで）を消した返答を返す（見出しの行は残す）
- check(source, result)        : 画面に返す {'added': [...], 'cleaned': '...'}（見つからなければ空）
AI には「足さない」と頼んでいるが、守られないことがあるので、画面で知らせて消せるようにする。
"""
import re

# 助言・指導・評価・飾りの言い回し（見つけた文字が原文にあれば、職員が言ったことなので知らせない）
PATTERNS = [
    r'(?:する|させる|行う|伝える|使う|入れる|置く|待つ|見る|示す|かける|与える|取り入れる|設ける)と(?:よい|良い|いい)(?:でしょう)?',
    r'(?:と|ば|たら)(?:よい|良い)でしょう',
    r'(?:が|も|は|こと(?:が)?)(?:大切|重要|大事|肝心|必要不可欠)',
    r'ことが(?:必要|求められ)',
    r'しましょう', r'(?:が|も)望ましい', r'心が(?:け|ける|けたい)', r'(?:と|も)考えられ', r'(?:が|も)見られ(?:た|る)',
    r'感じられ', r'うかがえ', r'(?:に|へ)留意して', r'(?:を|に)促(?:す|し)', r'てあげ', r'べき', r'ていきたい', r'ていきましょう',
    r'つなげ(?:たい|ていく)', r'期待(?:され|でき|したい)', r'(?:の)?成長(?:が|を)', r'様子(?:が|も)見られ',
    r'積極的に', r'意欲的に', r'しっかり(?:と)?', r'楽しそうに', r'落ち着いて', r'穏やかに', r'前向きに', r'丁寧に',
    r'寄り添', r'見守(?:る|り|っていく)', r'工夫(?:する|が必要|しましょう)', r'配慮(?:する|が必要|しましょう)',
]
_RE = re.compile('|'.join(f'(?:{p})' for p in PATTERNS))
_SPACE_RE = re.compile(r'\s+')


def _norm(text):
    return _SPACE_RE.sub('', text or '')


def find_added(source, result):
    """返答にあって原文に無い言い回し（重ならないように出てきた順）"""
    src = _norm(source)
    found = []
    for m in _RE.finditer(_norm(result)):
        word = m.group(0)
        if word not in src and word not in found:
            found.append(word)
    return found


def _is_heading(line):
    s = line.strip()
    return s.startswith('【') and s.endswith('】')


def strip_added(result, found):
    """found の言い回しを含む文（。まで）を消す。項目が空になったら行ごと消す。見出しは残す"""
    if not found:
        return result
    out = []
    for line in result.splitlines():
        if _is_heading(line) or not any(w in _norm(line) for w in found):
            out.append(line)
            continue
        head = re.match(r'^\s*(?:[・\-•●]\s*)?(?:[^：:\s]{1,20}[：:])?', line).group(0)
        body = line[len(head):]
        kept = [s for s in re.split(r'(?<=。)', body) if s and not any(w in _norm(s) for w in found)]
        rest = ''.join(kept).strip()
        if rest:
            out.append(head + rest)
    # 中身が無くなった見出しは消す
    cleaned = []
    for i, line in enumerate(out):
        if _is_heading(line):
            nxt = next((x for x in out[i + 1:] if x.strip()), '')
            if not nxt or _is_heading(nxt):
                continue
        cleaned.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned)).strip()


def check(source, result):
    """画面に返す形。見つからなければ {}"""
    found = find_added(source, result)
    if not found:
        return {}
    return {'added': found, 'cleaned': strip_added(result, found)}
