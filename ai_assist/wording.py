"""
言葉づかいのチェック（保存前に知らせる。止めはしない）

- 不適切な表現：第三者（保護者・行政・ほかの職員）が読んで嫌な気持ちになる、決めつけになる言葉（「問題児」「わがまま」など）
- 難しい言い回し：現場の人が読むのに硬い言葉（「遂行」「離席」「適宜」など）→ やさしい言いかえを添える
- 方針・助言：記録には事実を書き、「〜するとよい」「〜が大切」などの方針・助言は書かない（ai_assist/asis.py の言い回し）
事業所ごとの辞書は施設設定の「言葉づかいの辞書」（Facility.word_rules。1 行に「言葉=言いかえ」。言いかえは無くてもよい。
行頭が「-」なら標準の辞書からその言葉を外す）。AI は使わない（文字の照合だけ）。
"""
import re

from . import asis

KIND_LABELS = {'ng': '不適切な表現', 'hard': '難しい言い回し', 'advice': '方針・助言'}

# 標準の辞書：(言葉, 言いかえ・理由, 種類)
DEFAULT_RULES = [
    ('問題児', '「気になる行動のある子」など、子どもを決めつけない言い方に', 'ng'),
    ('問題行動', '「気になる行動」や、何をしたかそのまま（例：友だちをたたいた）', 'ng'),
    ('わがまま', '何をしたか・何を言ったかをそのまま（例：「やりたくない」と言った）', 'ng'),
    ('ダメな子', '子どもを決めつけない。何があったかを書く', 'ng'),
    ('ダメ', '「〜しないでほしい」「〜は危ない」のように', 'ng'),
    ('障害児', '「お子さん」「利用者」など', 'ng'),
    ('普通の子', '「同じ年の子」など', 'ng'),
    ('健常児', '「同じ年の子」など', 'ng'),
    ('暴れ', '「大きく体を動かした」「物を投げた」のように、何をしたか', 'ng'),
    ('パニック', '「大声で泣いた」「耳をふさいだ」のように、何が起きたか', 'ng'),
    ('キレ', '「大声を出した」「物を投げた」のように、何をしたか', 'ng'),
    ('手がかかる', '何に時間がかかったかを書く', 'ng'),
    ('面倒', '何に時間がかかったか・何が難しかったかを書く', 'ng'),
    ('うるさい', '「大きな声を出した」など', 'ng'),
    ('しつこい', '「何度も同じことを言った」など、回数や様子を', 'ng'),
    ('さぼ', '「取り組まなかった」「席を離れた」など、何をしたか', 'ng'),
    ('指示に従わない', '「〜の声かけのあと、〜した」のように、声かけと行動を', 'ng'),
    ('言うことを聞かない', '「〜の声かけのあと、〜した」のように、声かけと行動を', 'ng'),
    ('させた', '「〜するよう声をかけた」「〜を手伝った」など、子どもが主語になるように', 'ng'),
    ('遂行', '「やりとげた」「最後までした」', 'hard'),
    ('離席', '「席を立った」', 'hard'),
    ('着座', '「座った」', 'hard'),
    ('適宜', '「必要なときに」', 'hard'),
    ('随時', '「そのつど」', 'hard'),
    ('促し', '「声をかけて」', 'hard'),
    ('促す', '「声をかける」', 'hard'),
    ('介入', '「職員が入って」「手伝って」', 'hard'),
    ('表出', '「言葉で出す」「顔や体で出す」', 'hard'),
    ('逸脱', '「はずれた」「別のことをした」', 'hard'),
    ('拒否', '「いやがった」「首をふった」', 'hard'),
    ('不穏', '「落ち着かない様子」。何をしていたかを書く', 'hard'),
    ('多動', '「動きが多い」。何をしていたかを書く', 'hard'),
    ('他害', '「友だちをたたいた」など、何をしたか', 'hard'),
    ('自傷', '「自分の頭をたたいた」など、何をしたか', 'hard'),
    ('常同', '「同じ動きをくり返した」', 'hard'),
    ('固執', '「〜にこだわった」', 'hard'),
    ('傾聴', '「話を聞いた」', 'hard'),
    ('受容', '「受け止めた」', 'hard'),
    ('模倣', '「まねをした」', 'hard'),
    ('注視', '「じっと見た」', 'hard'),
    ('賦活', '「元気づける」「活発にする」', 'hard'),
    ('及び', '「と」「や」', 'hard'),
    ('概ね', '「だいたい」', 'hard'),
]
_SPACE_RE = re.compile(r'\s+')


def parse_rules(text):
    """施設の辞書の文（1 行に「言葉=言いかえ」）→ (足す規則のリスト, 外す言葉の集合)"""
    add, remove = [], set()
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('-'):
            remove.add(line[1:].strip())
            continue
        word, _, hint = line.replace('＝', '=').partition('=')
        word = word.strip()
        if word:
            add.append((word, hint.strip() or '言いかえを考える', 'ng'))
    return add, remove


def rules_for(facility):
    add, remove = parse_rules(getattr(facility, 'word_rules', '') if facility is not None else '')
    rules = [r for r in DEFAULT_RULES if r[0] not in remove] + add
    seen, out = set(), []
    for r in rules:
        if r[0] not in seen:
            seen.add(r[0])
            out.append(r)
    return out


def check(text, facility=None, advice=True):
    """
    文の中の気になる言葉を、出てきた順に返す：[{'word', 'hint', 'kind', 'label'}]。同じ言葉は 1 回。
    advice=True なら方針・助言の言い回し（asis.PATTERNS）も見る
    """
    body = _SPACE_RE.sub('', text or '')
    if not body:
        return []
    hits = []
    for word, hint, kind in rules_for(facility):
        pos = body.find(word)
        if pos >= 0:
            hits.append((pos, word, hint, kind))
    if advice:
        seen = {h[1] for h in hits}
        for m in asis._RE.finditer(body):
            w = m.group(0)
            if w not in seen:
                seen.add(w)
                hits.append((m.start(), w, '記録には事実を書く。方針・助言は計画や申し送りに', 'advice'))
    hits.sort(key=lambda h: h[0])
    return [{'word': w, 'hint': hint, 'kind': kind, 'label': KIND_LABELS[kind]} for _p, w, hint, kind in hits]
