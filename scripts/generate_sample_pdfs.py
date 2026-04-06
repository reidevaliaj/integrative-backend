from pathlib import Path


def escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: list[str]) -> bytes:
    text_commands = [
        "BT",
        "/F1 24 Tf",
        "72 740 Td",
    ]

    for index, line in enumerate(lines):
        if index == 1:
            text_commands.append("/F1 18 Tf")
        elif index > 1:
            text_commands.append("/F1 13 Tf")

        if index > 0:
            text_commands.append("0 -28 Td")
        text_commands.append(f"({escape_pdf_text(line)}) Tj")

    text_commands.append("ET")
    content_stream = "\n".join(text_commands).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1") + content_stream + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("latin-1"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))

    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)


def main() -> None:
    target_dir = Path(__file__).resolve().parent.parent / "app" / "static" / "pdfs"
    target_dir.mkdir(parents=True, exist_ok=True)

    documents = {
        "current-main-issue.pdf": [
            "Integrative Medicine Journal",
            "Current Main Issue",
            "Issue 2026 | No. 194",
            "Sample PDF for the magazine dashboard.",
        ],
        "sample-issue-request.pdf": [
            "Integrative Medicine Journal",
            "Sample Issue Request",
            "Digital and Print",
            "Sample PDF for prospective readers.",
        ],
        "current-special-issue.pdf": [
            "Integrative Medicine Journal",
            "Current Special Issue",
            "Special Issue SH41",
            "Sample PDF for the special issue viewer.",
        ],
    }

    for filename, lines in documents.items():
        (target_dir / filename).write_bytes(build_pdf(lines))


if __name__ == "__main__":
    main()
