"""
Master Excel Pipeline Synchronizer (backend/excel_sync.py).
Synchronizes smartapply.db into a beautifully formatted, multi-tab Excel workbook:
  - Tab 1: 1_Scored_Jobs_Triage
  - Tab 2: 2_Tailored_Resumes_Audit
  - Tab 3: 3_CEO_Manager_HR_Outreach

Guarantees Steps 1, 2, and 3 are 100% complete and inspectable at all times.
"""

import json
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import Optional

from backend.db import (
    DEFAULT_DB_PATH,
    get_db_connection,
    init_db,
)

DEFAULT_EXCEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "SmartApply_Master_Pipeline.xlsx"
)


def _apply_header_style(cell, text: str, fill_color: str = "1F497D", font_color: str = "FFFFFF"):
    cell.value = text
    cell.font = Font(name="Calibri", size=11, bold=True, color=font_color)
    cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _apply_data_style(cell, value, horizontal: str = "left", bold: bool = False, fill_color: Optional[str] = None):
    cell.value = value
    cell.font = Font(name="Calibri", size=10, bold=bold)
    cell.alignment = Alignment(horizontal=horizontal, vertical="center", wrap_text=True)
    if fill_color:
        cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")


def _auto_fit_columns(ws, max_cols: int, min_width: int = 12, max_width: int = 50):
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    for col_idx in range(1, max_cols + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for row in range(1, ws.max_row + 1):
            cell = ws.cell(row=row, column=col_idx)
            cell.border = thin_border
            val_str = str(cell.value or "")
            if "\n" in val_str:
                val_str = max(val_str.split("\n"), key=len)
            if len(val_str) > max_len:
                max_len = len(val_str)
        width = max(min_width, min(max_len + 3, max_width))
        ws.column_dimensions[col_letter].width = width


def sync_db_to_excel(
    db_path: str = DEFAULT_DB_PATH,
    excel_path: str = DEFAULT_EXCEL_PATH,
) -> str:
    """Exports all data from smartapply.db to a formatted 3-tab workbook."""
    init_db(db_path)
    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    with get_db_connection(db_path) as conn:
        # =========================================================================
        # TAB 1: 1_Scored_Jobs_Triage
        # =========================================================================
        ws1 = wb.create_sheet(title="1_Scored_Jobs_Triage")
        ws1.views.sheetView[0].showGridLines = True
        ws1.row_dimensions[1].height = 28

        headers_tab1 = [
            "Job ID", "Company", "Role Title", "Location", "Track",
            "Match Score", "Eligibility", "Knockout Reason", "Sponsorship",
            "Matched Skills", "True Gaps", "Pipeline Status", "Job URL"
        ]
        for col_idx, h in enumerate(headers_tab1, 1):
            _apply_header_style(ws1.cell(row=1, column=col_idx), h, fill_color="1F497D")

        jobs_rows = conn.execute(
            """
            SELECT id, company, title, location, assigned_track, match_score,
                   is_eligible, knockout_reason, sponsorship_signal,
                   matched_skills_json, missing_gaps_json, pipeline_status, job_url
            FROM jobs
            ORDER BY match_score DESC, id DESC
            """
        ).fetchall()

        for r_idx, job in enumerate(jobs_rows, start=2):
            ws1.row_dimensions[r_idx].height = 22
            score = job["match_score"]
            score_color = "E2EFDA" if score >= 75 else ("FFF2CC" if score >= 50 else "FCE4D6")
            is_elig = bool(job["is_eligible"])
            elig_text = "ELIGIBLE" if is_elig else "KNOCKOUT"
            elig_color = "E2EFDA" if is_elig else "FCE4D6"

            matched_list = json.loads(job["matched_skills_json"] or "[]")
            missing_list = json.loads(job["missing_gaps_json"] or "[]")

            _apply_data_style(ws1.cell(row=r_idx, column=1), job["id"], horizontal="center")
            _apply_data_style(ws1.cell(row=r_idx, column=2), job["company"], bold=True)
            _apply_data_style(ws1.cell(row=r_idx, column=3), job["title"])
            _apply_data_style(ws1.cell(row=r_idx, column=4), job["location"] or "Remote/Unspecified")
            _apply_data_style(ws1.cell(row=r_idx, column=5), job["assigned_track"])
            _apply_data_style(ws1.cell(row=r_idx, column=6), score, horizontal="center", bold=True, fill_color=score_color)
            _apply_data_style(ws1.cell(row=r_idx, column=7), elig_text, horizontal="center", bold=True, fill_color=elig_color)
            _apply_data_style(ws1.cell(row=r_idx, column=8), job["knockout_reason"] or "-")
            _apply_data_style(ws1.cell(row=r_idx, column=9), job["sponsorship_signal"] or "SILENT", horizontal="center")
            _apply_data_style(ws1.cell(row=r_idx, column=10), ", ".join(matched_list[:12]))
            _apply_data_style(ws1.cell(row=r_idx, column=11), ", ".join(missing_list[:8]))
            _apply_data_style(ws1.cell(row=r_idx, column=12), job["pipeline_status"], horizontal="center", bold=True)

            url_cell = ws1.cell(row=r_idx, column=13)
            url_cell.value = job["job_url"]
            url_cell.font = Font(name="Calibri", size=9, color="0563C1", underline="single")
            url_cell.alignment = Alignment(horizontal="left", vertical="center")

        _auto_fit_columns(ws1, len(headers_tab1))

        # =========================================================================
        # TAB 2: 2_Tailored_Resumes_Audit
        # =========================================================================
        ws2 = wb.create_sheet(title="2_Tailored_Resumes_Audit")
        ws2.views.sheetView[0].showGridLines = True
        ws2.row_dimensions[1].height = 28

        headers_tab2 = [
            "Resume ID", "Job ID", "Company", "Role Title", "Track Used",
            "Selected Projects", "Per-Entry Skills & Tools Summary",
            "Page Count", "Fill %", "Overfull Hbox (pt)", "Bijective Coverage %",
            "PDF Output Path", "TeX Source Path", "Created At"
        ]
        for col_idx, h in enumerate(headers_tab2, 1):
            _apply_header_style(ws2.cell(row=1, column=col_idx), h, fill_color="366092")

        resumes_rows = conn.execute(
            """
            SELECT tr.id AS resume_id, j.id AS job_id, j.company, j.title,
                   tr.track_used, tr.selected_projects_json, tr.entry_skills_map_json,
                   tr.page_count, tr.vertical_fill_pct, tr.max_overfull_hbox_pt,
                   tr.bijective_skill_coverage_pct, tr.pdf_output_path, tr.tex_source_path,
                   tr.created_at
            FROM tailored_resumes tr
            JOIN jobs j ON tr.job_id = j.id
            ORDER BY tr.id DESC
            """
        ).fetchall()

        for r_idx, res in enumerate(resumes_rows, start=2):
            ws2.row_dimensions[r_idx].height = 24
            proj_list = json.loads(res["selected_projects_json"] or "[]")
            entry_skills_map = json.loads(res["entry_skills_map_json"] or "{}")
            entry_summary = " | ".join(f"{k}: {v}" for k, v in list(entry_skills_map.items())[:3])

            fill_val = round(res["vertical_fill_pct"], 1)
            fill_color = "E2EFDA" if 88.0 <= fill_val <= 98.5 else "FCE4D6"

            _apply_data_style(ws2.cell(row=r_idx, column=1), res["resume_id"], horizontal="center")
            _apply_data_style(ws2.cell(row=r_idx, column=2), res["job_id"], horizontal="center")
            _apply_data_style(ws2.cell(row=r_idx, column=3), res["company"], bold=True)
            _apply_data_style(ws2.cell(row=r_idx, column=4), res["title"])
            _apply_data_style(ws2.cell(row=r_idx, column=5), res["track_used"])
            _apply_data_style(ws2.cell(row=r_idx, column=6), ", ".join(proj_list))
            _apply_data_style(ws2.cell(row=r_idx, column=7), entry_summary)
            _apply_data_style(ws2.cell(row=r_idx, column=8), f"{res['page_count']} Page", horizontal="center", bold=True)
            _apply_data_style(ws2.cell(row=r_idx, column=9), f"{fill_val}%", horizontal="center", bold=True, fill_color=fill_color)
            _apply_data_style(ws2.cell(row=r_idx, column=10), f"{res['max_overfull_hbox_pt']} pt", horizontal="center")
            _apply_data_style(ws2.cell(row=r_idx, column=11), f"{res['bijective_skill_coverage_pct']}%", horizontal="center", bold=True)
            _apply_data_style(ws2.cell(row=r_idx, column=12), res["pdf_output_path"])
            _apply_data_style(ws2.cell(row=r_idx, column=13), res["tex_source_path"])
            _apply_data_style(ws2.cell(row=r_idx, column=14), str(res["created_at"])[:19], horizontal="center")

        _auto_fit_columns(ws2, len(headers_tab2))

        # =========================================================================
        # TAB 3: 3_CEO_Manager_HR_Outreach
        # =========================================================================
        ws3 = wb.create_sheet(title="3_CEO_Manager_HR_Outreach")
        ws3.views.sheetView[0].showGridLines = True
        ws3.row_dimensions[1].height = 28

        headers_tab3 = [
            "Draft ID", "Job ID", "Company", "Role Title", "Persona Target",
            "Contact Name & Title", "Email Subject", "Plain-Text Email Body",
            "LinkedIn Note (<=300 chars)", "Word Count", "Status"
        ]
        for col_idx, h in enumerate(headers_tab3, 1):
            _apply_header_style(ws3.cell(row=1, column=col_idx), h, fill_color="215967")

        outreach_rows = conn.execute(
            """
            SELECT od.id AS draft_id, j.id AS job_id, j.company, j.title,
                   od.persona_type, od.target_person_name, od.target_person_title,
                   od.email_subject, od.email_body_plaintext, od.linkedin_note_text,
                   od.outreach_status
            FROM outreach_drafts od
            JOIN jobs j ON od.job_id = j.id
            ORDER BY od.job_id DESC, od.id ASC
            """
        ).fetchall()

        for r_idx, out in enumerate(outreach_rows, start=2):
            ws3.row_dimensions[r_idx].height = 50
            contact_str = f"{out['target_person_name'] or 'Hiring Leader'} ({out['target_person_title'] or 'Team Lead'})"
            body = out["email_body_plaintext"] or ""
            words = len(body.split())
            li_note = out["linkedin_note_text"] or ""

            persona_badge = {
                "CEO_DELEGATION": "CEO / Exec Delegation",
                "HIRING_MANAGER_SME": "Hiring Manager / SME",
                "HR_RECRUITER": "HR / Recruiter Checklist",
            }.get(out["persona_type"], out["persona_type"])

            _apply_data_style(ws3.cell(row=r_idx, column=1), out["draft_id"], horizontal="center")
            _apply_data_style(ws3.cell(row=r_idx, column=2), out["job_id"], horizontal="center")
            _apply_data_style(ws3.cell(row=r_idx, column=3), out["company"], bold=True)
            _apply_data_style(ws3.cell(row=r_idx, column=4), out["title"])
            _apply_data_style(ws3.cell(row=r_idx, column=5), persona_badge, bold=True)
            _apply_data_style(ws3.cell(row=r_idx, column=6), contact_str)
            _apply_data_style(ws3.cell(row=r_idx, column=7), out["email_subject"], bold=True)
            _apply_data_style(ws3.cell(row=r_idx, column=8), body)
            _apply_data_style(ws3.cell(row=r_idx, column=9), li_note)
            _apply_data_style(ws3.cell(row=r_idx, column=10), f"{words} w", horizontal="center")
            _apply_data_style(ws3.cell(row=r_idx, column=11), out["outreach_status"], horizontal="center", bold=True)

        _auto_fit_columns(ws3, len(headers_tab3), min_width=12, max_width=60)

    # Save workbook
    os.makedirs(os.path.dirname(os.path.abspath(excel_path)), exist_ok=True)
    wb.save(excel_path)
    return excel_path


if __name__ == "__main__":
    path = sync_db_to_excel()
    print(f"Successfully synced smartapply.db to: {path}")
