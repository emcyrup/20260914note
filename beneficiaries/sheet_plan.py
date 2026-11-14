"""
5領域アセスメント（DevelopmentAssessment）と個別支援計画（support_plans）をつなぐ。

- radar_svg()   … 5 領域の評価（今回・前回）をレーダーチャートの SVG にする（外部ライブラリなし。画面と PDF で同じ絵）
- import_sheet() … 用紙の内容を計画に取り込む。
    ステップ1（アセスメント）の間：心身の状況・希望する生活・面談日などの空いている欄を埋める
    ステップ2（原案）の間      ：方針・本人と家族の意向の空いている欄を埋め、領域ごとの「支援の方針・目標」を短期目標として足す
                                 （同じ文の目標があれば足さない。「いまの様子」は具体的な支援内容の欄に残す）
    ステップ3 以降・終了した計画：取り込まない（担当者会議で固まった目標を勝手に変えないため）
"""
import datetime
import math

from django.db import transaction
from django.utils.html import escape
from django.utils.safestring import mark_safe

from support_plans.models import PlanGoal, SupportPlan


def radar_svg(rows, size=220, with_labels=True, short=False):
    """rows は DevelopmentAssessment.rows()。評価の無い領域は中心（0）に置く。short なら領域名を 2 文字（PDF の右上用）"""
    n = len(rows)
    if n < 3:
        return ''
    # 左右の領域名がはみ出さないように、横長の画面にする
    width = (size + (60 if short else 230)) if with_labels else size        # 領域名が収まる余白（「言語・コミュニケーション」）
    cx, cy = width / 2, size / 2
    r = size * (0.36 if with_labels else 0.44)

    def pt(i, v):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + r * v / 5 * math.cos(ang), cy + r * v / 5 * math.sin(ang)

    def poly(vals, **attrs):
        pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (pt(i, v) for i, v in enumerate(vals)))
        a = ' '.join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
        return f'<polygon points="{pts}" {a}/>'

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {size}" width="{width}" height="{size}" '
           f'class="da-radar" role="img" aria-label="5領域の評価のレーダーチャート">']
    for lv in (1, 2, 3, 4, 5):
        out.append(poly([lv] * n, fill='none', stroke='#d9d4c8' if lv < 5 else '#aaa39a', stroke_width='1'))
    for i in range(n):
        x, y = pt(i, 5)
        out.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d9d4c8" stroke-width="1"/>')
    cur = [row['rating'] or 0 for row in rows]
    prev = [row.get('prev') or 0 for row in rows]
    if any(prev):
        out.append(poly(prev, fill='rgba(140,130,120,0.18)', stroke='#8a8279', stroke_width='1.5', stroke_dasharray='4 3'))
    out.append(poly(cur, fill='rgba(78,125,137,0.28)', stroke='#4e7d89', stroke_width='2'))
    for i, v in enumerate(cur):
        if v:
            x, y = pt(i, v)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#4e7d89"/>')
    if with_labels:
        for i, row in enumerate(rows):
            x, y = pt(i, 5.6)
            anchor = 'middle' if abs(x - cx) < 6 else ('start' if x > cx else 'end')
            dy = 4 if abs(y - cy) < 6 else (12 if y > cy else -4)
            label = escape(row['label'][:2] if short else row['label'])
            out.append(f'<text x="{x:.1f}" y="{y + dy:.1f}" font-size="9.5" fill="#3b3732" text-anchor="{anchor}">{label}</text>')
    out.append('</svg>')
    return mark_safe(''.join(out))


def current_plan(beneficiary):
    """取り込み先になる計画：終了していない一番新しい計画（無ければ None）"""
    return beneficiary.support_plans.exclude(status=SupportPlan.STATUS_CLOSED).order_by('-pk').first()


def new_plan(beneficiary, user):
    """用紙から新しく計画を作る（PlanCreateView と同じ初期化）"""
    n = beneficiary.support_plans.count() + 1
    plan = SupportPlan.objects.create(facility=beneficiary.facility, beneficiary=beneficiary, title=f'第{n}期 個別支援計画',
                                      manager=user, created_by=user)
    for k, _ in SupportPlan.STEPS:
        plan.get_step(k)
    return plan


def condition_text(sheet, rows):
    lines = []
    for row in rows:
        head = f'【{row["label"]}】' + (f'評価 {row["rating"]}（{row["rating_label"]}）' if row['rating'] else '')
        lines.append(head + (f'\n{row["now"]}' if row['now'] else ''))
    if sheet.strengths:
        lines.append(f'【強み・好きなこと】\n{sheet.strengths}')
    if sheet.concerns:
        lines.append(f'【気になること・課題】\n{sheet.concerns}')
    return '\n'.join(lines)


def wishes_text(sheet):
    parts = []
    if sheet.wishes_child:
        parts.append(f'本人：{sheet.wishes_child}')
    if sheet.wishes_family:
        parts.append(f'家族：{sheet.wishes_family}')
    return '\n'.join(parts)


@transaction.atomic
def import_sheet(sheet, plan):
    """
    用紙 → 計画。戻り値は {'step': 1 or 2, 'filled': [埋めた欄の名前], 'goals': 足した目標の数, 'skipped': 同じ文で足さなかった数}。
    取り込めない状態（ステップ3 以降・終了）なら ValueError
    """
    if plan.status == SupportPlan.STATUS_CLOSED:
        raise ValueError('この計画は終了しているので取り込めません。')
    rows = sheet.rows()
    result = {'step': plan.current_step, 'filled': [], 'goals': 0, 'skipped': 0}
    if plan.current_step == SupportPlan.STEP_ASSESSMENT:
        a = plan.get_step(SupportPlan.STEP_ASSESSMENT)
        if not a.condition.strip():
            a.condition = condition_text(sheet, rows)
            result['filled'].append('心身の状況')
        if not a.wishes.strip() and wishes_text(sheet):
            a.wishes = wishes_text(sheet)
            result['filled'].append('希望する生活')
        if not a.interview_date:
            a.interview_date = sheet.date
            result['filled'].append('面談日')
        if not a.interviewed_with.strip() and sheet.interviewed_with:
            a.interviewed_with = sheet.interviewed_with
            result['filled'].append('面談相手')
        if a.interviewer_id is None and sheet.assessed_by_id:
            a.interviewer = sheet.assessed_by
        a.save()
        return result
    if plan.current_step == SupportPlan.STEP_DRAFT:
        d = plan.get_step(SupportPlan.STEP_DRAFT)
        if not d.policy.strip() and sheet.summary:
            d.policy = sheet.summary
            result['filled'].append('総合的な支援の方針')
        if not d.family_wishes.strip() and wishes_text(sheet):
            d.family_wishes = wishes_text(sheet)
            result['filled'].append('本人・家族の意向')
        d.save()
        existing = {g.content for g in plan.goals.all()}
        target = d.period_end or (sheet.date + datetime.timedelta(days=182))
        order = plan.goals.filter(goal_type=PlanGoal.TYPE_SHORT).count()
        for row in rows:
            if not row['goal']:
                continue
            content = f'{row["label"]}：{row["goal"]}'[:200]
            if content in existing:
                result['skipped'] += 1
                continue
            support = ''
            if row['now'] or row['rating']:
                support = (f'いまの様子（{sheet.date:%Y/%-m/%-d} の5領域アセスメント'
                           + (f'・評価 {row["rating"]}' if row['rating'] else '') + f'）：{row["now"]}').strip('：')
            PlanGoal.objects.create(plan=plan, goal_type=PlanGoal.TYPE_SHORT, content=content, target_date=target,
                                    support_content=support, order=order)
            existing.add(content)
            order += 1
            result['goals'] += 1
        return result
    raise ValueError('計画がステップ3（担当者会議）以降に進んでいるので取り込めません。目標を変えるときは、ステップ2 を開き直してください。')


def import_message(sheet, plan, result):
    """画面に出す文"""
    parts = []
    if result['filled']:
        parts.append('・'.join(result['filled']) + ' を埋めました')
    if result['goals']:
        parts.append(f'短期目標を {result["goals"]} 件足しました')
    if result['skipped']:
        parts.append(f'同じ文の目標 {result["skipped"]} 件はそのままです')
    if not parts:
        parts.append('すでに埋まっているため、変えたところはありません')
    where = f'「{plan.title}」のステップ{result["step"]}' + ('（アセスメント）' if result['step'] == 1 else '（計画の原案）')
    tail = ''
    if result['step'] == 1:
        tail = ' ステップ1 を完了すると、ステップ2 で領域ごとの「支援の方針・目標」を短期目標として取り込めます。'
    return f'{sheet.date:%-m月%-d日}の5領域アセスメントを {where} に取り込みました：' + '、'.join(parts) + '。' + tail
