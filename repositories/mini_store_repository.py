from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from persistence.database import DatabaseTarget, coerce_database

from db_v3 import CATEGORY_CODES

_CATEGORY_DISPLAY = {
    "MILK_TEA": "奶茶饮品",
    "COFFEE": "咖啡",
    "FAST_FOOD_SNACK": "快餐小吃",
    "CHINESE_DINING": "中式正餐",
    "HOTPOT": "火锅",
    "BBQ": "烧烤",
    "BAKERY_DESSERT": "烘焙甜品",
    "BAR_LEISURE": "酒吧/休闲娱乐",
    "OTHER_FOOD": "其他餐饮",
}


class MiniStoreRepository:
    def __init__(self, database: DatabaseTarget):
        self.db = coerce_database(database)
        self.db_path = self.db.sqlite_path

    def _connect(self):
        return self.db.compat_connect()

    @staticmethod
    def _row(row: Any | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_or_get_user(self, openid: str, unionid: str | None = None) -> dict[str, Any]:
        openid = (openid or "").strip()
        if not openid:
            raise ValueError("openid is required")
        now = int(time.time() * 1000)
        with self._connect() as con:
            row = con.execute("SELECT * FROM mini_users WHERE wechat_openid=?", (openid,)).fetchone()
            if row is not None:
                con.execute(
                    "UPDATE mini_users SET wechat_unionid=COALESCE(?,wechat_unionid), last_login_at=? WHERE id=?",
                    (unionid, now, row["id"]),
                )
                con.commit()
                return dict(con.execute("SELECT * FROM mini_users WHERE id=?", (row["id"],)).fetchone())

            cur = con.execute(
                "INSERT INTO users(name,role) VALUES(?,?)",
                ("微信用户", "owner"),
            )
            user_id = int(cur.lastrowid)
            cur = con.execute(
                "INSERT INTO mini_users(user_id,wechat_openid,wechat_unionid,status,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                (user_id, openid, unionid, "ACTIVE", now, now),
            )
            mini_id = int(cur.lastrowid)
            con.commit()
            return dict(con.execute("SELECT * FROM mini_users WHERE id=?", (mini_id,)).fetchone())

    def bind_store(self, user_id: int, store_id: int) -> None:
        with self._connect() as con:
            row = con.execute("SELECT user_id FROM stores WHERE id=?", (store_id,)).fetchone()
            if row is None:
                raise KeyError("store not found")
            if int(row["user_id"]) != int(user_id):
                raise PermissionError("store belongs to another user")

    def get_current_store(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT s.*, p.category_code, p.city_code, p.owner_work_mode
                   FROM stores s
                   LEFT JOIN store_profiles p ON p.store_id=s.id
                   WHERE s.user_id=?
                   ORDER BY s.id ASC
                   LIMIT 1""",
                (user_id,),
            ).fetchone()
        return self._row(row)

    def create_store(self, user_id: int, name: str, category_code: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("store name is required")
        if category_code not in CATEGORY_CODES:
            raise ValueError("invalid category_code")
        now = int(time.time() * 1000)
        display_type = _CATEGORY_DISPLAY[category_code]
        with self._connect() as con:
            existing = con.execute("SELECT id FROM stores WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
            if existing is not None:
                raise ValueError("single-store Mini V1 user already has a store")
            cur = con.execute(
                """INSERT INTO stores(user_id,name,type,store_type,timezone,currency,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (user_id, name, display_type, display_type, "Asia/Shanghai", "CNY", now, now),
            )
            store_id = int(cur.lastrowid)
            con.execute(
                "INSERT INTO store_profiles(store_id,category_code,created_at,updated_at) VALUES(?,?,?,?)",
                (store_id, category_code, now, now),
            )
            con.commit()
        current = self.get_current_store(user_id)
        assert current is not None
        return current

    def update_store(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        current = self.get_current_store(user_id)
        if current is None:
            raise KeyError("store not found")
        store_id = int(current["id"])
        allowed = {"name", "category_code", "city_code", "owner_work_mode"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported store fields: {sorted(unknown)}")
        now = int(time.time() * 1000)
        with self._connect() as con:
            if "name" in values:
                name = str(values["name"]).strip()
                if not name:
                    raise ValueError("store name is required")
                con.execute("UPDATE stores SET name=?,updated_at=? WHERE id=? AND user_id=?", (name, now, store_id, user_id))
            profile_updates: list[str] = []
            params: list[Any] = []
            for key in ("category_code", "city_code", "owner_work_mode"):
                if key in values:
                    if key == "category_code" and values[key] not in CATEGORY_CODES:
                        raise ValueError("invalid category_code")
                    profile_updates.append(f"{key}=?")
                    params.append(values[key])
            if profile_updates:
                profile_updates.append("updated_at=?")
                params.extend([now, store_id])
                con.execute(f"UPDATE store_profiles SET {','.join(profile_updates)} WHERE store_id=?", params)
            con.commit()
        updated = self.get_current_store(user_id)
        assert updated is not None
        return updated
