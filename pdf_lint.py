import re


def validate_pdf_strict(data):
    errors = []
    if not data.startswith(b"%PDF-1.4"):
        return ["bad header"]

    objs = {}
    obj_offsets = {}
    for m in re.finditer(rb"(?m)^(\d+) 0 obj\n", data):
        num = int(m.group(1))
        end = data.find(b"\nendobj", m.end())
        if end == -1:
            errors.append(f"obj {num}: missing endobj")
            continue
        objs[num] = data[m.end():end]
        obj_offsets.setdefault(num, m.start())

    xref_m = re.search(rb"\nxref\n(\d+) (\d+)\n", data)
    if not xref_m:
        errors.append("no xref table")
        return errors
    count = int(xref_m.group(2))
    table_start = xref_m.end()
    for n in range(1, count):
        entry = data[table_start + 20 * n:table_start + 20 * n + 20]
        if len(entry) < 20:
            errors.append(f"xref entry {n} truncated")
            continue
        off = int(entry[0:10])
        marker = f"{n} 0 obj".encode()
        if data[off:off + len(marker)] != marker:
            errors.append(f"xref offset for obj {n} wrong (points at {data[off:off+12]!r})")

    for num, body in objs.items():
        sm = re.match(rb"<<(.*)>>\r?\nstream\r?\n", body[:1024], re.S)
        if sm:
            lm = re.search(rb"/Length (\d+)", sm.group(1))
            if not lm:
                errors.append(f"obj {num}: stream without /Length")
                continue
            declared = int(lm.group(1))
            start = sm.end()
            tail = body[start + declared:start + declared + 11]
            if not tail.startswith(b"\nendstream"):
                errors.append(f"obj {num}: /Length {declared} does not match stream size")
        else:
            for rm in re.finditer(rb"(\d+) 0 R", body):
                ref = int(rm.group(1))
                if ref not in objs:
                    errors.append(f"obj {num}: dangling reference {ref} 0 R")

    kids = re.search(rb"/Kids \[(.*?)\]", data, re.S)
    if not kids or b"0 R" not in kids.group(1):
        errors.append("page tree empty")

    pages_objs = [n for n, b in objs.items() if b"/Type /Page " in b]
    if not pages_objs:
        errors.append("no page objects")

    if not data.rstrip().endswith(b"%%EOF"):
        errors.append("missing EOF")
    return errors
