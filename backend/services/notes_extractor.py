"""
Notes Extractor Service for LearnAI
Safely extracts text from student study materials:
- Photos / Images: JPG, JPEG, PNG, WEBP (via Gemini Vision OCR or image text parsing)
- Documents: PDF (pypdf), DOCX (python-docx / XML), TXT (UTF-8/Latin-1)
- Presentations: PPTX (python-pptx / XML), legacy PPT (olefile / binary stream)

Implements strict validation:
- 5 MB per-file size boundary
- File signature / magic bytes validation
- Content sanitization and memory-bounded output
- Safe temporary handling with guaranteed cleanup
"""

import io
import os
import re
import logging
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional, Tuple
from services.logger_utils import sanitize_error_text

logger = logging.getLogger("learnai.notes_extractor")

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB per file
MAX_EXTRACTED_CHARS = 50000            # Bounded output to prevent DoS / token exhaustion

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".pdf", ".docx", ".txt",
    ".ppt", ".pptx"
}

# Magic signatures
MAGIC_SIGNATURES = {
    "pdf": b"%PDF-",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "webp": b"RIFF",
    "zip": b"PK\x03\x04",              # Used by docx and pptx
    "ole": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # Used by legacy ppt
}


class ExtractionError(Exception):
    """Raised when file extraction fails due to format, corruption, or processing errors."""
    pass


def validate_file_header(filename: str, file_bytes: bytes) -> str:
    """
    Validates file extension, size, and magic signature.
    Returns normalized extension.
    """
    if not file_bytes or len(file_bytes) == 0:
        raise ExtractionError(f"File '{filename}' is empty.")

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise ExtractionError(f"File '{filename}' exceeds maximum allowed size of {max_mb} MB.")

    _, ext = os.path.splitext(filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(f"File format '{ext}' is not supported. Supported: JPG, PNG, WEBP, PDF, DOCX, TXT, PPT, PPTX.")

    # Magic byte validation
    if ext == ".pdf" and not file_bytes.startswith(MAGIC_SIGNATURES["pdf"]):
        raise ExtractionError(f"File '{filename}' has invalid PDF signature or is corrupted.")
    elif ext == ".png" and not file_bytes.startswith(MAGIC_SIGNATURES["png"]):
        raise ExtractionError(f"File '{filename}' has invalid PNG signature.")
    elif ext in {".jpg", ".jpeg"} and not file_bytes.startswith(MAGIC_SIGNATURES["jpg"]):
        raise ExtractionError(f"File '{filename}' has invalid JPEG signature.")
    elif ext == ".webp" and not (file_bytes.startswith(MAGIC_SIGNATURES["webp"]) and b"WEBP" in file_bytes[:16]):
        raise ExtractionError(f"File '{filename}' has invalid WEBP signature.")
    elif ext in {".docx", ".pptx"} and not file_bytes.startswith(MAGIC_SIGNATURES["zip"]):
        raise ExtractionError(f"File '{filename}' is not a valid modern Office document or is corrupted.")
    elif ext == ".ppt" and not file_bytes.startswith(MAGIC_SIGNATURES["ole"]):
        raise ExtractionError(f"File '{filename}' is not a valid legacy PowerPoint document.")

    return ext


def extract_text_from_txt(file_bytes: bytes) -> str:
    """Extracts plain text with encoding fallbacks."""
    for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="ignore")


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extracts text from PDF using pypdf with fallback."""
    text_parts = []
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        if getattr(reader, "is_encrypted", False):
            try:
                decrypted = reader.decrypt("")
                if decrypted == 0:
                    raise ExtractionError("PDF is password-protected or encrypted. Please remove password protection before uploading.")
            except ExtractionError:
                raise
            except Exception:
                raise ExtractionError("PDF is password-protected or encrypted. Please remove password protection before uploading.")
        for idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_parts.append(page_text.strip())
    except ExtractionError:
        raise
    except Exception as e:
        logger.warning(f"pypdf extraction error: {sanitize_error_text(e)}")

    # Fallback to pure regex text stream scan if pypdf returned empty or failed
    if not text_parts:
        try:
            # Extract plain text chunks enclosed between BT (Begin Text) and ET (End Text)
            matches = re.findall(rb'\((.*?)\)\s*Tj', file_bytes)
            for m in matches:
                try:
                    s = m.decode("latin-1", errors="ignore").strip()
                    if len(s) > 2:
                        text_parts.append(s)
                except Exception:
                    pass
        except Exception:
            pass

    return "\n\n".join(text_parts)


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extracts text from DOCX using python-docx with zip/xml fallback."""
    paragraphs = []
    try:
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        for p in doc.paragraphs:
            if p.text and p.text.strip():
                paragraphs.append(p.text.strip())
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    paragraphs.append(row_text)
    except Exception as e:
        logger.warning(f"python-docx error, falling back to direct XML parse: {e}")
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                xml_content = z.read("word/document.xml")
                root = ET.fromstring(xml_content)
                for elem in root.iter():
                    if elem.tag.endswith("t") and elem.text:
                        paragraphs.append(elem.text.strip())
        except Exception as e2:
            logger.error(f"DOCX XML fallback failed: {e2}")

    return "\n".join(paragraphs)


def extract_text_from_pptx(file_bytes: bytes) -> str:
    """Extracts text from PPTX using python-pptx with zip/xml fallback."""
    slide_texts = []
    try:
        import pptx
        prs = pptx.Presentation(io.BytesIO(file_bytes))
        for s_idx, slide in enumerate(prs.slides):
            slide_parts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text and shape.text.strip():
                    slide_parts.append(shape.text.strip())
            if slide_parts:
                slide_texts.append(f"[Slide {s_idx + 1}]\n" + "\n".join(slide_parts))
    except Exception as e:
        logger.warning(f"python-pptx error, falling back to direct XML parse: {e}")
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                slide_files = [f for f in z.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", f)]
                slide_files.sort()
                for s_file in slide_files:
                    xml_content = z.read(s_file)
                    root = ET.fromstring(xml_content)
                    slide_parts = [elem.text.strip() for elem in root.iter() if elem.tag.endswith("t") and elem.text]
                    if slide_parts:
                        slide_texts.append("\n".join(slide_parts))
        except Exception as e2:
            logger.error(f"PPTX XML fallback failed: {e2}")

    return "\n\n".join(slide_texts)


PPT_CONTAINER_RECORD_TYPES = {
    1000,   # DocumentContainer
    1006,   # SlideContainer
    1008,   # NotesContainer
    4057,   # HeadersFootersContainer
    4080,   # SlideListWithTextContainer
    0xF000, # msofbtDggContainer
    0xF002, # msofbtDgContainer
    0xF003, # msofbtSpgrContainer
    0xF004, # msofbtSpContainer
    0xF00D, # msofbtClientTextbox
}

PPT_IGNORE_RECORD_TYPES = {
    1016,  # RT_MainMaster (Slide Master template container with default placeholder strings)
    1010,  # RT_NotesMaster (Notes Master template container)
    5000,  # RT_ProgTags (Programmer tags - internal metadata)
    5001,  # RT_ProgBinaryTag
    5002,  # RT_ProgStringTag
    5003,  # RT_ProgBinaryTagData
    6000,  # RT_PrintOptions
    1023,  # RT_VbaInfo
    1024,  # RT_VbaInfoAtom
}


def _decode_ppt_text_atom(rec_type: int, payload: bytes) -> str:
    """Decodes PowerPoint text atom payloads according to their character encoding."""
    text = ""
    if rec_type == 4000:    # RT_TextCharsAtom (UTF-16LE)
        if len(payload) % 2 != 0:
            payload = payload[:len(payload) - 1]
        text = payload.decode("utf-16-le", errors="ignore")
    elif rec_type == 4008:  # RT_TextBytesAtom (ANSI / Latin-1)
        text = payload.decode("latin-1", errors="ignore")
    elif rec_type == 4026:  # RT_CString (UTF-16LE)
        if len(payload) % 2 != 0:
            payload = payload[:len(payload) - 1]
        text = payload.decode("utf-16-le", errors="ignore")

    # Filter internal PowerPoint programmer tag markers
    if text and re.match(r"^___PPT\d*$", text.strip()):
        return ""
    return text


def _iter_ppt_records(data: bytes):
    """
    Safely iterates PowerPoint binary record headers [MS-PPT].
    Yields (rec_type, rec_ver, rec_inst, is_container, payload).
    """
    pos = 0
    length = len(data)
    record_count = 0
    max_records = 50000

    while pos + 8 <= length and record_count < max_records:
        record_count += 1
        ver_inst = int.from_bytes(data[pos:pos+2], "little")
        rec_ver = ver_inst & 0x0F
        rec_inst = (ver_inst >> 4) & 0x0FFF
        rec_type = int.from_bytes(data[pos+2:pos+4], "little")
        rec_len = int.from_bytes(data[pos+4:pos+8], "little")
        pos += 8

        if rec_len < 0 or pos + rec_len > length:
            break

        payload = data[pos:pos+rec_len]
        pos += rec_len
        is_container = (rec_ver == 0x0F) or (rec_type in PPT_CONTAINER_RECORD_TYPES)
        yield rec_type, rec_ver, rec_inst, is_container, payload


def _extract_blocks_from_container(container_payload: bytes, max_depth: int = 15) -> list[str]:
    """
    Extracts text blocks from a SlideContainer or Escher shape hierarchy.
    Merges text split across consecutive text atoms within the same text box.
    """
    blocks = []

    def _walk(data: bytes, depth: int):
        if depth > max_depth:
            return
        current_parts = []
        for rtype, rver, rinst, is_cont, rpayload in _iter_ppt_records(data):
            if rtype in PPT_IGNORE_RECORD_TYPES:
                continue
            if rtype in (4000, 4008, 4026):
                text = _decode_ppt_text_atom(rtype, rpayload)
                if text:
                    current_parts.append(text)
            elif rtype == 3999:  # RT_TextHeaderAtom (new text box / paragraph boundary)
                if current_parts:
                    combined = "".join(current_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
                    if combined and not re.match(r"^___PPT\d*$", combined):
                        blocks.append(combined)
                    current_parts = []
            elif is_cont:
                if current_parts:
                    combined = "".join(current_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
                    if combined and not re.match(r"^___PPT\d*$", combined):
                        blocks.append(combined)
                    current_parts = []
                _walk(rpayload, depth + 1)

        if current_parts:
            combined = "".join(current_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
            if combined and not re.match(r"^___PPT\d*$", combined):
                blocks.append(combined)

    _walk(container_payload, 0)
    return blocks


def extract_text_from_ppt(file_bytes: bytes) -> str:
    """
    Extracts structured slide content from legacy binary PowerPoint presentations (PPT, PowerPoint 97-2003).
    Parses OLE compound document streams and navigates MS-PPT record hierarchies:
    - Primary extraction: SlideContainer (1006) and Escher shape trees (0xF002-0xF00D)
    - Secondary extraction: SlideListWithTextContainer (4080) with SlidePersistAtoms (1011)
    - Preserves slide order, slide boundaries [Slide N], slide titles, and body paragraphs
    - Joins text runs split across binary records and handles UTF-16 Unicode text
    - Raises ExtractionError on corrupted OLE structure or missing slide text (no silent garbage fallback)
    """
    try:
        import olefile
    except ImportError:
        raise ExtractionError("Server configuration error: 'olefile' library is required to extract legacy PowerPoint (.ppt) documents.")

    try:
        ole = olefile.OleFileIO(io.BytesIO(file_bytes))
    except Exception as e:
        logger.warning(f"Failed to open OLE compound document: {sanitize_error_text(e)}")
        raise ExtractionError("PowerPoint file is corrupted or not a valid OLE compound document.")

    if not ole.exists("PowerPoint Document"):
        raise ExtractionError("PowerPoint file is missing the 'PowerPoint Document' stream or is corrupted.")

    try:
        ppt_stream = ole.openstream("PowerPoint Document").read()
    except Exception as e:
        logger.warning(f"Failed to read PowerPoint Document stream: {sanitize_error_text(e)}")
        raise ExtractionError("Could not read PowerPoint Document stream from presentation.")

    if not ppt_stream:
        raise ExtractionError("PowerPoint presentation contains no readable slide text or lecture notes.")

    slide_container_slides = []
    slidelist_slides = []

    def _scan(data: bytes, depth: int = 0):
        if depth > 15:
            return
        for rtype, rver, rinst, is_cont, rpayload in _iter_ppt_records(data):
            if rtype in PPT_IGNORE_RECORD_TYPES:
                continue
            if rtype == 1006:  # RT_Slide (SlideContainer)
                blocks = _extract_blocks_from_container(rpayload)
                if blocks:
                    slide_container_slides.append(blocks)
            elif rtype == 4080 and rinst == 0:  # RT_SlideListWithText (presentation slides)
                cur_slide = []
                cur_parts = []
                for sub_type, _, _, _, sub_payload in _iter_ppt_records(rpayload):
                    if sub_type == 1011:  # RT_SlidePersistAtom
                        if cur_parts:
                            comb = "".join(cur_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
                            if comb and not re.match(r"^___PPT\d*$", comb):
                                cur_slide.append(comb)
                            cur_parts = []
                        if cur_slide:
                            slidelist_slides.append(cur_slide)
                            cur_slide = []
                    elif sub_type in (4000, 4008, 4026):
                        t = _decode_ppt_text_atom(sub_type, sub_payload)
                        if t:
                            cur_parts.append(t)
                    elif sub_type == 3999:  # RT_TextHeaderAtom
                        if cur_parts:
                            comb = "".join(cur_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
                            if comb and not re.match(r"^___PPT\d*$", comb):
                                cur_slide.append(comb)
                            cur_parts = []
                if cur_parts:
                    comb = "".join(cur_parts).replace("\r", "\n").replace("\x0b", "\n").strip()
                    if comb and not re.match(r"^___PPT\d*$", comb):
                        cur_slide.append(comb)
                if cur_slide:
                    slidelist_slides.append(cur_slide)
            elif is_cont:
                _scan(rpayload, depth + 1)

    _scan(ppt_stream)

    # Determine slide sequence: prefer rich SlideContainer shapes, fallback to SlideListWithText
    chosen_slides = []
    if slide_container_slides:
        chosen_slides = slide_container_slides
    elif slidelist_slides:
        chosen_slides = slidelist_slides

    formatted_slides = []
    if chosen_slides:
        for s_idx, parts in enumerate(chosen_slides):
            seen = set()
            clean_parts = []
            for p in parts:
                for line in p.split("\n"):
                    s_line = line.strip()
                    if s_line and not re.match(r"^___PPT\d*$", s_line) and s_line not in seen:
                        seen.add(s_line)
                        clean_parts.append(s_line)
            if clean_parts:
                formatted_slides.append(f"[Slide {s_idx + 1}]\n" + "\n".join(clean_parts))

    result = "\n\n".join(formatted_slides).strip()
    if not result:
        raise ExtractionError("PowerPoint presentation contains no readable slide text or lecture notes.")

    return result


def extract_text_from_image(file_bytes: bytes, ext: str) -> str:
    """
    Extracts text from images (JPG, PNG, WEBP) using Gemini Multimodal Vision API.
    Falls back gracefully if API is unconfigured or rate limited.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_BACKUP_API_KEY")
    if not api_key:
        logger.info("Gemini API key not configured; skipping vision OCR for image.")
        return ""

    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp"
    }
    mime_type = mime_map.get(ext, "image/jpeg")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"

        prompt = (
            "You are an academic document OCR and notes extraction specialist. "
            "Carefully transcribe all readable notes, headings, key terms, definitions, and equations "
            "from this lecture image. Return ONLY the transcribed text without extra preamble."
        )

        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt
            ]
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning(f"Gemini vision OCR error: {sanitize_error_text(e)}")
        return ""


def clean_extracted_text(text: str) -> str:
    """Sanitizes text, removes control characters, and bounds size."""
    if not text:
        return ""
    # Strip prompt injection and delimiter breakout attempts
    text = text.replace("<student_notes_untrusted>", "").replace("</student_notes_untrusted>", "")
    # Strip null bytes and non-printable control characters
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", " ", text)
    # Collapse excess whitespace while preserving paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_EXTRACTED_CHARS]


def extract_text_from_file(filename: str, file_bytes: bytes) -> str:
    """
    Main entry point: validates file and extracts cleaned text based on extension.
    Raises ExtractionError on invalid file, format error, or corrupted data.
    """
    ext = validate_file_header(filename, file_bytes)

    extracted = ""
    if ext == ".txt":
        extracted = extract_text_from_txt(file_bytes)
    elif ext == ".pdf":
        extracted = extract_text_from_pdf(file_bytes)
    elif ext == ".docx":
        extracted = extract_text_from_docx(file_bytes)
    elif ext == ".pptx":
        extracted = extract_text_from_pptx(file_bytes)
    elif ext == ".ppt":
        extracted = extract_text_from_ppt(file_bytes)
    elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
        extracted = extract_text_from_image(file_bytes, ext)

    cleaned = clean_extracted_text(extracted)
    return cleaned
