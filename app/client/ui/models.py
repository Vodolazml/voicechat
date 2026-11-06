"""List models for QML that update in place.

`set_items` diffs by key: rows that stayed keep their delegates (so a speaking ring or an open menu is not
recreated), changed rows emit dataChanged for just the roles that changed, and new/removed rows animate.
"""
from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt, Signal, Property, Slot


class ListModel(QAbstractListModel):
    countChanged = Signal()

    def __init__(self, roles: Iterable[str], key: str = "id", parent=None) -> None:
        super().__init__(parent)
        self._roles = list(roles)
        if key not in self._roles:
            self._roles.insert(0, key)
        self._key = key
        self._role_ids = {Qt.UserRole + 1 + i: name for i, name in enumerate(self._roles)}
        self._role_by_name = {name: role for role, name in self._role_ids.items()}
        self._rows: list[dict[str, Any]] = []

    # --- Qt API -----------------------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt naming)
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        name = self._role_ids.get(role)
        return self._rows[index.row()].get(name) if name else None

    def roleNames(self):  # noqa: N802
        return {role: QByteArray(name.encode()) for role, name in self._role_ids.items()}

    def _get_count(self) -> int:
        return len(self._rows)

    count = Property(int, _get_count, notify=countChanged)

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict:
        return dict(self._rows[row]) if 0 <= row < len(self._rows) else {}

    # --- Python API -------------------------------------------------------------------------------

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    def index_of(self, key: Any) -> int:
        for i, row in enumerate(self._rows):
            if row.get(self._key) == key:
                return i
        return -1

    def find(self, key: Any) -> dict[str, Any] | None:
        i = self.index_of(key)
        return self._rows[i] if i >= 0 else None

    def set_items(self, items: list[dict[str, Any]]) -> None:
        new_keys = [item[self._key] for item in items]
        wanted = set(new_keys)
        # 1. remove rows that are gone (from the end, so indexes stay valid)
        for i in range(len(self._rows) - 1, -1, -1):
            if self._rows[i][self._key] not in wanted:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        # 2. walk the target order: move, insert or update
        for target, item in enumerate(items):
            key = item[self._key]
            current = self.index_of(key)
            if current == -1:
                self.beginInsertRows(QModelIndex(), target, target)
                self._rows.insert(target, dict(item))
                self.endInsertRows()
                continue
            if current != target:
                self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), target)
                row = self._rows.pop(current)
                self._rows.insert(target, row)
                self.endMoveRows()
            self._update_row(target, item)
        self.countChanged.emit()

    def update(self, key: Any, **changes: Any) -> None:
        i = self.index_of(key)
        if i >= 0:
            self._update_row(i, {**self._rows[i], **changes})

    def append(self, item: dict[str, Any]) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(dict(item))
        self.endInsertRows()
        self.countChanged.emit()

    def prepend_many(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        self.beginInsertRows(QModelIndex(), 0, len(items) - 1)
        self._rows[0:0] = [dict(item) for item in items]
        self.endInsertRows()
        self.countChanged.emit()

    def remove(self, key: Any) -> None:
        i = self.index_of(key)
        if i >= 0:
            self.beginRemoveRows(QModelIndex(), i, i)
            del self._rows[i]
            self.endRemoveRows()
            self.countChanged.emit()

    def clear(self) -> None:
        if self._rows:
            self.beginResetModel()
            self._rows = []
            self.endResetModel()
            self.countChanged.emit()

    def _update_row(self, i: int, item: dict[str, Any]) -> None:
        row = self._rows[i]
        changed = [self._role_by_name[name] for name, value in item.items()
                   if name in self._role_by_name and row.get(name) != value]
        if changed:
            row.update(item)
            index = self.index(i, 0)
            self.dataChanged.emit(index, index, changed)
