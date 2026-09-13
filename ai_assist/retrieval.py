"""
算定要件資料の検索（文字バイグラム + BM25）。

外部の埋め込みAPIに頼らず、日本語の PDF を形態素解析なしで検索できるようにする。
「ベクトル化」= テキスト抽出 → チャンク分割 → バイグラム頻度の保存。
"""
import math
import re
import unicodedata
from collections import Counter

CHUNK_SIZE = 600
CHUNK_OVERLAP = 120
_SPLIT_RE = re.compile(r'[\s　、。，．,.（）()「」『』【】\[\]〔〕:：;；!！?？/／・…‥—－\-]+')


def normalize(text):
    return unicodedata.normalize('NFKC', text or '').lower()


def tokenize(text):
    """文字バイグラム（1文字の語はそのまま）"""
    tokens = []
    for run in _SPLIT_RE.split(normalize(text)):
        if not run:
            continue
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def term_counts(text):
    return dict(Counter(tokenize(text)))


def split_chunks(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """size 文字ごとに、overlap 文字重ねて分割（段落の切れ目を優先）"""
    text = re.sub(r'[ \t]+', ' ', text or '').strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind('\n', start + size // 2, end), text.rfind('。', start + size // 2, end))
            if cut > start:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def extract_pdf_pages(path):
    """PDF → [(page_no, text), ...]"""
    import pymupdf
    pages = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            pages.append((i + 1, page.get_text('text')))
    return pages


def index_document(document):
    """資料をチャンクに分けて保存する（既存チャンクは作り直す）"""
    from django.utils import timezone

    from .models import DocumentChunk

    document.chunks.all().delete()
    pages = extract_pdf_pages(document.file.path)
    rows = []
    idx = 0
    for page_no, text in pages:
        for piece in split_chunks(text):
            rows.append(DocumentChunk(document=document, index=idx, page=page_no, text=piece,
                                      terms=term_counts(piece), length=len(tokenize(piece))))
            idx += 1
    DocumentChunk.objects.bulk_create(rows, batch_size=200)
    document.page_count = len(pages)
    document.chunk_count = len(rows)
    document.indexed_at = timezone.now()
    document.error = ''
    document.save(update_fields=['page_count', 'chunk_count', 'indexed_at', 'error'])
    return len(rows)


def search(facility, query, top_k=5, k1=1.5, b=0.75):
    """施設のベクトル化済み資料から、query に関連するチャンクを BM25 で返す"""
    from .models import DocumentChunk

    q_terms = set(tokenize(query))
    if not q_terms:
        return []
    chunks = list(DocumentChunk.objects.filter(document__facility=facility, document__indexed_at__isnull=False)
                  .select_related('document'))
    if not chunks:
        return []
    n = len(chunks)
    avg_len = sum(c.length for c in chunks) / n or 1
    df = Counter()
    for c in chunks:
        for t in q_terms:
            if t in c.terms:
                df[t] += 1
    scored = []
    for c in chunks:
        score = 0.0
        for t in q_terms:
            tf = c.terms.get(t)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * c.length / avg_len))
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [{'score': round(s, 3), 'text': c.text, 'title': c.document.title, 'page': c.page}
            for s, c in scored[:top_k]]


def format_context(hits, max_chars=3000):
    """検索結果をプロンプトに貼れる形に"""
    out, used = [], 0
    for h in hits:
        piece = f'［{h["title"]} p.{h["page"]}］\n{h["text"]}'
        if used + len(piece) > max_chars:
            piece = piece[:max_chars - used]
        out.append(piece)
        used += len(piece)
        if used >= max_chars:
            break
    return '\n\n'.join(out)
