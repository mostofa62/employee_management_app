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
ORG_SUBTITLE = "Employee Information & Visit Tracker System"
FOOTER_COURTESY = "Courtesy: This software is developed by Golam Mostofa, Computer Programmer, Chittagong University"
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
GOVT_LOGO_SVG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bangladesh-govt-logo.svg")

_GOVT_PNG_CACHE = {}


def _get_govt_logo_png(target_h_px=120):
    """Render bangladesh-govt-logo.svg to PNG bytes at `target_h_px` height.

    Returns (png_bytes, w, h) composited over white, or None. Results are
    cached in memory; the SVG is only re-rendered per process once per size.
    """
    key = int(target_h_px)
    if key in _GOVT_PNG_CACHE:
        return _GOVT_PNG_CACHE[key]
    if not os.path.isfile(GOVT_LOGO_SVG):
        return None
    try:
        import pymupdf
        from PIL import Image as _PILImage
        import io
        doc = pymupdf.open(GOVT_LOGO_SVG)
        page = doc[0]
        rect = page.rect
        if rect.height <= 0:
            return None
        scale = float(target_h_px) / rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        mode = "RGBA" if pix.alpha else "RGB"
        im = _PILImage.frombytes(mode, (pix.width, pix.height), pix.samples)
        if im.mode == "RGBA":
            bg = _PILImage.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        out = (buf.getvalue(), im.size[0], im.size[1])
        _GOVT_PNG_CACHE[key] = out
        return out
    except Exception:
        return None


def _get_govt_logo_path(target_h_px=36):
    """Disk-cached PNG path of the govt logo for openpyxl (needs a filename)."""
    import tempfile
    info = _get_govt_logo_png(target_h_px)
    if info is None:
        return None
    png_data, _, _ = info
    cache = os.path.join(tempfile.gettempdir(), f"iedcr_govt_logo_h{int(target_h_px)}.png")
    try:
        if not os.path.isfile(cache) or os.path.getsize(cache) != len(png_data):
            with open(cache, "wb") as f:
                f.write(png_data)
    except Exception:
        return None
    return cache

def _export_xlsx_via_openpyxl(path, rep, sheet_title, headers_list, rows_fn):
    """Try openpyxl for trusted, fully compliant XLSX with logo. Returns True if success."""
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    except ImportError:
        return False
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_title[:31]
        wb.properties.creator = "IEDCR"
        wb.properties.lastModifiedBy = "IEDCR"
        logo_added = False
        if os.path.isfile(LOGO_PATH) and LOGO_PATH.lower().endswith(".png"):
            try:
                from openpyxl.drawing.image import Image as XLImage
                from PIL import Image as _PIL
                pil = _PIL.open(LOGO_PATH)
                w, h = pil.size
                target_h = 36
                scale = target_h / h if h else 1
                img = XLImage(LOGO_PATH)
                img.width = int(w * scale)
                img.height = target_h
                ws.add_image(img, "A1")
                logo_added = True
                ws.row_dimensions[1].height = 28
                ws.row_dimensions[2].height = 14
            except Exception:
                logo_added = False
        title_font = Font(name="Calibri", size=15, bold=True, color="006633")
        subtitle_font = Font(name="Calibri", size=11, color="444444")
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        try:
            ws.page_setup.orientation = "landscape"
            ws.page_setup.paperSize = ws.PAPERSIZE_A3
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
        except: pass
        r = 1
        if logo_added:
            ws["B1"] = "Institute of Epidemiology, Disease Control and Research (IEDCR)"
            ws["B1"].font = title_font
            ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=6)
            ws["B2"] = "Employee Information & Visit Tracker System"
            ws["B2"].font = subtitle_font
            ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=6)
            r = 3
        else:
            ws[f"A{r}"] = "Institute of Epidemiology, Disease Control and Research (IEDCR)"
            ws[f"A{r}"].font = title_font
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            r+=1
            ws[f"A{r}"] = "Employee Information & Visit Tracker System"
            ws[f"A{r}"].font = subtitle_font
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            r+=1
        rows_fn(ws, r)
        for col in ws.columns:
            max_len=0
            col_letter=col[0].column_letter
            for cell in col:
                try:
                    v=str(cell.value) if cell.value is not None else ""
                    max_len=max(max_len, min(len(v),45))
                except: pass
            try: ws.column_dimensions[col_letter].width=max(12, min(max_len*1.15+2,50))
            except: pass
        try: ws.freeze_panes="A7"
        except: pass
        wb.save(path)
        return True
    except Exception as e:
        # import traceback; traceback.print_exc()
        return False


def _fmt_date(iso):
    """2026-01-26 -> 26 Jan 2026, keeps Ongoing/- as is."""
    if not iso or iso in ("Ongoing", "-", ""):
        return str(iso) if iso else ""
    s = str(iso).strip()
    if not s or s.lower() == "ongoing":
        return s
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d %b %Y")
        except Exception:
            return s


def _fmt_datetime(date_iso, time_iso):
    """'2026-01-26','09:00' -> '26 Jan 2026 at 09:00 AM'. Keeps date-only if no time."""
    d = _fmt_date(date_iso)
    if not d or not time_iso or time_iso in ("", "-"):
        return d
    try:
        t = datetime.strptime(str(time_iso).strip(), "%H:%M")
        ap = t.strftime("%p").upper()  # AM/PM
        th = t.strftime("%I:%M")      # 09:00 or 12:00
        return f"{d} at {th} {ap}"
    except Exception:
        return d

def _fmt_12(time_iso):
    """'10:20' -> '10:20 AM', '' -> ''."""
    if not time_iso or str(time_iso).strip() in ("", "-"):
        return ""
    try:
        t = datetime.strptime(str(time_iso).strip(), "%H:%M")
        return f"{t.strftime('%I:%M')} {t.strftime('%p').upper()}"
    except Exception:
        return str(time_iso).strip()


def _fmt_slot(date_iso, start_time="", end_time="", is_full_day=False, note=""):
    """Full-format single-day slot, e.g. '10 Jan 2026 at 10:20 AM - 12:30 PM'.
    No times -> bare date (off days get ' (off)'). No '(full day)' text."""
    d = _fmt_date(date_iso)
    if not d:
        return ""
    s12, e12 = _fmt_12(start_time), _fmt_12(end_time)
    if s12 and e12:
        txt = f"{d} at {s12} - {e12}"
    elif s12:
        txt = f"{d} at {s12}"
    elif e12:
        txt = f"{d} at {e12}"
    else:
        txt = f"{d}" if not is_full_day else d
    if note and str(note).strip():
        txt += f" ({str(note).strip()})"
    return txt


def _pdf_time_groups(time_txt):
    """Split 'DATE at TIME' slot lines into [DATE, 'at TIME'] group line pairs
    so narrow PDF columns keep each day's date+time together instead of
    word-wrapping mid-slot."""
    groups = []
    for line in str(time_txt or "").split("\n"):
        seg = line.strip()
        if not seg:
            continue
        if " at " in seg:
            date_part, time_part = seg.split(" at ", 1)
            groups.append(date_part.strip())
            groups.append("at " + time_part.strip())
        else:
            groups.append(seg)
    return "\n".join(groups)


def _fmt_period(start_iso, end_iso):
    """Always include the range: '10 Jan 2026' or '10 Jan 2026 to 12 Jan 2026'."""
    s = _fmt_date(start_iso)
    e = _fmt_date(end_iso or start_iso)
    if not s:
        return e
    if not e or s == e:
        return s
    return f"{s} to {e}"


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

def _openpyxl_header_logos(ws, XLImage, right_col="G", target_h=44):
    """Embed IEDCR logo at A1 (left) and govt logo at `<right_col>1` (right).

    Returns True if the left logo was embedded.
    Row heights are set snug around the two-line banner.
    """
    from PIL import Image as _PIL
    logo_ok = False
    if os.path.isfile(LOGO_PATH) and LOGO_PATH.lower().endswith(".png"):
        try:
            pil = _PIL.open(LOGO_PATH)
            w, h = pil.size
            scale = target_h / h if h else 1
            img = XLImage(LOGO_PATH)
            img.width = int(w * scale)
            img.height = target_h
            ws.add_image(img, "A1")
            logo_ok = True
        except Exception:
            logo_ok = False
    govt_path = _get_govt_logo_path(target_h)
    if govt_path:
        try:
            gimg = XLImage(govt_path)
            gimg.width = target_h
            gimg.height = target_h
            ws.add_image(gimg, f"{right_col}1")
        except Exception:
            pass
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 16
    return logo_ok


def _frame_row(ws, row, min_col, max_col, color="006633"):
    """Draw an outer frame (no interior lines) around one header row."""
    from openpyxl.styles import Border, Side
    edge = Side(style="thin", color=color)
    blank = Side()
    for ci in range(min_col, max_col + 1):
        ws.cell(row=row, column=ci).border = Border(
            left=edge if ci == min_col else blank,
            right=edge if ci == max_col else blank,
            top=edge, bottom=edge)


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

def _get_govt_excel_logo(target_h_px=36):
    """Return (png_bytes, cx_emu, cy_emu) for the govt logo, or None."""
    info = _get_govt_logo_png(target_h_px)
    if info is None:
        return None
    png_data, w, h = info
    # EMU: 1 inch = 914400, 96 dpi => 1 px = 9525
    return png_data, w * 9525, h * 9525


def _excel_drawing_xml(pics):
    """Build drawing XML for embedded header images.

    pics: list of dicts with keys col, row, cx, cy, rid, name.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    ]
    for p in pics:
        parts.append(
            '<xdr:oneCellAnchor>'
            f'<xdr:from><xdr:col>{p["col"]}</xdr:col><xdr:colOff>0</xdr:colOff>'
            f'<xdr:row>{p["row"]}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
            f'<xdr:ext cx="{p["cx"]}" cy="{p["cy"]}"/>'
            '<xdr:pic>'
            f'<xdr:nvPicPr><xdr:cNvPr id="{p["rid"]}" name="{p["name"]}"/><xdr:cNvPicPr/></xdr:nvPicPr>'
            f'<xdr:blipFill><a:blip r:embed="rId{p["rid"]}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            '<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{p["cx"]}" cy="{p["cy"]}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic><xdr:clientData/>'
            '</xdr:oneCellAnchor>'
        )
    parts.append('</xdr:wsDr>')
    return "".join(parts)


def _excel_pics_and_media(logo_info, govt_col=6):
    """Combine left logo + right govt logo into drawing pics + media files.

    Returns (pics, media) where pics feeds _excel_drawing_xml and media is a
    list of (part_name, png_bytes) for the ZIP.
    """
    pics, media = [], []
    if logo_info:
        png_data, cx, cy = logo_info
        pics.append({"col": 0, "row": 0, "cx": cx, "cy": cy, "rid": 1, "name": "Logo"})
        media.append(("xl/media/image1.png", png_data))
    govt_info = _get_govt_excel_logo()
    if govt_info:
        gpng, gcx, gcy = govt_info
        rid = len(pics) + 1
        pics.append({"col": govt_col, "row": 0, "cx": gcx, "cy": gcy,
                     "rid": rid, "name": "GovtLogo"})
        media.append((f"xl/media/image{rid}.png", gpng))
    return pics, media

def _excel_drawing_rels(count):
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="../media/image{i}.png"/>'
        for i in range(1, count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + rels +
        '</Relationships>'
    )


# Centered variants for the hand-rolled XLSX styles: {plain_style: centered_style}.
# xf3 = centered bold-11, xf4 = centered bold-15, xf5 = centered normal-11.
_FALLBACK_CENTER_STYLE = {None: 5, 1: 3, 2: 4}


def _status_fields(limit, used, times_reached):
    if limit == 0:
        return "Unlimited", "Unlimited visits", "-"
    if used >= limit:
        return "0", "MAX REACHED - blocked", str(times_reached)
    return str(limit - used), f"{limit - used} visit(s) left", str(times_reached)


def build_report(year):
    year = str(year)
    # Statistics are segmented by visit type. The yearly limit (and MAX
    # REACHED blocking) applies to Abroad visits only; Local visits are
    # unlimited and tracked as plain counts. Visit details stay combined.
    abroad_stats = []
    local_stats = []
    for r in db.summary(year):
        limit = r["max_visits"]
        used = r["used_this_year"]
        local = r["local_this_year"] if "local_this_year" in r.keys() else 0
        base = {
            "emp_id": r["emp_id"],
            "name": r["name"],
            "designation": r["designation"],
            "emp_type": r["emp_type"] or "",
            "project": r["project_name"] or "",
        }
        if used > 0:
            remaining, status, reached = _status_fields(limit, used, r["times_max_reached"])
            abroad_stats.append({
                **base,
                "limit_txt": "Unlimited" if limit == 0 else str(limit),
                "used": used,
                "remaining": remaining,
                "status": status,
                "reached": reached,
            })
        if local > 0:
            local_stats.append({**base, "local": local})
    def _visit_dict(v):
        s = v["start_date"] or v["visit_date"]
        e = v["end_date"] or s
        # Period always carries the full range when From/To differ.
        period = _fmt_period(s, e)
        try:
            mode = v["time_mode"] if "time_mode" in v.keys() else "fixed"
        except Exception:
            mode = "fixed"
        base_detail = (v["purpose_detail"] or "").strip()
        if (mode or "fixed") == "per_day":
            try:
                day_rows = [dict(r) for r in db.list_visit_days(v["id"])]
            except Exception:
                day_rows = []
            # Time column: date + time slots only, one day per line, no notes.
            time_val = "\n".join(
                _fmt_slot(d["day_date"], d["start_time"], d["end_time"],
                          bool(d["is_full_day"]))
                for d in day_rows
            )
            # Purpose Details: user purpose first, then an <hr>-style rule,
            # then each day line by line with its time slot + note.
            day_lines = [
                _fmt_slot(d["day_date"], d["start_time"], d["end_time"],
                          bool(d["is_full_day"]), d["note"])
                for d in day_rows
            ]
            if base_detail and day_lines:
                detail = base_detail + "\n" + DAYWISE_SEP + "\n" + "\n".join(day_lines)
            elif day_lines:
                detail = "\n".join(day_lines)
            else:
                detail = base_detail
        else:
            # Same slot every day: present it once in full format.
            time_val = _fmt_slot(s, v["start_time"], v["end_time"],
                                 bool(v["is_full_day"]))
            detail = base_detail
        return {
            "date": s,
            "period": period,
            "time": time_val,
            "visit_type": v["visit_type"] or "Abroad",
            "country": v["country"] or v["location"] or "",
            "destination": (v["country"] if (v["visit_type"] or "Abroad") == "Abroad"
                            else (v["location"] or "")),
            "purpose": v["purpose_name"] or "",
            "title": v["purpose_title"],
            "detail": detail,
        }

    details = []
    for emp in abroad_stats + [s for s in local_stats
                               if s["emp_id"] not in {a["emp_id"] for a in abroad_stats}]:
        visits = db.list_visits(year=year, emp_id=emp["emp_id"])
        # skip if no visits (should not happen after filter, but safe)
        if not visits:
            continue
        details.append({
            "emp_id": emp["emp_id"],
            "name": emp["name"],
            "designation": emp["designation"],
            "visits": [_visit_dict(v) for v in visits],
        })
    blocked = sum(1 for s in abroad_stats if s["status"].startswith("MAX REACHED"))
    return {
        "year": year,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_employees": len({s["emp_id"] for s in abroad_stats + local_stats}),
        "blocked_count": blocked,
        "abroad_stats": abroad_stats,
        "local_stats": local_stats,
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


# Abroad statistics carry the yearly limit; Local statistics are plain counts.
ABROAD_SUMMARY_HEADERS = ["ID", "Name", "Designation", "Type", "Project", "Yearly Limit",
                          "Abroad Used", "Remaining", "Status", "Times Max Reached"]
LOCAL_SUMMARY_HEADERS = ["ID", "Name", "Designation", "Type", "Project", "Local Visits"]
DETAIL_HEADERS = ["Employee ID", "Employee Name", "Type", "Period", "Time",
                  "Destination", "Purpose", "Purpose Title", "Purpose Details"]

# Horizontal-rule style separator between the user's purpose text and the
# day-by-day time slots in Purpose Details (plain-text <hr> equivalent).
DAYWISE_SEP = "-" * 40

TENURE_SUMMARY_HEADERS = ["ID", "Name", "Designation", "# Periods", "Total Days", "Total Time",
                          "First Join", "Last Release", "Current Status"]
TENURE_DETAIL_HEADERS = ["Employee ID", "Employee Name", "Join Date", "Release Date",
                         "Duration", "Days", "Type", "Project", "Collaborator Org", "Role", "Notes"]


def _abroad_summary_row(s):
    return [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
            s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]


def _local_summary_row(s):
    return [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
            s["local"]]


def _detail_row(d, v):
    return [d["emp_id"], d["name"], v.get("visit_type", "Abroad"), v.get("period", v.get("date", "")),
            v.get("time", ""), v.get("destination", v.get("country", "")),
            v.get("purpose", ""), v.get("title", ""), v.get("detail", "")]


def _clean_xml(text):
    return _xml_escape(_ILLEGAL_XML.sub("", str(text)))


def _col_letter(n):
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _disp_len(value):
    # Multi-line cells: width tracks the longest line, not the total.
    best = 0
    for line in _ILLEGAL_XML.sub("", str(value)).split("\n"):
        width = 0.0
        for ch in line:
            code = ord(ch)
            if code > 0x1100:
                width += 2
            elif code > 126:
                width += 1.7
            else:
                width += 1
        best = max(best, int(round(width)))
    return best


def export_xlsx(path, year):
    rep = build_report(year)
    # --- Trusted openpyxl path (avoids "We found a problem with some content" / Protected View trust warning) ---
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as _PIL
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Visit Report {rep['year']}"[:31]
        wb.properties.creator = ORG_NAME
        wb.properties.lastModifiedBy = ORG_NAME
        # Header: left logo, right govt logo, org text centered between them.
        logo_ok = _openpyxl_header_logos(ws, XLImage, right_col="G")
        # Styles
        title_font = Font(name="Calibri", size=15, bold=True, color="006633")
        subtitle_font = Font(name="Calibri", size=11, color="444444")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="006633", end_color="006633", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        normal_font = Font(name="Calibri", size=11)
        bold_font = Font(name="Calibri", size=11, bold=True)
        thin_border = Border(left=Side(style="thin", color="D9D9D9"), right=Side(style="thin", color="D9D9D9"), top=Side(style="thin", color="D9D9D9"), bottom=Side(style="thin", color="D9D9D9"))
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
        r = 1
        # Single banner band: left logo + org text + right govt logo in one band.
        # centerContinuous spans the text across the empty framed cells.
        span_align = Alignment(horizontal="centerContinuous", vertical="center")
        ws["B1"] = ORG_NAME
        ws["B1"].font = title_font
        ws["B1"].alignment = span_align
        ws["B2"] = ORG_SUBTITLE
        ws["B2"].font = subtitle_font
        ws["B2"].alignment = span_align
        _frame_row(ws, 1, 1, 7)
        _frame_row(ws, 2, 1, 7)
        r = 3
        ws[f"A{r}"] = f"Employee Visit Report - Year {rep['year']}"

        ws[f"A{r}"].font = Font(name="Calibri", size=13, bold=True, color="006633")
        ws[f"A{r}"].alignment = span_align
        _frame_row(ws, r, 1, 10)
        r+=1
        ws[f"A{r}"] = f"Generated: {rep['generated']}    Total employees: {rep['total_employees']}    Reached yearly limit: {rep['blocked_count']}"
        ws[f"A{r}"].font = Font(name="Calibri", size=9, color="666666")
        ws[f"A{r}"].alignment = span_align
        _frame_row(ws, r, 1, 10)
        r+=1
        def _stats_table(title, headers, rows, center_cols, bold_rows=()):
            nonlocal r
            ws[f"A{r}"] = title
            ws[f"A{r}"].font = Font(name="Calibri", size=11, bold=True, color="006633")
            r+=1
            for ci, h in enumerate(headers, start=1):
                c = ws.cell(row=r, column=ci, value=h)
                c.font = header_font
                c.fill = header_fill
                c.alignment = header_align
                c.border = thin_border
            r+=1
            for ri, row in enumerate(rows):
                for ci, v in enumerate(row, start=1):
                    c = ws.cell(row=r, column=ci, value=v)
                    c.font = bold_font if ri in bold_rows else normal_font
                    c.alignment = center_align if ci in center_cols else left_align
                    c.border = thin_border
                r+=1
            r+=1
        _stats_table("YEARLY ABROAD STATISTICS (yearly limit applies)",
                     ABROAD_SUMMARY_HEADERS,
                     [_abroad_summary_row(s) for s in rep["abroad_stats"]],
                     center_cols=(6, 7, 8, 10),
                     bold_rows={i for i, s in enumerate(rep["abroad_stats"])
                                if s["status"].startswith("MAX REACHED")})
        _stats_table("YEARLY LOCAL STATISTICS (no limit)",
                     LOCAL_SUMMARY_HEADERS,
                     [_local_summary_row(s) for s in rep["local_stats"]],
                     center_cols=(6,))
        ws[f"A{r}"] = "VISIT DETAILS"
        ws[f"A{r}"].font = Font(name="Calibri", size=11, bold=True, color="006633")
        r+=1
        for ci, h in enumerate(DETAIL_HEADERS, start=1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font = header_font
            c.fill = header_fill
            c.alignment = header_align
            c.border = thin_border
        r+=1
        for d in rep["details"]:
            if not d["visits"]:
                ws.cell(row=r, column=1, value=d["emp_id"]).font = normal_font
                ws.cell(row=r, column=2, value=d["name"]).font = normal_font
                ws.cell(row=r, column=3, value=f"(no visits in {year})").font = normal_font
                r+=1
                continue
            for v in d["visits"]:
                vals = _detail_row(d, v)
                for ci, val in enumerate(vals, start=1):
                    c = ws.cell(row=r, column=ci, value=val)
                    c.font = normal_font
                    c.alignment = left_align
                    c.border = thin_border
                    c.alignment = Alignment(wrap_text=True, vertical="center")
                r+=1
        # Auto-fit widths
        # Auto-fit widths: handle MergedCell (no column_letter) by using column index
        try:
            from openpyxl.utils import get_column_letter as _get_col_letter
            for ci in range(1, ws.max_column + 1):
                col_letter = _get_col_letter(ci)
                max_len = 0
                for row in ws.iter_rows(min_col=ci, max_col=ci):
                    for cell in row:
                        try:
                            # skip merged placeholder cells that are MergedCell without value
                            v = str(cell.value) if cell.value is not None else ""
                            # ignore empty merged cells
                            if v:
                                max_len = max(max_len, min(len(v), 45))
                        except: pass
                try:
                    ws.column_dimensions[col_letter].width = max(12, min(max_len*1.12+2, 50))
                except: pass
        except Exception:
            pass
        try:
            ws.freeze_panes = "A7"
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            ws.page_setup.orientation = "landscape"
            ws.page_setup.paperSize = ws.PAPERSIZE_A3
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.print_title_rows = "6:6"
        except: pass
        wb.save(path)
        return
    except ImportError:
        pass
    except Exception as e:
        pass
    logo_info = _get_excel_logo()
    pics, pics_media = _excel_pics_and_media(logo_info)

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

    # Header: left logo in A, right govt logo, org text centered between them.
    _C = _FALLBACK_CENTER_STYLE.get
    if logo_info:
        add_row(["", ORG_NAME], style=_C(2), fit=False)
        add_row(["", ORG_SUBTITLE], style=_C(1), fit=False)
    else:
        add_row([ORG_NAME], style=_C(2), fit=False)
        add_row([ORG_SUBTITLE], style=_C(1), fit=False)
    add_row([f"Employee Visit Report - Year {rep['year']}"], style=_C(2), fit=False)
    add_row([f"Generated: {rep['generated']}",
             f"Total employees: {rep['total_employees']}",
             f"Reached yearly limit: {rep['blocked_count']}"], style=_C(None), fit=False)
    add_row(["YEARLY ABROAD STATISTICS (yearly limit applies)"], style=1, fit=False)
    add_row(ABROAD_SUMMARY_HEADERS, style=1)
    for s in rep["abroad_stats"]:
        add_row(_abroad_summary_row(s), numeric_cols=(5,))
    add_row([""])
    add_row(["YEARLY LOCAL STATISTICS (no limit)"], style=1, fit=False)
    add_row(LOCAL_SUMMARY_HEADERS, style=1)
    for s in rep["local_stats"]:
        add_row(_local_summary_row(s))
    add_row([""])
    add_row(["VISIT DETAILS"], style=1, fit=False)
    add_row(DETAIL_HEADERS, style=1)
    for d in rep["details"]:
        if not d["visits"]:
            add_row([d["emp_id"], d["name"], f"(no visits recorded in {year})", "", "", "", "", "", ""])
            continue
        for v in d["visits"]:
            add_row(_detail_row(d, v))

    cols_xml = "".join(
        f'<col min="{ci}" max="{ci}" width="{min(max(ln * 1.1 + 2, 9), 55):.2f}" customWidth="1"/>'
        for ci, ln in sorted(width_map.items())
    )
    cols_xml = f"<cols>{cols_xml}</cols>" if cols_xml else ""
    if pics:
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
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    if pics:
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
        if pics:
            for part_name, png_data in pics_media:
                z.writestr(part_name, png_data)
            z.writestr("xl/drawings/drawing1.xml", _excel_drawing_xml(pics))
            z.writestr("xl/drawings/_rels/drawing1.xml.rels", _excel_drawing_rels(len(pics)))
            z.writestr("xl/worksheets/_rels/sheet1.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
                       '</Relationships>')


def export_tenure_xlsx(path, today=None):
    rep = build_tenure_report(today)
    # --- Trusted openpyxl path ---
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as _PIL
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tenure Report"[:31]
        wb.properties.creator = ORG_NAME
        wb.properties.lastModifiedBy = ORG_NAME
        logo_ok = _openpyxl_header_logos(ws, XLImage, right_col="G")
        title_font = Font(name="Calibri", size=15, bold=True, color="006633")
        subtitle_font = Font(name="Calibri", size=11, color="444444")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="006633", end_color="006633", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        normal_font = Font(name="Calibri", size=11)
        thin_border = Border(left=Side(style="thin", color="D9D9D9"), right=Side(style="thin", color="D9D9D9"), top=Side(style="thin", color="D9D9D9"), bottom=Side(style="thin", color="D9D9D9"))
        left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        r=1
        tenure_span = Alignment(horizontal="centerContinuous", vertical="center")
        r=1
        ws["B1"]=ORG_NAME; ws["B1"].font=title_font; ws["B1"].alignment=tenure_span
        ws["B2"]=ORG_SUBTITLE; ws["B2"].font=subtitle_font; ws["B2"].alignment=tenure_span
        _frame_row(ws, 1, 1, 7)
        _frame_row(ws, 2, 1, 7)
        r=3
        ws[f"A{r}"]=f"Tenure Report - As of {rep['today']}"; ws[f"A{r}"].font=Font(name="Calibri", size=13, bold=True, color="006633"); ws[f"A{r}"].alignment=tenure_span; _frame_row(ws, r, 1, 11); r+=1
        ws[f"A{r}"]=f"Generated: {rep['generated']}    Total employees: {rep['total_employees']}    Active: {rep['active_count']}    Released: {rep['released_count']}"; ws[f"A{r}"].font=Font(name="Calibri", size=9, color="666666"); ws[f"A{r}"].alignment=tenure_span; _frame_row(ws, r, 1, 11); r+=1
        ws[f"A{r}"]="TENURE SUMMARY (per employee)"; ws[f"A{r}"].font=Font(name="Calibri", size=11, bold=True, color="006633"); r+=1
        for ci,h in enumerate(TENURE_SUMMARY_HEADERS, start=1):
            c=ws.cell(row=r, column=ci, value=h); c.font=header_font; c.fill=header_fill; c.alignment=header_align; c.border=thin_border
        r+=1
        for s in rep["summaries"]:
            row=_tenure_summary_row(s)
            for ci,v in enumerate(row, start=1):
                c=ws.cell(row=r, column=ci, value=v); c.font=normal_font; c.alignment=center_align if ci in (4,5) else left_align; c.border=thin_border
            r+=1
        r+=1
        ws[f"A{r}"]="TENURE DETAILS (intervals)"; ws[f"A{r}"].font=Font(name="Calibri", size=11, bold=True, color="006633"); r+=1
        for ci,h in enumerate(TENURE_DETAIL_HEADERS, start=1):
            c=ws.cell(row=r, column=ci, value=h); c.font=header_font; c.fill=header_fill; c.alignment=header_align; c.border=thin_border
        r+=1
        for d in rep["details"]:
            if not d["intervals"]:
                ws.cell(row=r, column=1, value=d["emp_id"]).font=normal_font
                ws.cell(row=r, column=2, value=d["name"]).font=normal_font
                ws.cell(row=r, column=3, value="(no tenure)").font=normal_font
                r+=1; continue
            for iv in d["intervals"]:
                vals=[d["emp_id"], d["name"], iv["join"], iv["release"], iv["duration"], iv["days"], iv["type"], iv["project"], iv["org"], iv["role"], iv["notes"]]
                for ci,val in enumerate(vals, start=1):
                    c=ws.cell(row=r, column=ci, value=val); c.font=normal_font; c.border=thin_border; c.alignment=Alignment(wrap_text=True, vertical="center")
                r+=1
                # Auto-fit widths: handle MergedCell
        try:
            from openpyxl.utils import get_column_letter as _get_col_letter
            for ci in range(1, ws.max_column + 1):
                col_letter = _get_col_letter(ci)
                max_len = 0
                for row in ws.iter_rows(min_col=ci, max_col=ci):
                    for cell in row:
                        try:
                            v = str(cell.value) if cell.value is not None else ""
                            if v:
                                max_len = max(max_len, min(len(v), 45))
                        except: pass
                try:
                    ws.column_dimensions[col_letter].width = max(12, min(max_len*1.12+2, 50))
                except: pass
        except Exception:
            pass
        try:
            ws.freeze_panes="A7"; ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.page_setup.orientation="landscape"; ws.page_setup.paperSize=ws.PAPERSIZE_A3; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
        except: pass
        wb.save(path); return
    except ImportError:
        pass
    except Exception:
        pass
    logo_info = _get_excel_logo()
    pics, pics_media = _excel_pics_and_media(logo_info)
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
        # Header: left logo in A, right govt logo, org text centered between them.
    _C = _FALLBACK_CENTER_STYLE.get
    if logo_info:
        add_row(["", ORG_NAME], style=_C(2), fit=False)
        add_row(["", ORG_SUBTITLE], style=_C(1), fit=False)
    else:
        add_row([ORG_NAME], style=_C(2), fit=False)
        add_row([ORG_SUBTITLE], style=_C(1), fit=False)
    add_row([f"Tenure Report - As of {rep['today']}"], style=_C(2), fit=False)
    add_row([f"Generated: {rep['generated']}",
             f"Total employees: {rep['total_employees']}",
             f"Active: {rep['active_count']}",
             f"Released: {rep['released_count']}"], style=_C(None), fit=False)
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
    if pics:
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
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    if pics:
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
        if pics:
            for part_name, png_data in pics_media:
                z.writestr(part_name, png_data)
            z.writestr("xl/drawings/drawing1.xml", _excel_drawing_xml(pics))
            z.writestr("xl/drawings/_rels/drawing1.xml.rels", _excel_drawing_rels(len(pics)))
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
    # Preserve explicit line breaks so day-by-day slots render line by line.
    raw = _ILLEGAL_XML.sub("", str(text))
    out = []

    def fits(t):
        return measure(t, size) <= max_w

    for raw_line in raw.split("\n"):
        seg = raw_line.strip()
        if not seg:
            out.append("")
            continue
        cur = ""
        for word in seg.split():
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
        self._govt_raw = None
        self._govt_w = 0
        self._govt_h = 0
        self._govt_obj_id = None
        self._try_load_logo()
        self._try_load_govt()

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

    def _try_load_govt(self):
        # Right-side Bangladesh govt logo (SVG -> PNG, composited over white)
        try:
            info = _get_govt_logo_png(220)
            if info is None:
                return
            from PIL import Image as _PILImage
            import io
            png_data, _, _ = info
            im = _PILImage.open(io.BytesIO(png_data))
            if im.mode != "RGB":
                im = im.convert("RGB")
            max_px = 260
            if max(im.size) > max_px:
                im.thumbnail((max_px, max_px), _PILImage.Resampling.LANCZOS)
            self._govt_w, self._govt_h = im.size
            self._govt_raw = zlib.compress(im.tobytes())
        except Exception:
            self._govt_raw = None

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

    def rect_stroke(self, x_top, y_top, w, h, gray=0.5, width=0.8):
        x = _MARGIN + x_top
        y = PAGE_H - y_top - h
        self._ops.append(f"{gray} G {width} w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S 0 G")

    def draw_logo(self, x_top, y_top, w, h):
        if self._logo_raw is None:
            return
        x = _MARGIN + x_top
        y = PAGE_H - y_top - h
        self._ops.append(f"q {w:.1f} 0 0 {h:.1f} {x:.1f} {y:.1f} cm /ImLogo Do Q")

    def draw_govt(self, x_top, y_top, w, h):
        if self._govt_raw is None:
            return
        x = _MARGIN + x_top
        y = PAGE_H - y_top - h
        self._ops.append(f"q {w:.1f} 0 0 {h:.1f} {x:.1f} {y:.1f} cm /ImGovt Do Q")

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

    def _embed_govt(self, add_obj):
        if self._govt_raw is None:
            return None
        try:
            data = self._govt_raw
            obj = (
                f"<< /Type /XObject /Subtype /Image /Width {self._govt_w} /Height {self._govt_h} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"
            )
            _id = add_obj(obj)
            self._govt_obj_id = _id
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
        govt_id = None
        if self._govt_raw is not None:
            govt_id = self._embed_govt(add_obj)
        page_ids = []
        for ops in self.pages:
            stream = ("\n".join(ops)).encode("cp1252", "replace")
            content_id = add_obj(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
            xobj_part = ""
            if logo_id:
                xobj_part += f" /ImLogo {logo_id} 0 R"
            if govt_id:
                xobj_part += f" /ImGovt {govt_id} 0 R"
            if xobj_part:
                xobj_part = f" /XObject <<{xobj_part} >>"
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
    COLS_ABROAD_SUMMARY = [("ID", 55), ("Name", 125), ("Designation", 90), ("Type", 65), ("Project", 100),
                           ("Limit", 42), ("Used", 40), ("Remaining", 68), ("Status", 95), ("Times Max Reached", 90)]
    COLS_LOCAL_SUMMARY = [("ID", 60), ("Name", 150), ("Designation", 140), ("Type", 90),
                          ("Project", 200), ("Local Visits", 130)]
    COLS_DETAIL = [("Type", 45), ("Period", 135), ("Time", 105), ("Destination", 85),
                   ("Purpose", 60), ("Purpose Title", 120), ("Purpose Details", 220)]
    BODY = 8
    HEAD = 8.5
    LEAD = 11

    def __init__(self, rep):
        self.rep = rep
        needed = set()
        for s in rep["abroad_stats"] + rep["local_stats"]:
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

    def _centered(self, y_top, size, s, bold=False, gray=0):
        w = self._measure(s, size)
        x = max(0.0, (PAGE_W - 2 * _MARGIN - w) / 2.0)
        self.doc.text(x, y_top, size, s, bold=bold, gray=gray)

    def _draw_header(self):
        # Bordered header: both logos flank the centered org title.
        content_w = PAGE_W - 2 * _MARGIN
        title_w = self._measure(ORG_NAME, 9)
        lw = lh = gw = gh = 0
        if self.doc._logo_raw is not None and self.doc._logo_h:
            lh = 44
            lw = lh * self.doc._logo_w / self.doc._logo_h
            # ensure not too wide
            if lw > 54:
                lw = 54
                lh = lw * self.doc._logo_h / self.doc._logo_w
        if self.doc._govt_raw is not None and self.doc._govt_h:
            gh = 44
            gw = gh * self.doc._govt_w / self.doc._govt_h
        if lw and gw and title_w + lw + gw + 24 <= content_w:
            cx = (content_w - title_w) / 2.0
            left_x, govt_x = cx - 50 - lw, cx + title_w + 50
        else:  # fall back to both ends
            left_x, govt_x = 0, content_w - gw
        if lw:
            self.doc.draw_logo(left_x, 5, lw, lh)
        if gw:
            self.doc.draw_govt(govt_x, 5, gw, gh)
        self._centered(28, 12, ORG_NAME, bold=True, gray=0.15)
        self._centered(37, 8, ORG_SUBTITLE, gray=0.45)
        #self.doc.rect_stroke(0, 0, content_w, 48, gray=0.55)

    def _draw_footer(self):
        self.doc.line(0, PAGE_H - 22, PAGE_W - 2 * _MARGIN, PAGE_H - 22, gray=0.75)
        self.doc.text(PAGE_W - 2 * _MARGIN - 70, PAGE_H - 14, 7, f"Page {self.page_no}", gray=0.45)

    def _start_page(self, first=False):
        self.doc._new_page()
        self.page_no += 1
        self._draw_header()
        if first:
            self._centered(56, 12, f"Employee Visit Report - Year {self.rep['year']}", bold=True)
            meta = (f"Generated: {_fmt_generated(self.rep['generated'])}    Total employees: {self.rep['total_employees']}    "
                    f"Reached yearly limit: {self.rep['blocked_count']}")
            self._centered(66, 8, meta, gray=0.35)
            self.doc.line(0, 70, PAGE_W - 2 * _MARGIN, 70)
            self.y = 80
        else:
            self._centered(56, 9, f"Employee Visit Report - Year {self.rep['year']} (continued)", bold=True)
            self.doc.line(0, 60, PAGE_W - 2 * _MARGIN, 60)
            self.y = 66
        self._draw_footer()

    def _ensure(self, needed):
        if self.y + needed > PAGE_H - _BOTTOM:
            self._start_page(False)

    def _section_title(self, title):
        self._ensure(30)
        self.doc.text(0, self.y + 8, 10.5, title, bold=True)
        self.y += 15

    def _table_header(self, cols):
        h = self.LEAD + 6
        widths = [w for _, w in cols]
        self.doc.rect_fill(0, self.y, sum(widths), h, gray=0.88)
        x = 0
        for (label, w) in cols:
            self.doc.text(x + 4, self.y + 12, self.HEAD, label, bold=True)
            x += w
        self.y += h
        self.doc.line(0, self.y, sum(widths), self.y, gray=0.5)

    def _draw_row(self, cols, cells, bold=False, shade=False):
        size = self.BODY
        wraps = [_wrap_pdf(c, w - 8, self._measure, size) for c, (_, w) in zip(cells, cols)]
        lines = max(len(wl) for wl in wraps)
        rh = lines * self.LEAD + 6
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
        self._section_title("YEARLY ABROAD STATISTICS (yearly limit applies)")
        self._table_header(self.COLS_ABROAD_SUMMARY)
        for s in self.rep["abroad_stats"]:
            row = [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
                   s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]
            bold = s["status"].startswith("MAX REACHED")
            self._draw_row(self.COLS_ABROAD_SUMMARY, [str(c) for c in row], bold=bold)
        self.y += 10
        self._section_title("YEARLY LOCAL STATISTICS (no limit)")
        self._table_header(self.COLS_LOCAL_SUMMARY)
        for s in self.rep["local_stats"]:
            row = [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
                   s["local"]]
            self._draw_row(self.COLS_LOCAL_SUMMARY, [str(c) for c in row])
        self.y += 10
        self._section_title("VISIT DETAILS")
        for d in self.rep["details"]:
            group = f'{d["name"]} ({d["emp_id"]}) - {d["designation"]}'
            if d.get("project"):
                group += f' - Project: {d["project"]}'
            if not d["visits"]:
                group += f'  (no visits recorded in {self.rep["year"]})'
            self._ensure(40)
            self.doc.text(0, self.y + 8, 9, group, bold=True)
            self.y += 15
            self._table_header_detail()
            for v in d["visits"]:
                self._draw_row(self.COLS_DETAIL, [
                    v.get("visit_type", "Abroad"),
                    v.get("period", v.get("date", "")),
                    _pdf_time_groups(v.get("time", "")),
                    v.get("destination", v.get("country", "")),
                    v.get("purpose", ""),
                    v.get("title", ""),
                    v.get("detail", ""),
                ])
            self.y += 8

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

    def _centered(self, y_top, size, s, bold=False, gray=0):
        w = self._measure(s, size)
        x = max(0.0, (PAGE_W - 2 * _MARGIN - w) / 2.0)
        self.doc.text(x, y_top, size, s, bold=bold, gray=gray)

    def _draw_header(self):
        # Bordered header: both logos flank the centered org title.
        content_w = PAGE_W - 2 * _MARGIN
        title_w = self._measure(ORG_NAME, 9)
        lw = lh = gw = gh = 0
        if self.doc._logo_raw is not None and self.doc._logo_h:
            lh = 44
            lw = lh * self.doc._logo_w / self.doc._logo_h
            if lw > 54:
                lw = 54
                lh = lw * self.doc._logo_h / self.doc._logo_w
        if self.doc._govt_raw is not None and self.doc._govt_h:
            gh = 44
            gw = gh * self.doc._govt_w / self.doc._govt_h
        if lw and gw and title_w + lw + gw + 24 <= content_w:
            cx = (content_w - title_w) / 2.0
            left_x, govt_x = cx - 50 - lw, cx + title_w + 50
        else:  # fall back to both ends
            left_x, govt_x = 0, content_w - gw
        if lw:
            self.doc.draw_logo(left_x, 5, lw, lh)
        if gw:
            self.doc.draw_govt(govt_x, 5, gw, gh)
        self._centered(28, 12, ORG_NAME, bold=True, gray=0.15)
        self._centered(37, 8, ORG_SUBTITLE, gray=0.45)
        #self.doc.rect_stroke(0, 0, content_w, 48, gray=0.55)

    def _draw_footer(self):
        self.doc.line(0, PAGE_H - 22, PAGE_W - 2 * _MARGIN, PAGE_H - 22, gray=0.75)
        self.doc.text(PAGE_W - 2 * _MARGIN - 70, PAGE_H - 14, 7, f"Page {self.page_no}", gray=0.45)

    def _start_page(self, first=False):
        self.doc._new_page()
        self.page_no += 1
        self._draw_header()
        if first:
            self._centered(56, 12, f"Tenure Report - As of {_fmt_date(self.rep['today'])}", bold=True)
            meta = (f"Generated: {_fmt_generated(self.rep['generated'])}    Total employees: {self.rep['total_employees']}    "
                    f"Active: {self.rep['active_count']}    Released: {self.rep['released_count']}")
            self._centered(66, 8, meta, gray=0.35)
            self.doc.line(0, 70, PAGE_W - 2 * _MARGIN, 70)
            self.y = 80
        else:
            self._centered(56, 9, f"Tenure Report - As of {_fmt_date(self.rep['today'])} (continued)", bold=True)
            self.doc.line(0, 60, PAGE_W - 2 * _MARGIN, 60)
            self.y = 66
        self._draw_footer()

    def _ensure(self, needed):
        if self.y + needed > PAGE_H - _BOTTOM:
            self._start_page(False)

    def _section_title(self, title):
        self._ensure(30)
        self.doc.text(0, self.y + 8, 10.5, title, bold=True)
        self.y += 15

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
        self.y += 10
        self._section_title("TENURE DETAILS (intervals)")
        for d in self.rep["details"]:
            group = f'{d["name"]} ({d["emp_id"]}) - {d["designation"]}   Total: {d["total_formatted"]} ({d["total_days"]} days)  Status: {d["status"]}'
            self._ensure(30)
            self.doc.text(0, self.y + 8, 8.5, group, bold=True)
            self.y += 14
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
