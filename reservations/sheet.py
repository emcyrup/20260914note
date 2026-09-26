"""
月予約利用希望を Excel（.xlsx）または CSV から取り込む（ゆあーず）。

雛形（template_xlsx）：1行目が見出し「氏名 / 希望回数 / 書き方 / 1 / 2 / … / 31」、2行目が曜日（休業日は網掛け）、
3行目から利用者ごとに1行。日付の列には
  - 書き方が「○」のとき：終日なら「○」（または「終日」）、時刻を絞るなら「10,11」のように時（数字）を「,」区切りで
  - 書き方が「ダメな日」のとき：来られない日に「×」（それ以外の日はどの枠でも可能）
氏名は台帳の名前と照らして決める（姓だけでもよい）。読み取った結果は利用希望として直接保存する（入口「ファイルから」）。
"""
import csv
import datetime
import io
import re

from . import monthly, scan, services
from .models import MonthlyRequest

HEAD_NAME, HEAD_COUNT, HEAD_MODE = '氏名', '希望回数', '書き方'
MODE_OK_WORDS = ('○', '〇', 'まる', 'ok', 'OK', '可能な日時', '可能')
MODE_NG_WORDS = ('×', 'x', 'X', 'ダメな日', 'だめな日', '来られない日', 'ng', 'NG', '不可')
ALL_WORDS = ('○', '〇', '◯', '終日', 'all', 'ALL', '1', 'o', 'O', '●', '✓', 'レ')
NG_WORDS = ('×', 'x', 'X', '✕', '✗', 'ng', 'NG', '不可', 'ダメ', 'だめ')
SPREADSHEET_EXTENSIONS = ('xlsx', 'xls', 'csv')


def is_spreadsheet(filename):
    return (filename or '').lower().rsplit('.', 1)[-1] in SPREADSHEET_EXTENSIONS


def template_xlsx(facility, year, month, setting=None):
    """雛形の Excel（バイト列）。在籍中の利用者の名前を入れておく"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from beneficiaries.models import Beneficiary
    setting = setting or services.get_setting(facility)
    first, last = monthly.month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    days = monthly.month_days(year, month)
    wb = Workbook()
    ws = wb.active
    ws.title = f'{year}年{month}月'
    head = [HEAD_NAME, HEAD_COUNT, HEAD_MODE] + [d.day for d in days]
    ws.append(head)
    ws.append(['', '', '○ または ダメな日'] + [services.WEEK_JP[d.weekday()] for d in days])
    gray = PatternFill('solid', fgColor='DDDDDD')
    bold = Font(bold=True)
    for c in range(1, len(head) + 1):
        ws.cell(row=1, column=c).font = bold
        ws.cell(row=1, column=c).alignment = Alignment(horizontal='center')
        ws.cell(row=2, column=c).alignment = Alignment(horizontal='center')
    for i, d in enumerate(days):
        col = 4 + i
        ws.column_dimensions[get_column_letter(col)].width = 4.5
        if services.is_closed(facility, d, setting, closed):
            ws.cell(row=1, column=col).fill = gray
            ws.cell(row=2, column=col).fill = gray
    ws.column_dimensions['A'].width = 16
    ws.column_dimensions['B'].width = 9
    ws.column_dimensions['C'].width = 16
    for b in Beneficiary.objects.filter(facility=facility, status=Beneficiary.STATUS_ACTIVE):
        ws.append([b.full_name, '', '○'])
    ws.append([])
    ws.append(['書き方の説明'])
    ws.append(['「書き方」が ○ のとき：日付の列に、終日なら ○、時刻を絞るなら 10,11 のように時を「,」で区切って書く（網掛けはお休み）'])
    ws.append(['「書き方」が ダメな日 のとき：来られない日の列に × を書く（それ以外の日はどの枠でも可能として予定を組む）'])
    ws.append(['氏名は姓だけでも構いません。希望回数は数字だけを書いてください'])
    ws.freeze_panes = 'D3'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows_from_file(uploaded):
    """アップロードされた xlsx / csv → 文字列の行のリスト"""
    name = (uploaded.name or '').lower()
    data = uploaded.read()
    if name.endswith('.csv'):
        for enc in ('utf-8-sig', 'cp932', 'utf-8'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('CSV の文字コードを読めませんでした（UTF-8 か Shift_JIS で保存してください）。')
        return [[(c or '').strip() for c in row] for row in csv.reader(io.StringIO(text))]
    if name.endswith('.xls') and not name.endswith('.xlsx'):
        raise ValueError('古い Excel 形式（.xls）は読めません。「名前を付けて保存」で .xlsx にしてください。')
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(['' if v is None else str(v).strip() for v in row])
    return rows


def _cell_hours(text):
    """「10,11」「10 11」「10時、13時」→ [10, 11]"""
    return sorted({int(x) for x in re.findall(r'\d{1,2}', text) if 0 <= int(x) <= 23})


def parse_rows(rows, facility, year, month, setting=None):
    """
    行のリスト → 取り込み結果。
    戻り値 {'items': [{'beneficiary', 'name', 'desired', 'mode', 'wishes', 'ng_dates', 'notes'}], 'unmatched': [名前], 'errors': [文]}
    """
    setting = setting or services.get_setting(facility)
    first, last = monthly.month_range(year, month)
    closed = services.closed_dates(facility, first, last)
    out = {'items': [], 'unmatched': [], 'errors': []}
    rows = [['' if c is None else str(c).strip() for c in row] for row in rows]
    head_idx = next((i for i, r in enumerate(rows) if any((c or '').replace(' ', '') == HEAD_NAME for c in r)), None)
    if head_idx is None:
        out['errors'].append('「氏名」の見出しが見つかりません。雛形の形（氏名／希望回数／書き方／1〜31 の列）で作ってください。')
        return out
    head = [(c or '').replace(' ', '') for c in rows[head_idx]]
    col_name = head.index(HEAD_NAME)
    col_count = head.index(HEAD_COUNT) if HEAD_COUNT in head else None
    col_mode = head.index(HEAD_MODE) if HEAD_MODE in head else None
    day_cols = {}
    for i, h in enumerate(head):
        m = re.fullmatch(r'(\d{1,2})(日)?', h)
        if m and 1 <= int(m.group(1)) <= 31:
            day_cols[int(m.group(1))] = i
    if not day_cols:
        out['errors'].append('日付（1〜31）の列が見つかりません。')
        return out
    for row in rows[head_idx + 1:]:
        name = row[col_name] if col_name < len(row) else ''
        if not name or name in ('書き方の説明',) or name.startswith('「書き方」') or name.startswith('氏名は'):
            continue
        beneficiary, _ = scan.match_beneficiary(facility, name)
        if beneficiary is None:
            out['unmatched'].append(name)
            continue
        desired = 0
        if col_count is not None and col_count < len(row):
            m = re.search(r'\d+', row[col_count])
            desired = int(m.group(0)) if m else 0
        mode_text = row[col_mode] if col_mode is not None and col_mode < len(row) else ''
        mode = MonthlyRequest.WISH_NG if any(w in mode_text for w in MODE_NG_WORDS) else MonthlyRequest.WISH_OK
        wishes, ng, notes = {}, [], []
        for day_no, ci in day_cols.items():
            cell = row[ci] if ci < len(row) else ''
            if not cell:
                continue
            try:
                day = datetime.date(year, month, day_no)
            except ValueError:
                continue
            if mode == MonthlyRequest.WISH_NG:
                ng.append(day.isoformat())
                continue
            if services.is_closed(facility, day, setting, closed):
                notes.append(f'{day_no}日はお休みの日')
                continue
            if cell in ALL_WORDS:
                wishes[day.isoformat()] = 'all'
                continue
            hours = [h for h in _cell_hours(cell) if h in setting.slot_hours(day)]
            if hours:
                wishes[day.isoformat()] = hours
            elif cell in NG_WORDS:
                notes.append(f'{day_no}日の × は「○」の書き方では無視')
            else:
                wishes[day.isoformat()] = 'all'       # 印なら終日とみなす
        out['items'].append({'beneficiary': beneficiary, 'name': name, 'desired': min(max(desired, 0), 99),
                             'mode': mode, 'wishes': wishes, 'ng_dates': sorted(set(ng)), 'notes': notes})
    return out


def import_file(uploaded, facility, year, month, user=None, setting=None):
    """ファイルを読んで利用希望として保存する。戻り値は parse_rows の結果に saved（保存した名前）を足したもの"""
    setting = setting or services.get_setting(facility)
    rows = _rows_from_file(uploaded)
    result = parse_rows(rows, facility, year, month, setting)
    result['saved'] = []
    for it in result['items']:
        monthly.save_request(facility, it['beneficiary'], year, month, it['desired'], it['wishes'],
                             note='', source=MonthlyRequest.SOURCE_FILE, user=user,
                             wish_mode=it['mode'], ng_dates=it['ng_dates'])
        result['saved'].append(it['beneficiary'].full_name)
    return result
