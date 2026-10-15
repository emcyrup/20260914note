"""
日誌の「共通の質問・個別の質問」（2026-10-15 ウィズユー藤森の依頼。QuestionSetting を使う事業所だけ）

- 共通の質問：全員に同じ質問（標準は基本 4 問：参加・着席・傾聴・指示どおり）。施設設定で変えられる
- 個別の質問：利用者ごとにレベル別（1 レベル 4〜5 問）。利用者情報の画面で職員が入れる（個別支援計画の目標から AI が案を出せる）
- 答えは「はい／どちらでもない／いいえ」。日誌にはその日の質問の文も一緒に残す（DailyRecord.question_answers）
- レベル：いまのレベルの質問に答えた直近 window 回の記録で、答えの rate % 以上が「はい」なら、次のレベルへ上げる
- 自由な観点（AI が活動ごとに作る観点）は、この事業所では出さない（入力のブレを減らすため）
"""
import datetime

from .models import ChildQuestions, DailyRecord, QuestionSetting

DEFAULT_COMMON = ['活動に参加できた', '静かに座れた', '指示を聞けていた', '指示どおりに動けた']
ANSWERS = [('yes', 'はい'), ('neutral', 'どちらでもない'), ('no', 'いいえ')]
ANSWER_LABELS = dict(ANSWERS)
MAX_QUESTIONS = 8          # 1 レベルの質問の数の上限
MAX_LEVELS = 10
TEXT_MAX = 60


def setting(facility):
    """使っていれば QuestionSetting（使っていなければ None）"""
    if facility is None:
        return None
    try:
        s = facility.question_setting
    except QuestionSetting.DoesNotExist:
        return None
    return s if s.enabled else None


def enabled(facility):
    return setting(facility) is not None


def lines(text, limit=MAX_QUESTIONS):
    out = []
    for ln in (text or '').splitlines():
        ln = ln.strip().lstrip('・-●○').strip()[:TEXT_MAX]
        if ln and ln not in out:
            out.append(ln)
    return out[:limit]


def common_questions(facility):
    s = setting(facility)
    return (lines(s.common_questions) if s else []) or list(DEFAULT_COMMON)


def child_set(beneficiary, create=False):
    if create:
        return ChildQuestions.objects.get_or_create(beneficiary=beneficiary)[0]
    return ChildQuestions.objects.filter(beneficiary=beneficiary).first()


def current_level(cq):
    """(いまのレベル, その質問)。質問の無いレベルは飛ばさず、そのまま（質問なし）"""
    if cq is None or not cq.levels:
        return 1, []
    level = max(1, min(cq.level, len(cq.levels)))
    return level, list(cq.levels[level - 1])


def blank_rows(beneficiary):
    """新しい日誌の質問（共通＋いまのレベルの個別の質問。答えは空）"""
    rows = [{'kind': 'common', 'text': t, 'level': None, 'answer': None} for t in common_questions(beneficiary.facility)]
    level, qs = current_level(child_set(beneficiary))
    rows += [{'kind': 'child', 'text': t, 'level': level, 'answer': None} for t in qs]
    return rows


def rows_for(record):
    """日誌の質問（保存した答えがあればそれ。無ければ空の質問）"""
    return clean(record.question_answers) or blank_rows(record.beneficiary)


def clean(value):
    out = []
    for v in (value or [])[:MAX_QUESTIONS * 3]:
        if not isinstance(v, dict) or not str(v.get('text', '')).strip():
            continue
        kind = 'child' if v.get('kind') == 'child' else 'common'
        try:
            level = int(v.get('level')) if kind == 'child' else None
        except (TypeError, ValueError):
            level = 1
        out.append({'kind': kind, 'text': str(v['text']).strip()[:TEXT_MAX], 'level': level,
                    'answer': v.get('answer') if v.get('answer') in ANSWER_LABELS else None})
    return out


def from_post(post):
    """画面の q_count・q_text_<i>・q_kind_<i>・q_level_<i>・q_answer_<i> から"""
    try:
        n = max(0, min(MAX_QUESTIONS * 3, int(post.get('q_count') or 0)))
    except ValueError:
        n = 0
    return clean([{'text': post.get(f'q_text_{i}', ''), 'kind': post.get(f'q_kind_{i}'),
                   'level': post.get(f'q_level_{i}'), 'answer': post.get(f'q_answer_{i}')} for i in range(n)])


def with_labels(rows):
    return [dict(r, label=ANSWER_LABELS.get(r['answer'], '未回答')) for r in rows]


def unanswered(rows):
    return sum(1 for r in rows if r['answer'] not in ANSWER_LABELS)


def level_stats(beneficiary, facility):
    """
    いまのレベルの「はい」の割合：{'level', 'levels', 'records'（数えた記録の数）, 'answers', 'yes', 'rate'（%）,
    'window', 'need'（目安の %）, 'ready'（上げてよい）, 'last'（最後のレベル）}
    """
    s = setting(facility)
    window = max(1, s.window if s else 10)
    need = s.rate if s else 80
    cq = child_set(beneficiary)
    level, _qs = current_level(cq)
    count = yes = n = 0
    for answers in (DailyRecord.objects.filter(beneficiary=beneficiary).exclude(question_answers=[])
                    .order_by('-date').values_list('question_answers', flat=True)):
        mine = [a for a in clean(answers) if a['kind'] == 'child' and a['level'] == level and a['answer']]
        if not mine:
            continue
        n += 1
        count += len(mine)
        yes += sum(1 for a in mine if a['answer'] == 'yes')
        if n >= window:
            break
    total = len(cq.levels) if cq else 0
    rate = round(yes * 100 / count) if count else 0
    return {'level': level, 'levels': total, 'records': n, 'answers': count, 'yes': yes, 'rate': rate,
            'window': window, 'need': need, 'last': level >= total,
            'ready': n >= window and count > 0 and rate >= need}


def check_level_up(beneficiary, facility, today=None):
    """目安に届いていれば次のレベルへ上げる。上げたら新しいレベル（上げなければ None）"""
    if not enabled(facility):
        return None
    cq = child_set(beneficiary)
    if cq is None:
        return None
    st = level_stats(beneficiary, facility)
    if not st['ready'] or st['last']:
        return None
    cq.level = st['level'] + 1
    cq.leveled_at = today or datetime.date.today()
    cq.save(update_fields=['level', 'leveled_at', 'updated_at'])
    return cq.level


def text_for_ai(rows):
    """AI（支援計画の下書きなど）に渡す 1 行"""
    rows = [r for r in clean(rows) if r['answer']]
    return '／'.join(f'{r["text"]}={ANSWER_LABELS[r["answer"]]}' + (f'(個別L{r["level"]})' if r['kind'] == 'child' else '')
                    for r in rows)
