"""Server-side PDF export (admin). Generic: the frontend sends a title and a
list of sections (heading + optional columns + rows); we render a clean PDF
using reportlab and stream it back as a download."""
import io
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


class Section(BaseModel):
    heading: str = ""
    columns: Optional[List[str]] = None
    rows: List[List] = Field(default_factory=list)


class ExportIn(BaseModel):
    title: str = "Riepilogo"
    subtitle: Optional[str] = None
    filename: Optional[str] = None
    sections: List[Section] = Field(default_factory=list)


def build_router(current_admin):
    router = APIRouter(prefix="/export", tags=["export"])

    @router.post("/pdf")
    async def export_pdf(body: ExportIn, user: dict = Depends(current_admin)):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
        from reportlab.lib.styles import getSampleStyleSheet

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        )
        styles = getSampleStyleSheet()
        elems = [Paragraph(body.title, styles["Title"])]
        if body.subtitle:
            elems.append(Paragraph(body.subtitle, styles["Normal"]))
        elems.append(Paragraph(
            "RinoMagic · generato il " + datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
            styles["Normal"],
        ))
        elems.append(Spacer(1, 10))

        for s in body.sections:
            if s.heading:
                elems.append(Paragraph(s.heading, styles["Heading2"]))
            data = []
            if s.columns:
                data.append([str(c) for c in s.columns])
            data.extend([[("" if c is None else str(c)) for c in row] for row in s.rows])
            if data:
                table = Table(data, hAlign="LEFT")
                style = [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
                if s.columns:
                    style += [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F1216")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#F59E0B")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ]
                table.setStyle(TableStyle(style))
                elems.append(table)
            else:
                elems.append(Paragraph("Nessun dato.", styles["Italic"]))
            elems.append(Spacer(1, 12))

        doc.build(elems)
        buf.seek(0)
        fname = (body.filename or "riepilogo").replace('"', "") + ".pdf"
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    return router
