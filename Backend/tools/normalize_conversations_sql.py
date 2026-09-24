from pathlib import Path
import sys


COLUMNS = (
    "`Id`, `CallSid`, `ResponseType`, `ResponseText`, `CreatedBy`, "
    "`CreatedAt`, `UpdatedBy`, `UpdatedAt`, `IsDeleted`"
)


def split_values(tuple_body: str) -> list[str]:
    values: list[str] = []
    start = 0
    quoted = False
    escaped = False

    for index, char in enumerate(tuple_body):
        if escaped:
            escaped = False
            continue
        if quoted and char == "\\":
            escaped = True
            continue
        if char == "'":
            quoted = not quoted
        elif char == "," and not quoted:
            values.append(tuple_body[start:index])
            start = index + 1

    if quoted:
        raise ValueError("Unterminated SQL string in tuple")
    values.append(tuple_body[start:])
    return values


def normalize(source: str) -> tuple[str, int]:
    marker = "VALUES"
    marker_at = source.find(marker)
    if marker_at < 0:
        raise ValueError("INSERT statement does not contain VALUES")

    values_text = source[marker_at + len(marker):].strip()
    if values_text.endswith(";"):
        values_text = values_text[:-1].rstrip()

    tuples: list[str] = []
    quoted = False
    escaped = False
    depth = 0
    tuple_start = -1

    for index, char in enumerate(values_text):
        if escaped:
            escaped = False
            continue
        if quoted and char == "\\":
            escaped = True
            continue
        if char == "'":
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == "(":
            if depth == 0:
                tuple_start = index + 1
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("Unexpected closing parenthesis")
            if depth == 0:
                fields = split_values(values_text[tuple_start:index])
                if len(fields) != 11:
                    raise ValueError(
                        f"Row {len(tuples) + 1} has {len(fields)} values; expected 11"
                    )
                tuples.append("(" + ",".join(fields[:9]) + ")")

    if quoted or depth != 0:
        raise ValueError("Incomplete SQL statement")
    if not tuples:
        raise ValueError("No value tuples found")

    header = f"INSERT INTO `conversations` ({COLUMNS}) VALUES\n"
    return header + ",\n".join(tuples) + ";\n", len(tuples)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: normalize_conversations_sql.py INPUT OUTPUT")
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    corrected, row_count = normalize(input_path.read_text(encoding="utf-8"))
    output_path.write_text(corrected, encoding="utf-8", newline="\n")
    print(f"Wrote {row_count} rows to {output_path}")
