import os, re
import pandas as pd
from typing import Optional

class ClaimDataPrompt:
    REQ = {"datafield name", "input/output", "data type"}

    def __init__(self, placeholder: str = "---------"):
        self.placeholder = placeholder

    # ----------------- NEW: sheet coercion -----------------
    @staticmethod
    def _coerce_sheet_arg(sheet: Optional[str | int]):
        """
        Accepts None, int, or str. Returns None (first sheet), int index, or str sheet name.
        - "0" -> 0
        - "" / "none" / "null" -> None
        """
        if sheet is None:
            return None
        if isinstance(sheet, int):
            return sheet
        s = str(sheet).strip()
        if s == "" or s.lower() in {"none", "null"}:
            return None
        # numeric string?
        try:
            return int(s)
        except ValueError:
            return s  # treat as sheet name

    # ----------------- UPDATED: read_any with better errors -----------------
    def _read_any(self, path: str, sheet: Optional[str | int]) -> pd.DataFrame:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".xls", ".xlsx", ".xlsm", ".xlsb"):
            sn = self._coerce_sheet_arg(sheet)
            # First attempt
            try:
                return pd.read_excel(
                    path,
                    sheet_name=(0 if sn is None else sn),
                    dtype=str,
                    header=None
                ).fillna("")
            except ValueError as e:
                # If a wrong sheet name was passed (e.g., "0"), try to help
                try:
                    x = pd.ExcelFile(path)  # list sheets for diagnostics
                    names = x.sheet_names
                except Exception:
                    names = []
                # If sn is a string number like "0", try index 0 as a fallback
                if isinstance(sn, str):
                    try:
                        idx = int(sn)
                        return pd.read_excel(path, sheet_name=idx, dtype=str, header=None).fillna("")
                    except Exception:
                        pass
                # Final helpful error
                raise ValueError(
                    f"{e}\nAvailable sheets: {names}\n"
                    f"Tip: set SHEET to a number (e.g., 0) for the first sheet, or a valid name."
                )
        # CSV
        return pd.read_csv(path, dtype=str, header=None).fillna("")

    # ----------------- the rest stays the same -----------------
    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s).replace("\u00A0", " ").strip()).lower()

    @staticmethod
    def _label(field: str) -> str:
        s = str(field).replace("\u00A0", " ").replace("_", " ")
        return re.sub(r"\s+", " ", s).strip().lower()

    @staticmethod
    def _drop_empty_edges(df: pd.DataFrame) -> pd.DataFrame:
        non_empty_cols = ~((df.astype(str) == "").all(axis=0))
        df2 = df.loc[:, non_empty_cols]
        non_empty_rows = ~((df2.astype(str) == "").all(axis=1))
        return df2.loc[non_empty_rows, :]

    def _canonical_columns(self, cols):
        out = {}
        for c in cols:
            lc = self._norm(c)
            if lc in ("datafield name", "data field name", "field name", "field"):
                out[c] = "DataField Name"
            elif lc in ("input/output", "in/out", "io", "inputoutput"):
                out[c] = "Input/Output"
            elif lc in ("data type", "datatype", "type"):
                out[c] = "Data Type"
            elif lc in ("example", "sample", "default"):
                out[c] = "Example"
            else:
                out[c] = c
        return out

    def _detect_and_tidy(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        df = self._drop_empty_edges(df_raw)
        # A) transposed
        for col in df.columns:
            col_vals = {self._norm(v) for v in df[col].tolist()}
            if self.REQ.issubset(col_vals):
                t = df.copy()
                t.index = t[col].astype(str).str.replace("\u00A0", " ").str.strip()
                t = t.drop(columns=[col]).T.reset_index(drop=True)
                t = t.rename(columns=self._canonical_columns(t.columns))
                for need in ["DataField Name", "Input/Output", "Data Type"]:
                    if need not in t.columns:
                        t[need] = ""
                keep = [c for c in ["DataField Name", "Input/Output", "Data Type", "Example"] if c in t.columns]
                out = t[keep].copy()
                for c in keep:
                    out[c] = out[c].astype(str).str.replace("\u00A0", " ").str.strip()
                out = out[out["DataField Name"] != ""]
                if not out.empty:
                    return out.reset_index(drop=True)
        # B) tidy header
        for ridx in range(len(df)):
            row_vals = {self._norm(v) for v in df.iloc[ridx].tolist()}
            if self.REQ.issubset(row_vals):
                header = df.iloc[ridx].tolist()
                body = df.iloc[ridx + 1:].copy()
                body.columns = header
                body = body.rename(columns=self._canonical_columns(body.columns))
                keep = [c for c in ["DataField Name", "Input/Output", "Data Type", "Example"] if c in body.columns]
                out = body[keep].copy()
                for c in keep:
                    out[c] = out[c].astype(str).str.replace("\u00A0", " ").str.strip()
                out = out[out["DataField Name"] != ""]
                if not out.empty:
                    return out.reset_index(drop=True)
        raise ValueError("Schema not detected. Expect 4-row transposed or tidy header with required labels.")

    def _clean_value(self, val: str) -> str:
        s = str(val).strip()
        if not s:
            return s
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?: 00:00:00)?", s)
        if m:
            y, mo, d = m.groups()
            return f"{int(mo)}/{int(d)}/{y}"
        if re.fullmatch(r"\d+\.\d+", s):
            try:
                f = float(s)
                if f.is_integer():
                    return str(int(f))
            except Exception:
                pass
        return s

    def _build_prompt_from_tidy(self, tidy: pd.DataFrame) -> str:
        have_example = "Example" in tidy.columns
        inputs, outputs = [], []
        for _, r in tidy.iterrows():
            field = r["DataField Name"]
            io = self._norm(r["Input/Output"])
            label = self._label(field)
            if io.startswith("in"):
                val = self._clean_value(str(r.get("Example", ""))) if have_example else ""
                if not val:
                    val = self.placeholder
                inputs.append(f"{label}: {val}")
            elif io.startswith("out"):
                val = self._clean_value(str(r.get("Example", ""))) if have_example else ""
                if not val:
                    val = self.placeholder
                outputs.append(f"{label}: {val}")

        note_line = (
            "NOTE: The following are output fields. Always ASK/CONFIRM these with the insurance claim agent based on claim discussion and situation."
            f"If it shows {self.placeholder}, it means we dont have that value."
        )

        return (
            "INPUT (known):\n" + ("\n".join(inputs) if inputs else "(none)") + "\n\n" +
            "OUTPUT (to confirm on call):\n" + note_line + "\n" +
            ("\n".join(outputs) if outputs else "(none)")
        )

    # Public
    def build_from_file(self, file_path: str, sheet: Optional[str | int] = None) -> str:
        if not file_path:
            raise ValueError("FILE_PATH env var not set.")
        df0 = self._read_any(file_path, sheet)
        tidy = self._detect_and_tidy(df0)
        return self._build_prompt_from_tidy(tidy)


        