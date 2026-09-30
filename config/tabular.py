"""
表のファイル（Excel .xlsx／CSV／タブ区切り）を「文字列の行のリスト」にする共通の読み込み。

- 拡張子ではなく中身で見分ける（.csv という名前の .xlsx、.xlsx という名前の CSV も読める）
- CSV の文字コードは UTF-8（BOM あり／なし）・UTF-16（Excel の「Unicode テキスト」）・Shift_JIS（cp932）・EUC-JP
- 区切りはカンマ・タブ・セミコロンを見出しの行から自動で判定する
- 古い Excel（.xls）は読めないので、その旨を伝える
"""
import csv
import datetime
import io

ENCODINGS = ('utf-8-sig', 'cp932', 'utf-8', 'euc_jp')
MAX_CELL = 2000


def _cell_text(v):
    if v is None:
        return ''
    if isinstance(v, datetime.datetime):
        return v.strftime('%Y-%m-%d') if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime('%Y-%m-%d %H:%M')
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, datetime.time):
        return v.strftime('%H:%M')
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()[:MAX_CELL]


def decode_text(data):
    """バイト列 → 文字列。BOM と、日本語でよく使う文字コードを順に試す"""
    if data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff'):
        return data.decode('utf-16')
    if data.startswith(b'\xef\xbb\xbf'):
        return data.decode('utf-8-sig')
    # UTF-16 で BOM が無い：NUL バイトが多い（ASCII の文字は 2 バイトのうち片方が 0 になる）
    head = data[:4000]
    if head and head.count(b'\x00') > len(head) // 8:
        even = sum(1 for i in range(0, len(head) - 1, 2) if head[i] == 0)
        odd = sum(1 for i in range(1, len(head), 2) if head[i] == 0)
        return data.decode('utf-16-be' if even > odd else 'utf-16-le', errors='replace')
    for enc in ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError('文字コードを読み取れません（UTF-8 か Shift_JIS の CSV にしてください）')


def guess_delimiter(text):
    for line in text.splitlines():
        if line.strip():
            counts = {d: line.count(d) for d in ('\t', ',', ';')}
            best = max(counts, key=counts.get)
            return best if counts[best] else ','
    return ','


def read_rows(uploaded, name=None):
    """アップロードされたファイル（または bytes）→ 文字列の行のリスト（空行は除く）"""
    name = (name if name is not None else getattr(uploaded, 'name', '') or '').lower()
    data = uploaded if isinstance(uploaded, (bytes, bytearray)) else uploaded.read()
    data = bytes(data)
    if data[:2] == b'PK':                          # xlsx（zip）
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f'Excel ファイルを開けませんでした（{e.__class__.__name__}）') from e
        ws = wb.worksheets[0]
        rows = [[_cell_text(v) for v in r] for r in ws.iter_rows(values_only=True)]
    elif data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' or name.endswith('.xls'):
        raise ValueError('古い Excel 形式（.xls）は読めません。「名前を付けて保存」で .xlsx か CSV にしてください。')
    elif name.endswith('.xlsx'):
        raise ValueError('Excel ファイル（.xlsx）として開けませんでした。Excel で開いて .xlsx で保存し直すか、CSV にしてください。')
    elif name.endswith('.pdf') or data[:4] == b'%PDF':
        raise ValueError('PDF は読めません。Excel（.xlsx）か CSV にしてください。')
    else:
        text = decode_text(data)
        delimiter = guess_delimiter(text)
        rows = [[(c or '').strip()[:MAX_CELL] for c in r] for r in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter)]
    return [r for r in rows if any(c for c in r)]


def find_header(rows, must_have, limit=30):
    """先頭 limit 行のうち、must_have の文字をすべて含む最初の行番号（表題行を飛ばす）。無ければ None"""
    for i, r in enumerate(rows[:limit]):
        cells = {(c or '').strip() for c in r}
        if all(any(m == c for c in cells) for m in must_have):
            return i
    return None
