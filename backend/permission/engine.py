from __future__ import annotations

import fnmatch
import hashlib
import json
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import resolve_config

PERMISSION_KEYS = {
    "read", "edit", "glob", "grep", "list", "bash", "task", "skill",
    "webfetch", "websearch", "external_directory", "doom_loop",
    "todowrite", "question", "lsp",
}


class Decision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @classmethod
    def parse(cls, value: Any) -> "Decision":
        if isinstance(value, Decision):
            return value
        s = str(value).strip().lower()
        if s in ("allow", "yes", "true", "1"):
            return cls.ALLOW
        if s in ("deny", "no", "false", "0"):
            return cls.DENY
        return cls.ASK


@dataclass
class PermissionRule:
    key: str
    decision: Decision
    pattern: str | None = None
    order: int = 0


def _normalize_path(p: str | None) -> str:
    if not p:
        return ""
    return str(p).replace("\\", "/").strip("/")


def _literal_len(pattern: str) -> int:
    return len(pattern.replace("*", "").replace("?", ""))


def _match(pattern: str, target: str | None) -> bool:
    t = _normalize_path(target)
    p = pattern.replace("\\", "/")
    if p.endswith("/**"):
        prefix = p[:-3]
        return t == prefix or t.startswith(prefix + "/") or fnmatch.fnmatchcase(t, p)
    return fnmatch.fnmatchcase(t, p)


class AlwaysAllowStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            from ..cookbook import config as _cfg

            db_path = _cfg.CONFIG_DIR / "permissions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS always_allow (
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                pattern TEXT NOT NULL,
                decided_at REAL NOT NULL,
                PRIMARY KEY (project_id, agent, key, pattern)
            )"""
        )
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.commit()

    def is_allowed(self, project_id: str, agent: str, key: str, target: str | None) -> bool:
        t = _normalize_path(target)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pattern FROM always_allow WHERE project_id=? AND agent=? AND key=?",
                (project_id, agent, key),
            ).fetchall()
        for (pattern,) in rows:
            if pattern == "*" or pattern == "" or _match(pattern, t):
                return True
        return False

    def remember(self, project_id: str, agent: str, key: str, target: str | None) -> None:
        pattern = _normalize_path(target) or "*"
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO always_allow (project_id, agent, key, pattern, decided_at) VALUES (?,?,?,?,?)",
                (project_id, agent, key, pattern, time.time()),
            )
            conn.commit()

    def forget(self, project_id: str, agent: str | None = None) -> None:
        with self._conn() as conn:
            if agent is None:
                conn.execute("DELETE FROM always_allow WHERE project_id=?", (project_id,))
            else:
                conn.execute(
                    "DELETE FROM always_allow WHERE project_id=? AND agent=?",
                    (project_id, agent),
                )
            conn.commit()


def project_id_for(root: str | Path | None) -> str:
    if not root:
        return "default"
    return hashlib.sha1(str(root).encode()).hexdigest()[:16]


DANGER_PATTERNS = (
    "rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf $HOME",
    ":(){:|:&};:",
    "mkfs", "dd if=/dev/zero",
    "git push --force", "git push -f",
    "chmod -R 000",
)


def is_dangerous(tool: str, target: str | None) -> bool:
    if tool == "run_bash":
        cmd = (target or "").lower()
        return any(p in cmd for p in DANGER_PATTERNS)
    if tool in ("write_file", "edit", "apply_patch"):
        p = (target or "").lower().replace("\\", "/")
        return any(
            s in p
            for s in (
                ".bashrc",
                ".zshrc",
                ".profile",
                "id_rsa",
                "id_ed25519",
                "id_ecdsa",
                ".ssh/",
                "credentials",
                ".env",
            )
        )
    return False


def parse_rules(raw: dict[str, Any] | None) -> dict[str, list[PermissionRule]]:
    rules: dict[str, list[PermissionRule]] = {}
    if not raw:
        return rules
    for agent, body in raw.items():
        if not isinstance(body, dict):
            continue
        agent_rules: list[PermissionRule] = []
        for order, (key, val) in enumerate(body.items()):
            if val is None:
                continue
            if isinstance(val, dict):
                for pattern, dec in val.items():
                    agent_rules.append(PermissionRule(key=key, decision=Decision.parse(dec), pattern=pattern, order=order))
            else:
                agent_rules.append(PermissionRule(key=key, decision=Decision.parse(val), pattern=None, order=order))
        rules[agent] = agent_rules
    return rules


class PermissionEngine:
    def __init__(
        self,
        rules: dict[str, list[PermissionRule]] | None = None,
        project_id: str = "default",
        store: AlwaysAllowStore | None = None,
        doom_window: int = 3,
    ):
        self.rules = rules or {}
        self.project_id = project_id
        self.store = store or AlwaysAllowStore()
        self.doom_window = doom_window
        self._history: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=doom_window))

    @classmethod
    def from_config(cls, start_dir: str | Path | None = None, store: AlwaysAllowStore | None = None) -> "PermissionEngine":
        cfg = resolve_config(start_dir)
        rules = parse_rules(cfg.project.permission)
        root = cfg.project_root
        return cls(rules=rules, project_id=project_id_for(root), store=store)

    def evaluate(self, agent: str, key: str, target: str | None = None) -> Decision:
        if self.store.is_allowed(self.project_id, agent, key, target):
            return Decision.ALLOW
        candidates = [r for r in self.rules.get(agent, []) if (r.key == key or r.key == "*") and (r.pattern is None or _match(r.pattern, target))]
        if not candidates:
            return Decision.ASK
        best = max(candidates, key=lambda r: (1 if r.key != "*" else 0, _literal_len(r.pattern or "") if r.pattern else -1, r.order))
        if best.decision is Decision.ALLOW:
            tool = self._tool_for_key(key)
            if tool and is_dangerous(tool, target):
                return Decision.ASK
        return best.decision

    @staticmethod
    def _tool_for_key(key: str) -> str:
        mapping = {"edit": "write_file", "bash": "run_bash", "read": "read_file", "list": "list_files", "webfetch": "web_search"}
        return mapping.get(key, "")

    def remember(self, agent: str, key: str, target: str | None) -> None:
        self.store.remember(self.project_id, agent, key, target)

    def forget(self, agent: str | None = None) -> None:
        self.store.forget(self.project_id, agent)

    def record_tool_call(self, agent: str, tool: str, args: dict) -> bool:
        sig = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
        key = (agent, tool)
        hist = self._history[key]
        prev = list(hist)
        hist.append(sig)
        if len(hist) >= self.doom_window and len(set(hist)) == 1:
            return True
        return False

    def clear_history(self, agent: str | None = None) -> None:
        if agent is None:
            self._history.clear()
        else:
            self._history = defaultdict(lambda: deque(maxlen=self.doom_window), {k: v for k, v in self._history.items() if k[0] != agent})
