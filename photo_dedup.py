#!/usr/bin/env python3
"""
photo_dedup.py - 画像重複検出スクリプト（AVIF/HEIC対応・差分更新版）
MacBook Air M3 16GB向けに最適化

レポート構成:
  - 確定重複（ORB 100%）: 無条件で削除して問題なし
  - 要確認（ORB <100%）: 目視確認推奨
  - 参考（pHashのみ）: ORBでは一致しなかった候補

使い方:
    python photo_dedup.py ./source --workers 4
    → source_dedup.db, source_report.md が自動生成される
    → DBが既に存在すれば差分更新（新規ファイルのみスキャン）
"""

import os
import sys
import hashlib
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillowがインストールされていません。")
    sys.exit(1)

try:
    import cv2
    import numpy as np
except ImportError:
    print("ERROR: opencv-pythonがインストールされていません。")
    sys.exit(1)

try:
    import imagehash
except ImportError:
    print("ERROR: imagehashがインストールされていません。")
    sys.exit(1)


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
    ".avif", ".heic", ".heif", ".raw", ".cr2", ".dng", ".nef",
}


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def compute_file_hash(filepath: Path, algorithm="sha256") -> str:
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_phash(filepath: Path, hash_size=16) -> str | None:
    try:
        with Image.open(filepath) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            phash = imagehash.phash(img, hash_size=hash_size)
            return str(phash)
    except Exception as e:
        print(f"  [WARN] pHash計算失敗 {filepath}: {e}")
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    x = int(hash1, 16) ^ int(hash2, 16)
    return bin(x).count("1")


def compute_orb_similarity(path1: Path, path2: Path) -> tuple[bool, float]:
    try:
        img1 = _load_image_cv2(path1)
        img2 = _load_image_cv2(path2)

        if img1 is None or img2 is None:
            return False, 0.0
        if img1.size < 10000 or img2.size < 10000:
            return False, 0.0

        orb = cv2.ORB_create(nfeatures=500)
        kp1, des1 = orb.detectAndCompute(img1, None)
        kp2, des2 = orb.detectAndCompute(img2, None)

        if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
            return False, 0.0

        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        matches = bf.knnMatch(des1, des2, k=2)

        good_matches = 0
        for m_n in matches:
            if len(m_n) == 2:
                m, n = m_n
                if m.distance < 0.75 * n.distance:
                    good_matches += 1

        match_ratio = good_matches / len(kp1)
        is_match = match_ratio >= 0.15 and good_matches >= 20
        return is_match, match_ratio

    except Exception as e:
        return False, 0.0


def _load_image_cv2(filepath: Path):
    ext = filepath.suffix.lower()
    img = cv2.imread(str(filepath), cv2.IMREAD_GRAYSCALE)
    if img is not None:
        return img
    if ext in (".avif", ".heic", ".heif"):
        try:
            with Image.open(filepath) as pil_img:
                if pil_img.mode in ("RGBA", "P"):
                    pil_img = pil_img.convert("RGB")
                img_array = np.array(pil_img.convert("RGB"))
                return cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        except Exception as e:
            print(f"  [WARN] 画像読み込み失敗 {filepath}: {e}")
            return None
    return None


class DedupDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                path TEXT PRIMARY KEY,
                filename TEXT,
                file_hash TEXT,
                file_size INTEGER,
                width INTEGER,
                height INTEGER,
                phash TEXT,
                mtime REAL,
                scanned_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_file_hash ON images(file_hash);
            CREATE INDEX IF NOT EXISTS idx_phash ON images(phash);

            CREATE TABLE IF NOT EXISTS duplicate_groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_type TEXT,
                confidence REAL,
                keep_path TEXT,
                paths TEXT,
                created_at TEXT
            );
        """)
        self.conn.commit()

    def insert_image(self, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO images
            (path, filename, file_hash, file_size, width, height, phash, mtime, scanned_at)
            VALUES (:path, :filename, :file_hash, :file_size, :width, :height, :phash, :mtime, :scanned_at)
        """, data)

    def get_existing_paths(self) -> set:
        cursor = self.conn.execute("SELECT path FROM images")
        return {row[0] for row in cursor.fetchall()}

    def remove_missing_paths(self, existing_paths: set):
        if not existing_paths:
            return 0
        placeholders = ",".join("?" * len(existing_paths))
        cursor = self.conn.execute(
            f"DELETE FROM images WHERE path NOT IN ({placeholders})",
            list(existing_paths)
        )
        self.conn.commit()
        return cursor.rowcount

    def commit(self):
        self.conn.commit()

    def get_all_images(self):
        cursor = self.conn.execute("SELECT * FROM images")
        return [dict(row) for row in cursor.fetchall()]

    def clear_duplicate_groups(self):
        self.conn.execute("DELETE FROM duplicate_groups")
        self.conn.commit()

    def save_duplicate_group(self, match_type: str, confidence: float, paths: list, keep_path: str = None):
        self.conn.execute("""
            INSERT INTO duplicate_groups (match_type, confidence, keep_path, paths, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (match_type, confidence, keep_path, json.dumps(paths), datetime.now().isoformat()))
        self.conn.commit()

    def close(self):
        self.conn.close()


def scan_image_file(filepath: Path) -> dict | None:
    try:
        stat = filepath.stat()
        file_hash = compute_file_hash(filepath)
        phash = compute_phash(filepath)

        try:
            with Image.open(filepath) as img:
                width, height = img.size
        except Exception:
            width, height = 0, 0

        return {
            "path": str(filepath),
            "filename": filepath.name,
            "file_hash": file_hash,
            "file_size": stat.st_size,
            "width": width,
            "height": height,
            "phash": phash,
            "mtime": stat.st_mtime,
            "scanned_at": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"  [ERROR] {filepath}: {e}")
        return None


def scan_directory(target_dir: Path, db: DedupDatabase, max_workers=4):
    current_files = {
        str(p.resolve())
        for p in target_dir.rglob("*")
        if p.is_file() and is_image_file(p)
    }

    db_paths = db.get_existing_paths()
    new_files_paths = current_files - db_paths
    removed_paths = db_paths - current_files

    print(f"📁 ディレクトリ内の画像ファイル: {len(current_files)} 枚")
    print(f"🗄️  DB登録済み: {len(db_paths)} 枚")

    if removed_paths:
        count = db.remove_missing_paths(current_files)
        print(f"🗑️  DBから削除（ファイルが存在しない）: {count} 件")

    if not new_files_paths:
        print("✅ 新規ファイルなし。DBをそのまま利用します。")
        return len(current_files)

    new_files = [Path(p) for p in new_files_paths]
    print(f"🆕 新規スキャン対象: {len(new_files)} 枚")
    print(f"   並列ワーカー: {max_workers}\n")

    processed = 0
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_image_file, f): f for f in new_files}
        for future in as_completed(futures):
            result = future.result()
            if result:
                db.insert_image(result)
            processed += 1
            if processed % 100 == 0:
                print(f"   ... {processed}/{len(new_files)} 枚処理完了")
                db.commit()

    db.commit()
    print(f"\n✅ スキャン完了: {len(new_files)} 枚を追加")
    return len(current_files)


def find_exact_duplicates(db: DedupDatabase) -> list:
    images = db.get_all_images()
    hash_groups = defaultdict(list)
    for img in images:
        hash_groups[img["file_hash"]].append(img["path"])

    duplicates = []
    for file_hash, paths in hash_groups.items():
        if len(paths) > 1:
            duplicates.append({
                "type": "exact",
                "confidence": 100.0,
                "paths": paths,
                "keep": select_best_quality(paths),
            })
    return duplicates


def find_phash_duplicates(db: DedupDatabase, threshold: int = 10) -> list:
    images = db.get_all_images()
    phash_items = [(img["path"], img["phash"]) for img in images if img["phash"]]

    print(f"\n🔍 pHash重複検出: {len(phash_items)} 枚を比較中...")

    n = len(phash_items)
    duplicates = []
    checked = set()

    for i in range(n):
        group = [phash_items[i][0]]
        for j in range(i + 1, n):
            if phash_items[j][0] in checked:
                continue
            dist = hamming_distance(phash_items[i][1], phash_items[j][1])
            if dist <= threshold:
                group.append(phash_items[j][0])
                checked.add(phash_items[j][0])

        if len(group) > 1:
            duplicates.append({
                "type": "phash",
                "confidence": max(0, 100 - threshold * 5),
                "paths": group,
                "keep": select_best_quality(group),
            })

    return duplicates


def find_orb_duplicates(candidate_groups: list) -> tuple[list, list]:
    """
    ORBで検証し、結果を確定重複（100%）と要確認（<100%）に分離して返す。
    戻り値: (confirmed_high, confirmed_low)
    """
    confirmed_high = []
    confirmed_low = []

    print(f"\n🔬 ORB特徴量マッチング: {len(candidate_groups)} グループを検証中...")

    for group in candidate_groups:
        paths = group["paths"]
        if len(paths) < 2:
            continue

        keep = paths[0]
        matched = [keep]
        max_ratio = 0.0
        for other in paths[1:]:
            is_match, ratio = compute_orb_similarity(Path(keep), Path(other))
            if is_match:
                matched.append(other)
                max_ratio = max(max_ratio, ratio)

        if len(matched) > 1:
            item = {
                "type": "orb",
                "confidence": min(100, max_ratio * 200),
                "paths": matched,
                "keep": select_best_quality(matched),
            }
            if item["confidence"] >= 99.9:
                confirmed_high.append(item)
            else:
                confirmed_low.append(item)

    return confirmed_high, confirmed_low


def select_best_quality(paths: list) -> str:
    best = paths[0]
    best_size = 0
    for p in paths:
        try:
            size = Path(p).stat().st_size
            if size > best_size:
                best_size = size
                best = p
        except:
            pass
    return best


def generate_report(db: DedupDatabase, exact, orb_high, orb_low, phash_only, output_path: Path):
    lines = []
    lines.append("# 画像重複検出レポート")
    lines.append(f"生成日時: {datetime.now().isoformat()}\n")

    total_auto = len(exact) + len(orb_high)
    total_manual = len(orb_low) + len(phash_only)

    lines.append(f"## サマリー")
    lines.append(f"- 確定重複（自動削除OK）: {total_auto} グループ")
    lines.append(f"- 要確認（目視確認推奨）: {total_manual} グループ")
    lines.append(f"- 削除候補ファイル数（確定）: {sum(len(g['paths'])-1 for g in exact + orb_high)}\n")

    if exact or orb_high:
        lines.append("## 確定重複（自動削除OK）")
        lines.append("ORB特徴量マッチングで信頼度100%のもの。無条件で削除して問題ありません。\n")
        for i, g in enumerate(exact + orb_high, 1):
            lines.append(f"### グループ {i}（信頼度: {g['confidence']:.1f}%）")
            lines.append(f"- 保持: `{g['keep']}`")
            for p in g["paths"]:
                if p != g["keep"]:
                    lines.append(f"- 削除候補: `{p}`")
            lines.append("")

    if orb_low:
        lines.append("## 要確認（ORB中低信頼度）")
        lines.append("特徴量マッチングで一致したが信頼度が100%未満です。目視で確認してください。\n")
        for i, g in enumerate(orb_low, 1):
            lines.append(f"### グループ {i}（信頼度: {g['confidence']:.1f}%）")
            lines.append(f"- 保持: `{g['keep']}`")
            for p in g["paths"]:
                if p != g["keep"]:
                    lines.append(f"- 削除候補: `{p}`")
            lines.append("")

    if phash_only:
        lines.append("## 参考（pHashのみ）")
        lines.append("pHashで近似的に一致しましたが、ORB特徴量マッチングでは一致しませんでした。")
        lines.append("同じキャラクターの別イラストなどの可能性があります。\n")
        for i, g in enumerate(phash_only, 1):
            lines.append(f"### グープ {i}（pHash信頼度: {g['confidence']:.1f}%）")
            lines.append(f"- 保持: `{g['keep']}`")
            for p in g["paths"]:
                if p != g["keep"]:
                    lines.append(f"- 削除候補: `{p}`")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📝 レポート保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="画像重複検出ツール（AVIF/HEIC対応・差分更新版）")
    parser.add_argument("target_dir", help="スキャン対象ディレクトリ")
    parser.add_argument("--db", default=None, help="SQLite DBパス（未指定時は {dir}_dedup.db）")
    parser.add_argument("--report", default=None, help="レポート出力パス（未指定時は {dir}_report.md）")
    parser.add_argument("--phash-threshold", type=int, default=10, help="pHashハミング距離閾値（デフォルト: 10）")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数（デフォルト: 4）")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).expanduser().resolve()
    if not target_dir.exists():
        print(f"ERROR: ディレクトリが存在しません: {target_dir}")
        sys.exit(1)

    dir_name = target_dir.name
    db_path = args.db if args.db else f"{dir_name}_dedup.db"
    report_path = Path(args.report if args.report else f"{dir_name}_report.md")

    db_exists = Path(db_path).exists()
    mode_str = "差分更新" if db_exists else "新規作成"

    print(f"📂 対象ディレクトリ: {target_dir}")
    print(f"🗄️  DBファイル: {db_path}（{mode_str}）")
    print(f"📝 レポートファイル: {report_path}")

    db = DedupDatabase(db_path)
    scan_directory(target_dir, db, max_workers=args.workers)
    db.clear_duplicate_groups()

    print("\n" + "=" * 50)
    print("Step 2: ファイルハッシュで完全一致を検出")
    exact = find_exact_duplicates(db)
    print(f"   完全一致グループ: {len(exact)}")

    print("\n" + "=" * 50)
    print("Step 3: pHashで近似的重複を検出")
    phash = find_phash_duplicates(db, threshold=args.phash_threshold)
    print(f"   pHash重複グループ: {len(phash)}")

    print("\n" + "=" * 50)
    print("Step 4: ORB特徴量で精密検証")
    orb_high, orb_low = find_orb_duplicates(phash)
    print(f"   ORB確定重複（100%）: {len(orb_high)} グループ")
    print(f"   ORB要確認（<100%）: {len(orb_low)} グループ")

    orb_paths = set()
    for g in orb_high + orb_low:
        orb_paths.update(g["paths"])
    phash_only = [g for g in phash if not any(p in orb_paths for p in g["paths"])]
    print(f"   pHashのみ（参考）: {len(phash_only)} グループ")

    for g in exact:
        db.save_duplicate_group(g["type"], g["confidence"], g["paths"], g["keep"])
    for g in orb_high:
        db.save_duplicate_group(g["type"], g["confidence"], g["paths"], g["keep"])

    print("\n" + "=" * 50)
    print("Step 5: レポート生成")
    generate_report(db, exact, orb_high, orb_low, phash_only, report_path)

    db.close()
    print("\n✅ 全処理完了")


if __name__ == "__main__":
    main()
