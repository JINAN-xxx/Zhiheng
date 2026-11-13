"""Strict decoding for CSV files exported by UTF-8 and Chinese Windows applications."""


def decode_csv_text(content: bytes) -> str:
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('gb18030')
    if '\x00' in text:
        raise ValueError('CSV contains NUL bytes')
    return text
