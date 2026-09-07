import csv
import os
import re
import zlib
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

import uharfbuzz as hb

import db

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

ORG_NAME = "Institute of Epidemiology, Disease Control and Research (IEDCR)"
ORG_SUBTITLE = "Employee Information & Abroad Visit Tracker System"
FOOTER_COURTESY = "Courtesy: This software is developed by Golam Mostofa, Computer Programmer, Chittagong University"
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

def _fmt_date(iso):
    """2026-01-26 -> 26 Jan 2026, keeps Ongoing/- as is."""
    if not iso or iso in ("Ongoing", "-", ""):
        return str(iso) if iso else ""
    s = str(iso).strip()
    # handle already formatted or empty
    if not s or s.lower() == "ongoing":
        return s
    try:
        # try ISO first
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        try:
            # try already datetime with time
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
            return dt.strftime("%d %b %Y %H:%M")
        except Exception:
            return s

def _fmt_generated(dt_str):
    """2026-09-07 14:30 -> 07 Sep 2026 14:30"""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            return dt.strftime("%d %b %Y")
        except Exception:
            return dt_str

def _get_excel_logo():
    """Return (png_bytes, cx_emu, cy_emu) for embedding in XLSX, or None if not available."""
    # Use PNG only (SVG was not showing in PDF)
    if not os.path.isfile(LOGO_PATH):
        return None
    try:
        from PIL import Image as _PILImage
        import io
        im = _PILImage.open(LOGO_PATH)
        # keep aspect, target height ~ 36px for Excel header (better visibility)
        target_h_px = 36
        w, h = im.size
        if h == 0:
            return None
        scale = target_h_px / h
        new_w = max(1, int(w * scale))
        new_h = target_h_px
        # resize for smaller embed to keep file small
        if im.size != (new_w, new_h):
            im = im.resize((new_w, new_h), _PILImage.Resampling.LANCZOS)
        # ensure RGBA -> convert to RGB with white bg for Excel
        if im.mode in ("RGBA", "LA"):
            bg = _PILImage.new("RGB", im.size, (255, 255, 255))
            if im.mode == "RGBA":
                bg.paste(im, mask=im.split()[3])
            else:
                bg.paste(im, mask=im.split()[1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png_data = buf.getvalue()
        # EMU: 1 inch = 914400, 96 dpi => 1 px = 9525
        cx = new_w * 9525
        cy = new_h * 9525
        return png_data, cx, cy
    except Exception:
        return None

def _excel_drawing_xml(cx, cy):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<xdr:oneCellAnchor>'
        '<xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>0</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
        f'<xdr:ext cx="{cx}" cy="{cy}"/>'
        '<xdr:pic>'
        '<xdr:nvPicPr><xdr:cNvPr id="1" name="Logo"/><xdr:cNvPicPr/></xdr:nvPicPr>'
        '<xdr:blipFill><a:blip r:embed="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        '<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
        f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
        '</xdr:pic><xdr:clientData/>'
        '</xdr:oneCellAnchor></xdr:wsDr>'
    )

def _excel_drawing_rels():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>'
        '</Relationships>'
    )


def _status_fields(limit, used, times_reached):
    if limit == 0:
        return "Unlimited", "Unlimited visits", "-"
    if used >= limit:
        return "0", "MAX REACHED - blocked", str(times_reached)
    return str(limit - used), f"{limit - used} visit(s) left", str(times_reached)


def build_report(year):
    year = str(year)
    stats = []
    for r in db.summary(year):
        limit = r["max_visits"]
        used = r["used_this_year"]
        # skip employees with no visits in this year
        if used == 0:
            continue
        remaining, status, reached = _status_fields(limit, used, r["times_max_reached"])
        stats.append({
            "emp_id": r["emp_id"],
            "name": r["name"],
            "designation": r["designation"],
            "emp_type": r["emp_type"] or "",
            "project": r["project_name"] or "",
            "limit_txt": "Unlimited" if limit == 0 else str(limit),
            "used": used,
            "remaining": remaining,
            "status": status,
            "reached": reached,
        })
    details = []
    for emp in stats:
        visits = db.list_visits(year=year, emp_id=emp["emp_id"])
        # skip if no visits (should not happen after filter, but safe)
        if not visits:
            continue
        details.append({
            "emp_id": emp["emp_id"],
            "name": emp["name"],
            "designation": emp["designation"],
            "visits": [
                {
                    "date": v["visit_date"],
                    "country": v["country"],
                    "title": v["purpose_title"],
                    "detail": v["purpose_detail"],
                }
                for v in visits
            ],
        })
    blocked = sum(1 for s in stats if s["status"].startswith("MAX REACHED"))
    return {
        "year": year,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_employees": len(stats),
        "blocked_count": blocked,
        "stats": stats,
        "details": details,
    }


def build_tenure_report(today=None):
    from datetime import date as _date
    today_iso = today or _date.today().isoformat()
    # normalize today via db helper if provided as other format
    try:
        today_iso = db._parse_tenure_date(today_iso, "today", allow_empty=True) or _date.today().isoformat()
    except Exception:
        today_iso = _date.today().isoformat()
    all_summaries = db.tenure_summary_all(today=today_iso)
    # skip employees with no tenure records
    summaries = [s for s in all_summaries if s["interval_count"] > 0]
    details = []
    for s in summaries:
        info = db.tenure_summary_for_employee(s["emp_id"], today=today_iso)
        intervals = []
        for iv in info["intervals"]:
            intervals.append({
                "join": iv["join_date"],
                "release": iv["release_date"] or "Ongoing",
                "release_raw": iv["release_date"],
                "duration": iv.get("duration_formatted", ""),
                "days": iv.get("duration_days", 0),
                "type": iv.get("tenure_type", ""),
                "project": iv.get("project_name") or "",
                "org": iv.get("organization_name") or "",
                "role": iv.get("role") or "",
                "notes": iv.get("notes") or "",
            })
        # skip if no intervals (should not happen after filter, but safe)
        if not intervals:
            continue
        details.append({
            "emp_id": s["emp_id"],
            "name": s["name"],
            "designation": s["designation"],
            "intervals": intervals,
            "total_days": s["total_days"],
            "total_formatted": s["total_formatted"],
            "status": "Active" if s["is_currently_active"] else "Released",
        })
    active = sum(1 for s in summaries if s["is_currently_active"])
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": today_iso,
        "total_employees": len(summaries),
        "active_count": active,
        "released_count": len(summaries) - active,
        "summaries": summaries,
        "details": details,
    }


def _tenure_summary_row(s):
    return [s["emp_id"], s["name"], s["designation"],
            s["interval_count"], s["total_days"], s["total_formatted"],
            s["first_join"] or "-", s["last_release"] or "Ongoing",
            "Active" if s["is_currently_active"] else "Released"]


SUMMARY_HEADERS = ["ID", "Name", "Designation", "Type", "Project", "Yearly Limit",
                   "Visits Used", "Remaining", "Status", "Times Max Reached"]
DETAIL_HEADERS = ["Employee ID", "Employee Name", "Date", "Country", "Purpose Title", "Purpose Details"]

TENURE_SUMMARY_HEADERS = ["ID", "Name", "Designation", "# Periods", "Total Days", "Total Time",
                          "First Join", "Last Release", "Current Status"]
TENURE_DETAIL_HEADERS = ["Employee ID", "Employee Name", "Join Date", "Release Date",
                         "Duration", "Days", "Type", "Project", "Collaborator Org", "Role", "Notes"]


def _summary_row(s):
    return [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
            s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]


def export_csv(path, year):
    rep = build_report(year)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([ORG_NAME])
        w.writerow([ORG_SUBTITLE])
        w.writerow([f"Abroad Visit Report - Year {rep['year']}"])
        w.writerow([f"Generated: {rep['generated']}",
                    f"Total employees: {rep['total_employees']}",
                    f"Reached yearly limit: {rep['blocked_count']}"])
        w.writerow([])
        w.writerow(["YEARLY EMPLOYEE STATISTICS"])
        w.writerow(SUMMARY_HEADERS)
        for s in rep["stats"]:
            w.writerow(_summary_row(s))
        w.writerow([])
        w.writerow(["VISIT DETAILS"])
        w.writerow(DETAIL_HEADERS)
        for d in rep["details"]:
            if not d["visits"]:
                w.writerow([d["emp_id"], d["name"], f"(no visits recorded in {year})", "", "", ""])
                continue
            for v in d["visits"]:
                w.writerow([d["emp_id"], d["name"], v["date"], v["country"], v["title"], v["detail"]])


def export_tenure_csv(path, today=None):
    rep = build_tenure_report(today)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([ORG_NAME])
        w.writerow([ORG_SUBTITLE])
        w.writerow([f"Tenure Report - As of {rep['today']}"])
        w.writerow([f"Generated: {rep['generated']}",
                    f"Total employees: {rep['total_employees']}",
                    f"Active: {rep['active_count']}",
                    f"Released: {rep['released_count']}"])
        w.writerow([])
        w.writerow(["TENURE SUMMARY (per employee)"])
        w.writerow(TENURE_SUMMARY_HEADERS)
        for s in rep["summaries"]:
            w.writerow(_tenure_summary_row(s))
        w.writerow([])
        w.writerow(["TENURE DETAILS (intervals)"])
        w.writerow(TENURE_DETAIL_HEADERS)
        for d in rep["details"]:
            if not d["intervals"]:
                w.writerow([d["emp_id"], d["name"], "(no tenure recorded)", "", "", "", "", "", "", "", ""])
                continue
            for iv in d["intervals"]:
                w.writerow([d["emp_id"], d["name"], iv["join"], iv["release"],
                            iv["duration"], iv["days"], iv["type"], iv["project"], iv["org"], iv["role"], iv["notes"]])


def _clean_xml(text):
    return _xml_escape(_ILLEGAL_XML.sub("", str(text)))


def _col_letter(n):
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _disp_len(value):
    width = 0.0
    for ch in _ILLEGAL_XML.sub("", str(value)):
        code = ord(ch)
        if code > 0x1100:
            width += 2
        elif code > 126:
            width += 1.7
        else:
            width += 1
    return int(round(width))


def export_xlsx(path, year):
    rep = build_report(year)
    logo_info = _get_excel_logo()

    def cell(ref, value, style=None, numeric=False):
        s_attr = f' s="{style}"' if style is not None else ""
        if numeric and isinstance(value, (int, float)):
            return f'<c r="{ref}"{s_attr}><v>{value}</v></c>'
        return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{_clean_xml(value)}</t></is></c>'

    sheet_rows = []
    width_map = {}

    def add_row(cells, style=None, numeric_cols=(), fit=True):
        idx = len(sheet_rows) + 1
        parts = []
        for ci, value in enumerate(cells, start=1):
            st = style if style is not None else None
            parts.append(cell(f"{_col_letter(ci)}{idx}", value, st, numeric=ci in numeric_cols))
            if fit and str(value).strip():
                ln = _disp_len(value)
                if ln > width_map.get(ci, 0):
                    width_map[ci] = ln
        sheet_rows.append(f'<row r="{idx}">' + "".join(parts) + "</row>")

    # Header with logo alignment: if logo, leave A column empty for image, put org name in B
    if logo_info:
        add_row(["", ORG_NAME], style=2, fit=False)
        add_row(["", ORG_SUBTITLE], style=1, fit=False)
    else:
        add_row([ORG_NAME], style=2, fit=False)
        add_row([ORG_SUBTITLE], style=1, fit=False)
    add_row([f"Abroad Visit Report - Year {rep['year']}"], style=2, fit=False)
    add_row([f"Generated: {rep['generated']}",
             f"Total employees: {rep['total_employees']}",
             f"Reached yearly limit: {rep['blocked_count']}"], fit=False)
    add_row([""])
    add_row(["YEARLY EMPLOYEE STATISTICS"], style=1, fit=False)
    add_row(SUMMARY_HEADERS, style=1)
    for s in rep["stats"]:
        add_row(_summary_row(s), numeric_cols=(5,))
    add_row([""])
    add_row(["VISIT DETAILS"], style=1, fit=False)
    add_row(DETAIL_HEADERS, style=1)
    for d in rep["details"]:
        if not d["visits"]:
            add_row([d["emp_id"], d["name"], f"(no visits recorded in {year})", "", "", ""])
            continue
        for v in d["visits"]:
            add_row([d["emp_id"], d["name"], v["date"], v["country"], v["title"], v["detail"]])

    cols_xml = "".join(
        f'<col min="{ci}" max="{ci}" width="{min(max(ln * 1.1 + 2, 9), 55):.2f}" customWidth="1"/>'
        for ci, ln in sorted(width_map.items())
    )
    cols_xml = f"<cols>{cols_xml}</cols>" if cols_xml else ""
    logo_info = _get_excel_logo()
    if logo_info:
        png_data, cx, cy = logo_info
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"{cols_xml}"
            "<sheetData>" + "".join(sheet_rows) + "</sheetData>"
            '<drawing r:id="rId1"/>'
            "</worksheet>"
        )
    else:
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{cols_xml}"
            "<sheetData>" + "".join(sheet_rows) + "</sheetData>"
            "</worksheet>"
        )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="15"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    if logo_info:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.drawing+xml"/>'
            '</Types>'
        )
    else:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="Visit Report {rep["year"]}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        if logo_info:
            png_data, cx, cy = logo_info
            z.writestr("xl/media/image1.png", png_data)
            z.writestr("xl/drawings/drawing1.xml", _excel_drawing_xml(cx, cy))
            z.writestr("xl/drawings/_rels/drawing1.xml.rels", _excel_drawing_rels())
            z.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
                       '</Relationships>')


def export_tenure_xlsx(path, today=None):
    rep = build_tenure_report(today)
    logo_info = _get_excel_logo()
    def cell(ref, value, style=None, numeric=False):
        s_attr = f' s="{style}"' if style is not None else ""
        if numeric and isinstance(value, (int, float)):
            return f'<c r="{ref}"{s_attr}><v>{value}</v></c>'
        return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{_clean_xml(value)}</t></is></c>'
    sheet_rows = []
    width_map = {}
    def add_row(cells, style=None, numeric_cols=(), fit=True):
        idx = len(sheet_rows) + 1
        parts = []
        for ci, value in enumerate(cells, start=1):
            st = style if style is not None else None
            parts.append(cell(f"{_col_letter(ci)}{idx}", value, st, numeric=ci in numeric_cols))
            if fit and str(value).strip():
                ln = _disp_len(value)
                if ln > width_map.get(ci, 0):
                    width_map[ci] = ln
        sheet_rows.append(f'<row r="{idx}">' + "".join(parts) + "</row>")
    if logo_info:
        add_row(["", ORG_NAME], style=2, fit=False)
        add_row(["", ORG_SUBTITLE], style=1, fit=False)
    else:
        add_row([ORG_NAME], style=2, fit=False)
        add_row([ORG_SUBTITLE], style=1, fit=False)
    add_row([f"Tenure Report - As of {rep['today']}"], style=2, fit=False)
    add_row([f"Generated: {rep['generated']}",
             f"Total employees: {rep['total_employees']}",
             f"Active: {rep['active_count']}",
             f"Released: {rep['released_count']}"], fit=False)
    add_row([""])
    add_row(["TENURE SUMMARY (per employee)"], style=1, fit=False)
    add_row(TENURE_SUMMARY_HEADERS, style=1)
    for s in rep["summaries"]:
        add_row(_tenure_summary_row(s), numeric_cols=(4,5))
    add_row([""])
    add_row(["TENURE DETAILS (intervals)"], style=1, fit=False)
    add_row(TENURE_DETAIL_HEADERS, style=1)
    for d in rep["details"]:
        if not d["intervals"]:
            add_row([d["emp_id"], d["name"], "(no tenure recorded)", "", "", "", "", "", "", "", ""])
            continue
        for iv in d["intervals"]:
            add_row([d["emp_id"], d["name"], iv["join"], iv["release"], iv["duration"], iv["days"],
                     iv["type"], iv["project"], iv["org"], iv["role"], iv["notes"]], numeric_cols=(6,))

    cols_xml = "".join(
        f'<col min="{ci}" max="{ci}" width="{min(max(ln * 1.1 + 2, 9), 55):.2f}" customWidth="1"/>'
        for ci, ln in sorted(width_map.items())
    )
    cols_xml = f"<cols>{cols_xml}</cols>" if cols_xml else ""
    logo_info = _get_excel_logo()
    if logo_info:
        png_data, cx, cy = logo_info
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"{cols_xml}"
            "<sheetData>" + "".join(sheet_rows) + "</sheetData>"
            '<drawing r:id="rId1"/>'
            "</worksheet>"
        )
    else:
        sheet1 = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{cols_xml}"
            "<sheetData>" + "".join(sheet_rows) + "</sheetData>"
            "</worksheet>"
        )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="15"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    if logo_info:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.drawing+xml"/>'
            '</Types>'
        )
    else:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>'
        )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="Tenure Report" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        if logo_info:
            png_data, cx, cy = logo_info
            z.writestr("xl/media/image1.png", png_data)
            z.writestr("xl/drawings/drawing1.xml", _excel_drawing_xml(cx, cy))
            z.writestr("xl/drawings/_rels/drawing1.xml.rels", _excel_drawing_rels())
            z.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
                       '</Relationships>')


PAGE_W, PAGE_H = 842, 595
_MARGIN = 36
_BOTTOM = 46


def _pdf_escape(text):
    text = str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("cp1252", "replace").decode("cp1252")


def _wrap_pdf(text, max_w, measure, size):
    raw = _ILLEGAL_XML.sub("", str(text)).replace("\n", " ").strip()

    def fits(t):
        return measure(t, size) <= max_w

    out = []
    cur = ""
    for word in raw.split():
        while word and not fits(word):
            piece = word[0]
            i = 1
            while i < len(word) and fits(piece + word[i]):
                piece += word[i]
                i += 1
            if cur:
                out.append(cur)
                cur = ""
            out.append(piece)
            word = word[i:]
        cand = f"{cur} {word}".strip() if cur else word
        if fits(cand):
            cur = cand
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out or [""]


class _TrueTypeFont:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        num_tables = int.from_bytes(d[4:6], "big")
        tables = {}
        for i in range(num_tables):
            off = 12 + 16 * i
            tag = d[off:off + 4].decode("latin-1")
            tables[tag] = int.from_bytes(d[off + 8:off + 12], "big")
        head = tables["head"]
        hhea = tables["hhea"]
        self.units_per_em = int.from_bytes(d[head + 18:head + 20], "big") or 1000
        self.ascent = self._s16(hhea + 4)
        self.descent = self._s16(hhea + 6)
        self.bbox = [round(self._s16(head + o) * 1000 / self.units_per_em) for o in (36, 38, 40, 42)]
        n_metrics = int.from_bytes(d[hhea + 34:hhea + 36], "big")
        hm = tables["hmtx"]
        self.advances = [int.from_bytes(d[hm + 4 * i:hm + 4 * i + 2], "big") for i in range(n_metrics)]
        blob = hb.Blob(self.data)
        self.hb_face = hb.Face(blob)
        self.hb_font = hb.Font(self.hb_face)
        cm = tables["cmap"]
        n_sub = int.from_bytes(d[cm + 2:cm + 4], "big")
        prio = {(3, 10): 5, (0, 4): 5, (0, 6): 5, (3, 1): 3, (0, 1): 3, (0, 2): 3, (0, 3): 3}
        best = None
        for i in range(n_sub):
            base = cm + 4 + 8 * i
            pid = int.from_bytes(d[base:base + 2], "big")
            eid = int.from_bytes(d[base + 2:base + 4], "big")
            soff = int.from_bytes(d[base + 4:base + 8], "big")
            fmt = int.from_bytes(d[cm + soff:cm + soff + 2], "big")
            key = (prio.get((pid, eid), 0), 1 if fmt == 12 else 0)
            if best is None or key > best[0]:
                best = (key, cm + soff, fmt)
        _, sub, fmt = best
        self.cmap = {}
        if fmt == 12:
            ng = int.from_bytes(d[sub + 12:sub + 16], "big")
            for i in range(ng):
                gb = sub + 16 + 12 * i
                start = int.from_bytes(d[gb:gb + 4], "big")
                end = int.from_bytes(d[gb + 4:gb + 8], "big")
                g0 = int.from_bytes(d[gb + 8:gb + 12], "big")
                for c in range(start, min(end, 0x10FFFF) + 1):
                    self.cmap[c] = g0 + (c - start)
        elif fmt == 4:
            seg_x2 = int.from_bytes(d[sub + 6:sub + 8], "big")
            seg = seg_x2 // 2
            ends = sub + 14
            starts = ends + seg_x2 + 2
            deltas = starts + seg_x2
            ranges = deltas + seg_x2
            for i in range(seg):
                end = int.from_bytes(d[ends + 2 * i:ends + 2 * i + 2], "big")
                start = int.from_bytes(d[starts + 2 * i:starts + 2 * i + 2], "big")
                delta = self._s16(deltas + 2 * i)
                ro = int.from_bytes(d[ranges + 2 * i:ranges + 2 * i + 2], "big")
                if start == 0xFFFF:
                    continue
                for c in range(start, end + 1):
                    if ro == 0:
                        g = (c + delta) & 0xFFFF
                    else:
                        addr = ranges + 2 * i + ro + 2 * (c - start)
                        g = int.from_bytes(d[addr:addr + 2], "big")
                        if g:
                            g = (g + delta) & 0xFFFF
                    if g:
                        self.cmap[c] = g

    def _s16(self, off):
        return int.from_bytes(self.data[off:off + 2], "big", signed=True)

    def gid(self, ch):
        return self.cmap.get(ord(ch), 0)

    def width_units(self, gid):
        if not self.advances:
            return self.units_per_em // 2
        return self.advances[gid] if gid < len(self.advances) else self.advances[-1]

    def char_width(self, ch):
        return self.width_units(self.gid(ch))

    def shape_text(self, s):
        buf = hb.Buffer()
        buf.add_str(str(s))
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf)
        infos = buf.glyph_infos or []
        positions = buf.glyph_positions or []
        gids = [info.codepoint for info in infos]
        total_advance = sum(pos.x_advance for pos in positions) if positions else 0
        return gids, total_advance


_FONT_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]
_FONT_PRIORITY = [
    "kalpurush", "solaimanlipi", "notosansbengali", "nikosh", "nirmala", "vrinda", "shonar", "bangla", "bengali", "sutonny", "charukola",
]


def _font_file(name):
    for d in _FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def _iter_ttf_files():
    seen = set()
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for name in entries:
            if not name.lower().endswith(".ttf"):
                continue
            p = os.path.join(d, name)
            if p not in seen:
                seen.add(p)
                yield p


def _priority_index(path):
    lower = re.sub(r"[^a-z0-9]", "", os.path.basename(path).lower())
    for i, kw in enumerate(_FONT_PRIORITY):
        if kw in lower:
            return i
    return len(_FONT_PRIORITY)


def _bold_variant(path, tf_reg):
    stem, ext = os.path.splitext(path)
    for cand in (stem + "b" + ext, stem + "bd" + ext, stem + "-bold" + ext):
        if os.path.isfile(cand):
            try:
                return _TrueTypeFont(cand)
            except Exception:
                pass
    return tf_reg


def _pick_unicode_fonts(codepoints):
    needed = {cp for cp in codepoints if cp > 126}
    if not needed:
        return None, None
    best = None
    best_tf = None
    for path in sorted(_iter_ttf_files(), key=_priority_index):
        try:
            tf = _TrueTypeFont(path)
        except Exception:
            continue
        coverage = sum(1 for cp in needed if cp in tf.cmap)
        score = (coverage, -_priority_index(path))
        if best is None or score > best:
            best = score
            best_tf = tf
            if coverage == len(needed) and _priority_index(path) < len(_FONT_PRIORITY):
                break
    if best_tf is None:
        return None, None
    return best_tf, _bold_variant(best_tf.path, best_tf)


class _PdfDoc:
    def __init__(self, mode="latin", font_reg=None, font_bold=None):
        self.mode = mode
        self.font_reg = font_reg
        self.font_bold = font_bold
        self.used_gids_reg = set()
        self.used_gids_bold = set()
        self.pages = []
        self._ops = None
        # logo handling
        self._logo_raw = None
        self._logo_w = 0
        self._logo_h = 0
        self._logo_obj_id = None
        self._try_load_logo()

    def _try_load_logo(self):
        # PDF: use PNG only (SVG was not showing)
        if not os.path.isfile(LOGO_PATH):
            return
        try:
            from PIL import Image as _PILImage
            im = _PILImage.open(LOGO_PATH)
            # convert to RGB, composite over white if has alpha
            if im.mode in ("RGBA", "LA"):
                bg = _PILImage.new("RGB", im.size, (255, 255, 255))
                if im.mode == "RGBA":
                    bg.paste(im, mask=im.split()[3])
                else:
                    bg.paste(im, mask=im.split()[1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            # keep high-res for sharp PDF (limit 450px)
            max_px = 450
            if max(im.size) > max_px:
                im.thumbnail((max_px, max_px), _PILImage.Resampling.LANCZOS)
            self._logo_w, self._logo_h = im.size
            raw = im.tobytes()
            self._logo_raw = zlib.compress(raw)
            self._logo_is_compressed = True
            self._logo_raw_uncompressed_len = len(raw)
        except Exception:
            self._logo_raw = None

    def _new_page(self):
        self._ops = []
        self.pages.append(self._ops)

    def text(self, x_top, y_top, size, s, bold=False, gray=0):
        font = "/F2" if bold else "/F1"
        x = _MARGIN + x_top
        y = PAGE_H - y_top
        raw = _ILLEGAL_XML.sub("", str(s))
        if self.mode == "unicode":
            tf = self.font_bold if bold else self.font_reg
            used = self.used_gids_bold if bold else self.used_gids_reg
            gids, _ = tf.shape_text(raw)
            hexs = []
            for g in gids:
                used.add(g)
                hexs.append(f"{g:04X}")
            payload = "<" + "".join(hexs) + ">"
        else:
            payload = "(" + _pdf_escape(raw) + ")"
        self._ops.append(
            f"{gray} g BT {font} {size} Tf {x:.1f} {y:.1f} Td {payload} Tj ET 0 g"
        )

    def line(self, x_top, y_top, x2_top, y2_top, gray=0.75):
        self._ops.append(
            f"{gray} G 0.6 w {_MARGIN + x_top:.1f} {PAGE_H - y_top:.1f} m {_MARGIN + x2_top:.1f} {PAGE_H - y2_top:.1f} l S 0 G"
        )

    def rect_fill(self, x_top, y_top, w, h, gray=0.9):
        self._ops.append(f"{gray} g {_MARGIN + x_top:.1f} {PAGE_H - y_top - h:.1f} {w:.1f} {h:.1f} re f 0 g")

    def draw_logo(self, x_top, y_top, w, h):
        if self._logo_raw is None:
            return
        x = _MARGIN + x_top
        y = PAGE_H - y_top - h
        self._ops.append(f"q {w:.1f} 0 0 {h:.1f} {x:.1f} {y:.1f} cm /ImLogo Do Q")

    def _embed_logo(self, add_obj):
        if self._logo_raw is None:
            return None
        try:
            data = self._logo_raw
            # need to store as stream with Filter FlateDecode
            obj = (
                f"<< /Type /XObject /Subtype /Image /Width {self._logo_w} /Height {self._logo_h} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"
            )
            _id = add_obj(obj)
            self._logo_obj_id = _id
            return _id
        except Exception:
            return None

    def _embed_font(self, add_obj, tf, name, used_gids):
        w_parts = [f"{g} [{round(tf.width_units(g) * 1000 / tf.units_per_em)}]" for g in sorted(used_gids)]
        ascent = round(tf.ascent * 1000 / tf.units_per_em)
        descent = round(tf.descent * 1000 / tf.units_per_em)
        file_id = add_obj(
            f"<< /Length {len(tf.data)} /Length1 {len(tf.data)} >>\nstream\n".encode() + tf.data + b"\nendstream"
        )
        desc_id = add_obj(
            f"<< /Type /FontDescriptor /FontName /{name} /Flags 4 "
            f"/FontBBox [{tf.bbox[0]} {tf.bbox[1]} {tf.bbox[2]} {tf.bbox[3]}] "
            f"/ItalicAngle 0 /Ascent {ascent} /Descent {descent} /CapHeight {ascent} /StemV 80 "
            f"/FontFile2 {file_id} 0 R >>".encode()
        )
        cid_id = add_obj(
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {desc_id} 0 R /DW 1000 /W [{' '.join(w_parts)}] /CIDToGIDMap /Identity >>".encode()
        )
        return add_obj(
            f"<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H "
            f"/DescendantFonts [{cid_id} 0 R] >>".encode()
        )

    def save(self, path):
        objects = []

        def add_obj(body):
            objects.append(body)
            return len(objects)

        catalog_id = add_obj(None)
        pages_id = add_obj(None)
        if self.mode == "unicode":
            f1 = self._embed_font(add_obj, self.font_reg, "ReportFont", self.used_gids_reg)
            f2 = self._embed_font(add_obj, self.font_bold, "ReportFont-Bold", self.used_gids_bold)
        else:
            f1 = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
            f2 = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        # embed logo once if available
        logo_id = None
        if self._logo_raw is not None:
            logo_id = self._embed_logo(add_obj)
        page_ids = []
        for ops in self.pages:
            stream = ("\n".join(ops)).encode("cp1252", "replace")
            content_id = add_obj(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
            xobj_part = f" /XObject << /ImLogo {logo_id} 0 R >>" if logo_id else ""
            page_ids.append(add_obj(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >>{xobj_part} >> "
                f"/Contents {content_id} 0 R >>".encode()
            ))
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n")
            offsets = []
            for obj_num, body in enumerate(objects, start=1):
                offsets.append(f.tell())
                f.write(f"{obj_num} 0 obj\n".encode() + body + b"\nendobj\n")
            xref_pos = f.tell()
            f.write(f"xref\n0 {len(objects) + 1}\n".encode())
            f.write(b"0000000000 65535 f \n")
            for off in offsets:
                f.write(f"{off:010d} 00000 n \n".encode())
            f.write(
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
            )


class _PdfReport:
    COLS_SUMMARY = [("ID", 55), ("Name", 125), ("Designation", 90), ("Type", 65), ("Project", 100),
                    ("Limit", 42), ("Used", 40), ("Remaining", 68), ("Status", 95), ("Times Max Reached", 90)]
    COLS_DETAIL = [("Date", 70), ("Country", 130), ("Purpose Title", 190), ("Purpose Details", 380)]
    BODY = 8
    HEAD = 8.5
    LEAD = 11

    def __init__(self, rep):
        self.rep = rep
        needed = set()
        for s in rep["stats"]:
            for v in s.values():
                needed.update(ord(c) for c in str(v))
        for d in rep["details"]:
            for v in (d["name"], d["designation"]):
                needed.update(ord(c) for c in str(v))
            for visit in d["visits"]:
                for v in visit.values():
                    needed.update(ord(c) for c in str(v))

        def needs_unicode():
            for cp in needed:
                try:
                    chr(cp).encode("cp1252")
                except UnicodeEncodeError:
                    return True
            return False

        font_reg, font_bold = _pick_unicode_fonts(needed)
        if needs_unicode() and font_reg is not None:
            self.doc = _PdfDoc("unicode", font_reg, font_bold)
        else:
            self.doc = _PdfDoc("latin")
        self.page_no = 0

    def _measure(self, s, size):
        if self.doc.mode == "unicode":
            tf = self.doc.font_reg
            _, total_advance = tf.shape_text(s)
            return total_advance * size / tf.units_per_em
        return len(str(s)) * size * 0.52

    def _draw_header(self):
        # logo + org name at very top - preserve aspect ratio, better fit (larger logo, PNG only)
        if self.doc._logo_raw is not None and self.doc._logo_h:
            h = 50
            w = h * self.doc._logo_w / self.doc._logo_h
            # ensure not too wide
            if w > 58:
                w = 58
                h = w * self.doc._logo_h / self.doc._logo_w
            self.doc.draw_logo(0, 2, w, h)
            self.doc.text(w + 8, 15, 9, ORG_NAME, bold=True, gray=0.15)
            self.doc.text(w + 8, 24, 7.5, ORG_SUBTITLE, gray=0.45)
        else:
            self.doc.text(0, 14, 9, ORG_NAME, bold=True, gray=0.15)
            self.doc.text(0, 24, 7.5, ORG_SUBTITLE, gray=0.45)

    def _draw_footer(self):
        self.doc.line(0, PAGE_H - 22, PAGE_W - 2 * _MARGIN, PAGE_H - 22, gray=0.75)
        self.doc.text(PAGE_W - 2 * _MARGIN - 70, PAGE_H - 14, 7, f"Page {self.page_no}", gray=0.45)

    def _start_page(self, first=False):
        self.doc._new_page()
        self.page_no += 1
        self._draw_header()
        if first:
            self.doc.text(0, 62, 11, f"Abroad Visit Report - Year {self.rep['year']}", bold=True)
            meta = (f"Generated: {_fmt_generated(self.rep['generated'])}    Total employees: {self.rep['total_employees']}    "
                    f"Reached yearly limit: {self.rep['blocked_count']}")
            self.doc.text(0, 72, 8, meta, gray=0.35)
            self.doc.line(0, 80, PAGE_W - 2 * _MARGIN, 82)
            self.y = 96
        else:
            self.doc.text(0, 60, 9, f"Abroad Visit Report - Year {self.rep['year']} (continued)", bold=True)
            self.doc.line(0, 66, PAGE_W - 2 * _MARGIN, 66)
            self.y = 78
        self._draw_footer()

    def _ensure(self, needed):
        if self.y + needed > PAGE_H - _BOTTOM:
            self._start_page(False)

    def _section_title(self, title):
        self._ensure(30)
        self.doc.text(0, self.y + 10, 10.5, title, bold=True)
        self.y += 18

    def _table_header(self, cols):
        h = self.LEAD + 8
        widths = [w for _, w in cols]
        self.doc.rect_fill(0, self.y, sum(widths), h, gray=0.88)
        x = 0
        for (label, w) in cols:
            self.doc.text(x + 4, self.y + 14, self.HEAD, label, bold=True)
            x += w
        self.y += h
        self.doc.line(0, self.y, sum(widths), self.y, gray=0.5)

    def _draw_row(self, cols, cells, bold=False, shade=False):
        size = self.BODY
        wraps = [_wrap_pdf(c, w - 8, self._measure, size) for c, (_, w) in zip(cells, cols)]
        lines = max(len(wl) for wl in wraps)
        rh = lines * self.LEAD + 8
        self._ensure(rh + 6)
        total_w = sum(w for _, w in cols)
        if shade:
            self.doc.rect_fill(0, self.y, total_w, rh, gray=0.93)
        ty = self.y + 4 + size
        x_offsets = []
        acc = 0
        for _, w in cols:
            x_offsets.append(acc)
            acc += w
        for wl, xo, (label, w) in zip(wraps, x_offsets, cols):
            tyy = ty
            for ln in wl:
                self.doc.text(xo + 4, tyy, size, ln, bold=bold)
                tyy += self.LEAD
        self.y += rh
        self.doc.line(0, self.y, total_w, self.y, gray=0.85)

    def write(self):
        self._start_page(first=True)
        self._section_title("YEARLY EMPLOYEE STATISTICS")
        self._table_header(self.COLS_SUMMARY)
        for s in self.rep["stats"]:
            row = [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
                   s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]
            bold = s["status"].startswith("MAX REACHED")
            self._draw_row(self.COLS_SUMMARY, [str(c) for c in row], bold=bold)
        self.y += 14
        self._section_title("VISIT DETAILS")
        for d in self.rep["details"]:
            group = f'{d["name"]} ({d["emp_id"]}) - {d["designation"]}'
            if d.get("project"):
                group += f' - Project: {d["project"]}'
            if not d["visits"]:
                group += f'  (no visits recorded in {self.rep["year"]})'
            self._ensure(40)
            self.doc.text(0, self.y + 10, 9, group, bold=True)
            self.y += 17
            self._table_header_detail()
            for v in d["visits"]:
                self._draw_row(self.COLS_DETAIL, [_fmt_date(v["date"]), v["country"], v["title"], v["detail"]])
            self.y += 10

    def _table_header_detail(self):
        self._table_header(self.COLS_DETAIL)

    def save(self, path):
        self.write()
        self.doc.save(path)


def export_pdf(path, year):
    rep = build_report(year)
    _PdfReport(rep).save(path)


class _TenurePdfReport:
    COLS_SUMMARY = [("ID", 52), ("Name", 110), ("Designation", 105), ("# Periods", 58), ("Total Days", 62),
                    ("Total Time", 68), ("First Join", 78), ("Last Release", 84), ("Status", 70)]
    COLS_DETAIL = [("Join", 70), ("Release", 78), ("Duration", 70), ("Days", 38), ("Type", 62),
                   ("Project", 90), ("Collab Org", 92), ("Role", 85), ("Notes", 105)]
    BODY = 7.5
    HEAD = 7.5
    LEAD = 10

    def __init__(self, rep):
        self.rep = rep
        needed = set()
        for s in rep["summaries"]:
            for v in s.values():
                needed.update(ord(c) for c in str(v))
        for d in rep["details"]:
            for v in (d["name"], d["designation"]):
                needed.update(ord(c) for c in str(v))
            for iv in d["intervals"]:
                for v in iv.values():
                    needed.update(ord(c) for c in str(v))
        def needs_unicode():
            for cp in needed:
                try:
                    chr(cp).encode("cp1252")
                except UnicodeEncodeError:
                    return True
            return False
        font_reg, font_bold = _pick_unicode_fonts(needed)
        if needs_unicode() and font_reg is not None:
            self.doc = _PdfDoc("unicode", font_reg, font_bold)
        else:
            self.doc = _PdfDoc("latin")
        self.page_no = 0

    def _measure(self, s, size):
        if self.doc.mode == "unicode":
            tf = self.doc.font_reg
            _, total_advance = tf.shape_text(s)
            return total_advance * size / tf.units_per_em
        return len(str(s)) * size * 0.52

    def _draw_header(self):
        if self.doc._logo_raw is not None and self.doc._logo_h:
            h = 42
            w = h * self.doc._logo_w / self.doc._logo_h
            if w > 50:
                w = 50
                h = w * self.doc._logo_h / self.doc._logo_w
            self.doc.draw_logo(0, 3, w, h)
            self.doc.text(w + 8, 14, 9, ORG_NAME, bold=True, gray=0.15)
            self.doc.text(w + 8, 24, 7.5, ORG_SUBTITLE, gray=0.45)
        else:
            self.doc.text(0, 14, 9, ORG_NAME, bold=True, gray=0.15)
            self.doc.text(0, 24, 7.5, ORG_SUBTITLE, gray=0.45)

    def _draw_footer(self):
        self.doc.line(0, PAGE_H - 22, PAGE_W - 2 * _MARGIN, PAGE_H - 22, gray=0.75)
        self.doc.text(PAGE_W - 2 * _MARGIN - 70, PAGE_H - 14, 7, f"Page {self.page_no}", gray=0.45)

    def _start_page(self, first=False):
        self.doc._new_page()
        self.page_no += 1
        self._draw_header()
        if first:
            self.doc.text(0, 62, 13, f"Tenure Report - As of {_fmt_date(self.rep['today'])}", bold=True)
            meta = (f"Generated: {_fmt_generated(self.rep['generated'])}    Total employees: {self.rep['total_employees']}    "
                    f"Active: {self.rep['active_count']}    Released: {self.rep['released_count']}")
            self.doc.text(0, 72, 8, meta, gray=0.35)
            self.doc.line(0, 80, PAGE_W - 2 * _MARGIN, 82)
            self.y = 96
        else:
            self.doc.text(0, 60, 9, f"Tenure Report - As of {_fmt_date(self.rep['today'])} (continued)", bold=True)
            self.doc.line(0, 66, PAGE_W - 2 * _MARGIN, 66)
            self.y = 78
        self._draw_footer()

    def _ensure(self, needed):
        if self.y + needed > PAGE_H - _BOTTOM:
            self._start_page(False)

    def _section_title(self, title):
        self._ensure(30)
        self.doc.text(0, self.y + 10, 10.5, title, bold=True)
        self.y += 18

    def _table_header(self, cols):
        h = self.LEAD + 7
        widths = [w for _, w in cols]
        self.doc.rect_fill(0, self.y, sum(widths), h, gray=0.88)
        x = 0
        for (label, w) in cols:
            self.doc.text(x + 4, self.y + 13, self.HEAD, label, bold=True)
            x += w
        self.y += h
        self.doc.line(0, self.y, sum(widths), self.y, gray=0.5)

    def _draw_row(self, cols, cells, bold=False, shade=False):
        size = self.BODY
        wraps = [_wrap_pdf(c, w - 8, self._measure, size) for c, (_, w) in zip(cells, cols)]
        lines = max(len(wl) for wl in wraps)
        rh = lines * self.LEAD + 7
        self._ensure(rh + 6)
        total_w = sum(w for _, w in cols)
        if shade:
            self.doc.rect_fill(0, self.y, total_w, rh, gray=0.93)
        ty = self.y + 4 + size
        x_offsets = []
        acc = 0
        for _, w in cols:
            x_offsets.append(acc)
            acc += w
        for wl, xo, (label, w) in zip(wraps, x_offsets, cols):
            tyy = ty
            for ln in wl:
                self.doc.text(xo + 4, tyy, size, ln, bold=bold)
                tyy += self.LEAD
        self.y += rh
        self.doc.line(0, self.y, total_w, self.y, gray=0.85)

    def write(self):
        self._start_page(first=True)
        self._section_title("TENURE SUMMARY (per employee)")
        self._table_header(self.COLS_SUMMARY)
        for s in self.rep["summaries"]:
            row = [s["emp_id"], s["name"], s["designation"],
                   str(s["interval_count"]), str(s["total_days"]), s["total_formatted"],
                   _fmt_date(s["first_join"]) if s["first_join"] else "-",
                   _fmt_date(s["last_release"]) if s["last_release"] else "Ongoing",
                   "Active" if s["is_currently_active"] else "Released"]
            bold = s["is_currently_active"]
            self._draw_row(self.COLS_SUMMARY, row, bold=bold)
        self.y += 12
        self._section_title("TENURE DETAILS (intervals)")
        for d in self.rep["details"]:
            group = f'{d["name"]} ({d["emp_id"]}) - {d["designation"]}   Total: {d["total_formatted"]} ({d["total_days"]} days)  Status: {d["status"]}'
            self._ensure(30)
            self.doc.text(0, self.y + 9, 8.5, group, bold=True)
            self.y += 16
            self._table_header(self.COLS_DETAIL)
            if not d["intervals"]:
                self._draw_row(self.COLS_DETAIL, ["(no tenure recorded)", "", "", "", "", "", "", "", ""])
            else:
                for iv in d["intervals"]:
                    self._draw_row(self.COLS_DETAIL,
                                   [_fmt_date(iv["join"]), _fmt_date(iv["release"]), iv["duration"], str(iv["days"]),
                                    iv["type"], iv["project"], iv["org"], iv["role"], iv["notes"]])
            self.y += 8

    def save(self, path):
        self.write()
        self.doc.save(path)


def export_tenure_pdf(path, today=None):
    rep = build_tenure_report(today)
    _TenurePdfReport(rep).save(path)
