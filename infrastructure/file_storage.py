import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class FileStorage:
    """永続化用のファイル I/O を集約する。"""

    def append_json_line(self, path: Path, data: Mapping[str, Any]) -> None:
        """JSON Lines 形式で1件追記する。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False))
            file.write("\n")

    def overwrite_json(self, path: Path, data: Mapping[str, Any]) -> None:
        """JSON ファイルを一時ファイル経由で上書きする。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False, indent=2))
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(path)

    def read_json(self, path: Path) -> Mapping[str, Any] | None:
        """JSON ファイルを読み込む。存在しない場合は None を返す。"""

        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, Mapping):
            raise TypeError("JSON root must be object")
        return data
