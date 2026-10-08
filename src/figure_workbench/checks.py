"""Deterministic file checks, separate from scientific and visual approval."""
import csv
import hashlib
import io
import math
import warnings

from PIL import Image

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_files(files: list[dict], read_bytes) -> list[dict]:
    findings = []

    def add(level, code, message):
        findings.append({"level": level, "code": code, "message": message})

    roles = {f["role"] for f in files}
    for role in ("figure", "data"):
        if role not in roles:
            add("error", "missing_" + role, f"缺少{role}文件")
    for f in files:
        try:
            data = read_bytes(f)
        except (OSError, ValueError) as exc:
            add("error", "unreadable_file", f"{f['name']}: {exc}")
            continue
        if digest(data) != f["sha256"]:
            add("error", "hash_mismatch", f"{f['name']} 的字节已改变")
            continue
        add("pass", "hash_ok", f"{f['name']} · SHA-256一致")
        if f["role"] == "figure":
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format != "PNG":
                            raise ValueError("仅支持真实PNG文件")
                        w, h = image.size
                        if w * h > MAX_IMAGE_PIXELS:
                            raise ValueError("图像超过2000万像素")
                        image.verify()
                    with Image.open(io.BytesIO(data)) as image:
                        image.load()
                        extrema = image.convert("RGB").getextrema()
                        uniform = all(low == high for low, high in extrema)
                if w < 800 or h < 400:
                    add("error", "low_resolution", f"{w}×{h} px；本原型要求至少800×400 px")
                else:
                    add("pass", "resolution_ok", f"{w}×{h} px")
                if uniform:
                    add("error", "uniform_image", "图像为单一颜色，需检查内容")
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                    Image.DecompressionBombWarning) as exc:
                add("error", "invalid_png", f"无法完整解码PNG: {exc}")
        elif f["role"] == "data":
            try:
                reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")), strict=True)
                header = reader.fieldnames or []
                if len(header) != len(set(header)) or not {"series", "x", "value"} <= set(header):
                    raise ValueError("CSV需要唯一的series、x、value列")
                seen, count = set(), 0
                for row in reader:
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError("CSV行列数不一致")
                    key = (row["series"].strip(), row["x"].strip())
                    if not all(key) or key in seen or not math.isfinite(float(row["value"])):
                        raise ValueError("存在空标签、重复(series,x)或非有限数值")
                    seen.add(key)
                    count += 1
                if not count:
                    raise ValueError("CSV没有数据行")
                add("pass", "csv_ok", f"{count}条有效记录；不自动证明图像与CSV语义一致")
            except (UnicodeError, ValueError, csv.Error) as exc:
                add("error", "invalid_csv", str(exc))
        elif f["role"] == "code":
            add("info", "code_archived", "源码仅归档，未执行或验证可复现性")
    return findings
