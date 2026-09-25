"""メジャーなツール・サービスの既定ポート (予約表の初期値)。

family は「同じ系統のツール同士なら衝突扱いしない」ための分類。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WellKnown:
    port: int
    service: str
    family: str


WELLKNOWN: list[WellKnown] = [
    # Firebase Emulator Suite
    WellKnown(4000, "Firebase Emulator UI", "firebase"),
    WellKnown(4400, "Firebase Emulator Hub", "firebase"),
    WellKnown(4500, "Firebase Emulator Logging", "firebase"),
    WellKnown(5000, "Firebase Hosting emulator", "firebase"),
    WellKnown(5001, "Firebase Functions emulator", "firebase"),
    WellKnown(8080, "Firebase Firestore emulator", "firebase"),
    WellKnown(8085, "Firebase Pub/Sub emulator", "firebase"),
    WellKnown(9000, "Firebase Realtime Database emulator", "firebase"),
    WellKnown(9099, "Firebase Auth emulator", "firebase"),
    WellKnown(9150, "Firebase Firestore emulator (WebSocket)", "firebase"),
    WellKnown(9199, "Firebase Storage emulator", "firebase"),
    WellKnown(9299, "Firebase Eventarc emulator", "firebase"),
    WellKnown(9399, "Firebase Data Connect emulator", "firebase"),
    WellKnown(9499, "Firebase Cloud Tasks emulator", "firebase"),
    # フロントエンド開発サーバ
    WellKnown(3000, "Next.js / Nuxt / CRA / Remix / serve", "web"),
    WellKnown(4173, "Vite preview", "vite"),
    WellKnown(4200, "Angular / Ember", "web"),
    WellKnown(4321, "Astro", "web"),
    WellKnown(5173, "Vite dev", "vite"),
    WellKnown(6006, "Storybook", "web"),
    WellKnown(8081, "Expo / Metro", "web"),
    WellKnown(8787, "Cloudflare Wrangler", "cloudflare"),
    WellKnown(1234, "Parcel", "web"),
    # Python
    WellKnown(7860, "Gradio", "python"),
    WellKnown(8000, "uvicorn / Django / Gatsby", "python"),
    WellKnown(8501, "Streamlit", "python"),
    WellKnown(8888, "Jupyter", "python"),
    # ミドルウェア
    WellKnown(1433, "SQL Server", "db"),
    WellKnown(3306, "MySQL / MariaDB", "db"),
    WellKnown(5432, "PostgreSQL", "db"),
    WellKnown(6379, "Redis", "db"),
    WellKnown(9200, "Elasticsearch", "db"),
    WellKnown(11434, "Ollama", "ai"),
    WellKnown(27017, "MongoDB", "db"),
    # Windows 自身
    WellKnown(135, "Windows RPC", "system"),
    WellKnown(445, "Windows SMB", "system"),
    WellKnown(3389, "Windows Remote Desktop", "system"),
    WellKnown(5357, "Windows WSD", "system"),
]

BY_PORT: dict[int, list[WellKnown]] = {}
for _w in WELLKNOWN:
    BY_PORT.setdefault(_w.port, []).append(_w)
