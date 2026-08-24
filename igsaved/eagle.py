"""Клієнт локального API Eagle (http://localhost:41595).

Документація: https://api.eagle.cool/ — сервер піднімається, коли Eagle запущений.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests


class EagleError(RuntimeError):
    pass


@dataclass
class EagleItem:
    path: str
    name: str
    website: str = ""
    annotation: str = ""
    tags: List[str] = field(default_factory=list)

    def payload(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"path": str(self.path), "name": self.name}
        if self.website:
            data["website"] = self.website
        if self.annotation:
            data["annotation"] = self.annotation[:3000]
        if self.tags:
            data["tags"] = [t for t in self.tags if t][:30]
        return data


class EagleClient:
    def __init__(self, base_url: str = "http://localhost:41595", token: str = "", timeout: int = 30):
        self.base_url = (base_url or "http://localhost:41595").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout
        self.session = requests.Session()
        self._folder_cache: Dict[str, str] = {}

    # ------------------------------------------------------------- низький рівень
    def _params(self) -> Dict[str, str]:
        return {"token": self.token} if self.token else {}

    def _get(self, endpoint: str, params: Optional[dict] = None) -> Any:
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        merged = {**self._params(), **(params or {})}
        try:
            resp = self.session.get(url, params=merged, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as exc:
            raise EagleError(f"Eagle недоступний ({exc}).") from exc
        except ValueError as exc:
            raise EagleError("Eagle повернув не-JSON відповідь.") from exc
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            raise EagleError(f"Eagle: {body.get('message') or body.get('status')}")
        return body.get("data") if isinstance(body, dict) else body

    def _post(self, endpoint: str, payload: dict) -> Any:
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        try:
            resp = self.session.post(url, json=payload, params=self._params(), timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as exc:
            raise EagleError(f"Eagle недоступний ({exc}).") from exc
        except ValueError as exc:
            raise EagleError("Eagle повернув не-JSON відповідь.") from exc
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            raise EagleError(f"Eagle: {body.get('message') or body.get('status')}")
        return body.get("data") if isinstance(body, dict) else body

    # ---------------------------------------------------------------- перевірка
    def ping(self) -> Dict[str, Any]:
        """Повертає інформацію про застосунок або кидає EagleError."""
        data = self._get("application/info") or {}
        return data if isinstance(data, dict) else {}

    def library_path(self) -> str:
        info = self._get("library/info") or {}
        library = info.get("library") if isinstance(info, dict) else None
        if isinstance(library, dict):
            return str(library.get("path") or "")
        return ""

    def library_name(self) -> str:
        try:
            path = self.library_path()
            return str(path).rstrip("/\\").split("/")[-1].split("\\")[-1]
        except EagleError:
            pass
        return ""

    # ------------------------------------------------------------------ папки
    def list_folders(self) -> List[dict]:
        data = self._get("folder/list")
        return data if isinstance(data, list) else []

    @staticmethod
    def _walk(folders: List[dict]):
        for folder in folders or []:
            yield folder
            children = folder.get("children") or folder.get("folders") or []
            if isinstance(children, list):
                yield from EagleClient._walk(children)

    def find_folder(self, name: str, parent_id: Optional[str] = None) -> Optional[str]:
        """Шукає папку за назвою; parent_id=None — шукати серед кореневих."""
        folders = self.list_folders()
        target = (name or "").strip().lower()
        if parent_id is None:
            # Спершу серед кореневих — це найочікуваніше.
            for folder in folders:
                if str(folder.get("name", "")).strip().lower() == target:
                    return str(folder.get("id"))
            # Потім будь-де в дереві: якщо потрібна папка вкладена, краще взяти
            # її, ніж створити поруч дублікат із тією самою назвою.
            for folder in self._walk(folders):
                if str(folder.get("name", "")).strip().lower() == target:
                    return str(folder.get("id"))
            return None
        for folder in self._walk(folders):
            if str(folder.get("id")) != str(parent_id):
                continue
            for child in folder.get("children") or folder.get("folders") or []:
                if str(child.get("name", "")).strip().lower() == target:
                    return str(child.get("id"))
        return None

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        payload: Dict[str, Any] = {"folderName": name}
        if parent_id:
            payload["parent"] = parent_id
        data = self._post("folder/create", payload) or {}
        folder_id = data.get("id") if isinstance(data, dict) else None
        if not folder_id:
            raise EagleError(f"Eagle не повернув id для папки «{name}».")
        return str(folder_id)

    def ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Знаходить або створює папку; результат кешується на час сесії."""
        key = f"{parent_id or 'root'}::{(name or '').strip().lower()}"
        if key in self._folder_cache:
            return self._folder_cache[key]
        folder_id = self.find_folder(name, parent_id)
        if not folder_id:
            folder_id = self.create_folder(name, parent_id)
        self._folder_cache[key] = folder_id
        return folder_id

    def invalidate_folders(self) -> None:
        self._folder_cache.clear()

    # ------------------------------------------------------------------ імпорт
    def add_items(self, items: List[EagleItem], folder_id: Optional[str] = None) -> int:
        """Додає файли в бібліотеку. Повертає кількість надісланих елементів."""
        payloads = [item.payload() for item in items if item.path]
        if not payloads:
            return 0
        body: Dict[str, Any] = {"items": payloads}
        if folder_id:
            body["folderId"] = folder_id
        self._post("item/addFromPaths", body)
        return len(payloads)

    # -------------------------------------------------- наявні елементи
    def list_items(self, folder_ids: Optional[List[str]] = None,
                   limit: int = 200, offset: int = 0) -> List[dict]:
        """Сторінка елементів бібліотеки. Порожній список = кінець."""
        params: Dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
        if folder_ids:
            params["folders"] = ",".join(str(f) for f in folder_ids if f)
        data = self._get("item/list", params)
        return data if isinstance(data, list) else []

    def iter_items(self, folder_ids: Optional[List[str]] = None,
                   page: int = 200, max_items: int = 100000):
        """Проходить бібліотеку сторінками — їх бувають тисячі."""
        offset = 0
        seen = 0
        while seen < max_items:
            chunk = self.list_items(folder_ids, page, offset)
            if not chunk:
                return
            for item in chunk:
                yield item
                seen += 1
                if seen >= max_items:
                    return
            if len(chunk) < page:
                return
            offset += page

    def update_item(self, item_id: str, tags: Optional[List[str]] = None,
                    annotation: Optional[str] = None, url: str = "") -> None:
        """Дописує нотатку й теги до вже наявного елемента."""
        payload: Dict[str, Any] = {"id": str(item_id)}
        if tags is not None:
            payload["tags"] = [t for t in tags if t][:60]
        if annotation is not None:
            payload["annotation"] = annotation[:3000]
        if url:
            payload["url"] = url
        self._post("item/update", payload)

    def trash_items(self, item_ids: List[str]) -> int:
        """Переносить елементи в кошик Eagle. Насправді видаляє їх користувач —
        із кошика все ще можна дістати назад."""
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return 0
        self._post("item/moveToTrash", {"itemIds": ids})
        return len(ids)

    def item_file(self, item: dict, library: str = "") -> Optional[str]:
        """Шлях до самого файлу елемента на диску.

        Eagle тримає кожен елемент у своїй теці `<бібліотека>/images/<id>.info/`.
        Ім'я всередині може відрізнятись від того, що показує API (Eagle його
        нормалізує), тому шукаємо за розширенням, а не збираємо рядок.
        """
        from pathlib import Path

        item_id = str(item.get("id") or "")
        if not item_id:
            return None
        library = library or self.library_path()
        if not library:
            return None
        folder = Path(library) / "images" / f"{item_id}.info"
        if not folder.is_dir():
            return None

        ext = str(item.get("ext") or "").lower().lstrip(".")
        name = str(item.get("name") or "")
        if ext:
            exact = folder / f"{name}.{ext}"
            if exact.is_file():
                return str(exact)
            for candidate in folder.glob(f"*.{ext}"):
                if "_thumbnail" not in candidate.stem:
                    return str(candidate)
        for candidate in sorted(folder.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() != ".json" \
                    and "_thumbnail" not in candidate.stem:
                return str(candidate)
        return None
