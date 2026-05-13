from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from src.presentation import MemoSpec, get_artifact_template, memo_spec_to_preview_markdown, resolve_brand_theme
from src.presentation.artifact_contracts import DEFAULT_MEMO_TEMPLATE_ID, normalize_memo_spec
from src.presentation.render_qa import qa_check_docx


def _normalize_hex(value: str) -> str:
    return str(value).replace("#", "").upper()


def _docx_size_scale(cover_style: str) -> dict[str, int]:
    if cover_style == "formal":
        return {"title": 34, "summary": 22, "section": 24, "accent": 22, "body": 20}
    if cover_style == "operator":
        return {"title": 30, "summary": 21, "section": 22, "accent": 20, "body": 19}
    return {"title": 32, "summary": 22, "section": 23, "accent": 21, "body": 20}


def _docx_surface_tokens(cover_style: str, *, background: str, surface: str, accent: str, border: str) -> dict[str, str]:
    if cover_style == "formal":
        return {
            "title_fill": accent,
            "title_border": border,
            "section_fill": surface,
            "section_border": border,
            "accent_fill": background,
            "accent_border": accent,
        }
    if cover_style == "operator":
        return {
            "title_fill": background,
            "title_border": accent,
            "section_fill": background,
            "section_border": accent,
            "accent_fill": surface,
            "accent_border": accent,
        }
    return {
        "title_fill": accent,
        "title_border": border,
        "section_fill": surface,
        "section_border": border,
        "accent_fill": background,
        "accent_border": accent,
    }


def _run_props(
    *,
    bold: bool = False,
    color: str | None = None,
    font_family: str | None = None,
    font_size_half_points: int | None = None,
) -> str:
    parts: list[str] = []
    if bold:
        parts.append("<w:b/>")
    if color:
        parts.append(f"<w:color w:val=\"{_normalize_hex(color)}\"/>")
    if font_family:
        escaped = font_family.replace("&", "&amp;").replace('"', "&quot;")
        parts.append(
            f"<w:rFonts w:ascii=\"{escaped}\" w:hAnsi=\"{escaped}\" w:cs=\"{escaped}\"/>"
        )
    if font_size_half_points:
        parts.append(f"<w:sz w:val=\"{font_size_half_points}\"/>")
        parts.append(f"<w:szCs w:val=\"{font_size_half_points}\"/>")
    return f"<w:rPr>{''.join(parts)}</w:rPr>" if parts else ""


def _paragraph(
    text: str,
    *,
    style: str | None = None,
    bold: bool = False,
    color: str | None = None,
    font_family: str | None = None,
    font_size_half_points: int | None = None,
    align: str | None = None,
    fill: str | None = None,
    border_color: str | None = None,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
    indent_left: int | None = None,
) -> str:
    paragraph_props: list[str] = []
    if style:
        paragraph_props.append(f"<w:pStyle w:val=\"{style}\"/>")
    if align:
        paragraph_props.append(f"<w:jc w:val=\"{align}\"/>")
    if spacing_before is not None or spacing_after is not None:
        attrs: list[str] = []
        if spacing_before is not None:
            attrs.append(f'w:before="{spacing_before}"')
        if spacing_after is not None:
            attrs.append(f'w:after="{spacing_after}"')
        paragraph_props.append(f"<w:spacing {' '.join(attrs)}/>")
    if indent_left is not None:
        paragraph_props.append(f"<w:ind w:left=\"{indent_left}\" w:hanging=\"240\"/>")
    if fill:
        paragraph_props.append(
            f"<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"{_normalize_hex(fill)}\"/>"
        )
    if border_color:
        border = _normalize_hex(border_color)
        paragraph_props.append(
            "<w:pBdr>"
            f"<w:top w:val=\"single\" w:sz=\"6\" w:space=\"1\" w:color=\"{border}\"/>"
            f"<w:bottom w:val=\"single\" w:sz=\"6\" w:space=\"1\" w:color=\"{border}\"/>"
            "</w:pBdr>"
        )

    props_xml = f"<w:pPr>{''.join(paragraph_props)}</w:pPr>" if paragraph_props else ""
    run_props = _run_props(
        bold=bold,
        color=color,
        font_family=font_family,
        font_size_half_points=font_size_half_points,
    )
    safe_text = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"<w:p>{props_xml}<w:r>{run_props}<w:t xml:space=\"preserve\">{safe_text}</w:t></w:r></w:p>"


def _document_xml(paragraphs: list[str]) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document "
        "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<w:body>"
        f"{''.join(paragraphs)}"
        "<w:sectPr>"
        "<w:pgSz w:w=\"12240\" w:h=\"15840\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1200\" w:bottom=\"1440\" w:left=\"1200\" w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/>"
        "</w:sectPr>"
        "</w:body></w:document>"
    )


def _styles_xml(theme, sizes: dict[str, int]) -> str:
    heading_font = theme.typography.heading_family
    body_font = theme.typography.body_family
    primary = _normalize_hex(theme.colors.primary)
    secondary = _normalize_hex(theme.colors.secondary)
    text = _normalize_hex(theme.colors.text)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr>
      <w:rFonts w:ascii="{body_font}" w:hAnsi="{body_font}" w:cs="{body_font}"/>
      <w:color w:val="{text}"/>
      <w:sz w:val="{sizes['body']}"/>
      <w:szCs w:val="{sizes['body']}"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="center"/></w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="{heading_font}" w:hAnsi="{heading_font}" w:cs="{heading_font}"/>
      <w:b/>
      <w:color w:val="{primary}"/>
      <w:sz w:val="{sizes['title']}"/>
      <w:szCs w:val="{sizes['title']}"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:rPr>
      <w:rFonts w:ascii="{heading_font}" w:hAnsi="{heading_font}" w:cs="{heading_font}"/>
      <w:b/>
      <w:color w:val="{secondary}"/>
      <w:sz w:val="{sizes['section']}"/>
      <w:szCs w:val="{sizes['section']}"/>
    </w:rPr>
  </w:style>
</w:styles>"""


def _font_table_xml(theme) -> str:
    heading_font = theme.typography.heading_family
    body_font = theme.typography.body_family
    mono_font = theme.typography.mono_family
    fonts = [heading_font, body_font, mono_font]
    entries = "".join(
        f"<w:font w:name=\"{font}\"><w:panose1 w:val=\"02020603050405020304\"/></w:font>"
        for font in fonts
    )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:fonts xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"{entries}</w:fonts>"
    )


def _theme_xml(theme) -> str:
    primary = _normalize_hex(theme.colors.primary)
    secondary = _normalize_hex(theme.colors.secondary)
    accent = _normalize_hex(theme.colors.accent)
    background = _normalize_hex(theme.colors.background)
    text = _normalize_hex(theme.colors.text)
    heading_font = theme.typography.heading_family
    body_font = theme.typography.body_family
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="agenticMIND Theme">
  <a:themeElements>
    <a:clrScheme name="agenticMIND">
      <a:dk1><a:srgbClr val="{text}"/></a:dk1>
      <a:lt1><a:srgbClr val="{background}"/></a:lt1>
      <a:accent1><a:srgbClr val="{primary}"/></a:accent1>
      <a:accent2><a:srgbClr val="{secondary}"/></a:accent2>
      <a:accent3><a:srgbClr val="{accent}"/></a:accent3>
    </a:clrScheme>
    <a:fontScheme name="agenticMIND">
      <a:majorFont><a:latin typeface="{heading_font}"/></a:majorFont>
      <a:minorFont><a:latin typeface="{body_font}"/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="agenticMIND"/>
  </a:themeElements>
</a:theme>"""


def _settings_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:zoom w:percent="100"/>
  <w:proofState w:spelling="clean" w:grammar="clean"/>
</w:settings>"""


def _web_settings_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:webSettings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:optimizeForBrowser/>
</w:webSettings>"""


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/word/webSettings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml"/>
  <Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>
  <Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def _root_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _document_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/webSettings" Target="webSettings.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>
</Relationships>"""


def _core_xml(title: str) -> str:
    safe_title = (
        title.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{safe_title}</dc:title>
  <dc:creator>agenticMIND</dc:creator>
</cp:coreProperties>"""


def _app_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>agenticMIND</Application>
</Properties>"""


def _write_docx_package(
    path: Path,
    *,
    title: str,
    document_xml: str,
    styles_xml: str,
    settings_xml: str,
    web_settings_xml: str,
    font_table_xml: str,
    theme_xml: str,
) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("docProps/core.xml", _core_xml(title))
        archive.writestr("docProps/app.xml", _app_xml())
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("word/settings.xml", settings_xml)
        archive.writestr("word/webSettings.xml", web_settings_xml)
        archive.writestr("word/fontTable.xml", font_table_xml)
        archive.writestr("word/_rels/document.xml.rels", _document_rels_xml())
        archive.writestr("word/theme/theme1.xml", theme_xml)


def render_docx_memo(*, path: Path, memo_spec: MemoSpec) -> dict[str, object]:
    memo_spec = normalize_memo_spec(memo_spec)
    theme = resolve_brand_theme(memo_spec.metadata.get("theme_id") or None)
    sizes = _docx_size_scale(theme.cover_style)
    surfaces = _docx_surface_tokens(
        theme.cover_style,
        background=theme.colors.background,
        surface=theme.colors.surface,
        accent=theme.colors.accent,
        border=theme.colors.border,
    )
    template = get_artifact_template(str(memo_spec.metadata.get("template_id") or DEFAULT_MEMO_TEMPLATE_ID))
    ordered_labels = list(memo_spec.section_order) or list(getattr(template, "section_order", []))
    seen_labels = set(ordered_labels)
    ordered_labels.extend([section.label for section in memo_spec.sections if section.label not in seen_labels])

    paragraphs = [
        _paragraph(
            memo_spec.title,
            style="Title",
            bold=True,
            color=theme.colors.primary,
            font_family=theme.typography.heading_family,
            font_size_half_points=sizes["title"],
            align="center",
            fill=surfaces["title_fill"],
            border_color=surfaces["title_border"],
            spacing_before=120,
            spacing_after=120,
        )
    ]

    if memo_spec.summary:
        paragraphs.append(_paragraph(""))
        paragraphs.append(
            _paragraph(
                memo_spec.summary,
                bold=False,
                color=theme.colors.text,
                font_family=theme.typography.body_family,
                font_size_half_points=sizes["summary"],
                fill=theme.colors.surface if theme.cover_style == "operator" else None,
                spacing_after=80,
            )
        )

    for label in ordered_labels:
        section = next((item for item in memo_spec.sections if item.label == label), None)
        if not section:
            continue
        paragraphs.append(_paragraph(""))
        paragraphs.append(
            _paragraph(
                section.label,
                style="Heading1",
                bold=True,
                color=theme.colors.secondary,
                font_family=theme.typography.heading_family,
                font_size_half_points=sizes["section"],
                fill=surfaces["section_fill"],
                border_color=surfaces["section_border"],
                spacing_before=80,
                spacing_after=40,
            )
        )
        for item in section.items:
            paragraphs.append(
                _paragraph(
                    item,
                    color=theme.colors.text,
                    font_family=theme.typography.body_family,
                    font_size_half_points=sizes["body"],
                    indent_left=720,
                )
            )

    if memo_spec.assumptions:
        paragraphs.append(_paragraph(""))
        paragraphs.append(
            _paragraph(
                "Assumptions",
                style="Heading1",
                bold=True,
                color=theme.colors.accent,
                font_family=theme.typography.heading_family,
                font_size_half_points=sizes["accent"],
                fill=surfaces["accent_fill"],
                border_color=surfaces["accent_border"],
                spacing_before=80,
                spacing_after=40,
            )
        )
        for item in memo_spec.assumptions:
            paragraphs.append(
                _paragraph(
                    item,
                    color=theme.colors.text,
                    font_family=theme.typography.body_family,
                    font_size_half_points=sizes["body"],
                    indent_left=720,
                )
            )

    if memo_spec.open_questions:
        paragraphs.append(_paragraph(""))
        paragraphs.append(
            _paragraph(
                "Open Questions",
                style="Heading1",
                bold=True,
                color=theme.colors.accent,
                font_family=theme.typography.heading_family,
                font_size_half_points=sizes["accent"],
                fill=surfaces["accent_fill"],
                border_color=surfaces["accent_border"],
                spacing_before=80,
                spacing_after=40,
            )
        )
        for item in memo_spec.open_questions:
            paragraphs.append(
                _paragraph(
                    item,
                    color=theme.colors.text,
                    font_family=theme.typography.body_family,
                    font_size_half_points=sizes["body"],
                    indent_left=720,
                )
            )

    document_xml = _document_xml(paragraphs)
    styles_xml = _styles_xml(theme, sizes)
    settings_xml = _settings_xml()
    web_settings_xml = _web_settings_xml()
    font_table_xml = _font_table_xml(theme)
    theme_xml = _theme_xml(theme)

    _write_docx_package(
        path,
        title=memo_spec.title,
        document_xml=document_xml,
        styles_xml=styles_xml,
        settings_xml=settings_xml,
        web_settings_xml=web_settings_xml,
        font_table_xml=font_table_xml,
        theme_xml=theme_xml,
    )

    qa_report = qa_check_docx(path, memo_spec)
    return {
        "preview_content": memo_spec_to_preview_markdown(memo_spec),
        "preview_format": "md",
        "preview_metadata": memo_spec.metadata,
        "qa_report": qa_report.model_dump(),
    }
