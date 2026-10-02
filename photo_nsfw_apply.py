#!/usr/bin/env python3
"""
photo_nsfw_apply_v2.py - NSFW判定レポートに基づく移動ツール

photo_nsfw_scan.py が生成したレポートを読み込み、
NSFW判定された画像を nsfw_review_YYMMDD ディレクトリへ、
UNCERTAIN判定された画像を uncertain_review_YYMMDD ディレクトリへ移動する。

使い方:
    # dry-run で確認
    python photo_nsfw_apply_v2.py pic-classifier --dry-run

    # 本番実行（nsfw_review_YYMMDD と uncertain_review_YYMMDD を作成して移動）
    python photo_nsfw_apply_v2.py pic-classifier --yes

    # NSFWのみ移動（従来と同じ動作）
    python photo_nsfw_apply_v2.py pic-classifier --yes --nsfw-only

    # UNCERTAINのみ移動
    python photo_nsfw_apply_v2.py pic-classifier --yes --uncertain-only
"""

import re
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime


def parse_nsfw_report(report_path: Path) -> tuple[list[dict], list[dict]]:
    """
    NSFWレポートをパースして NSFW と UNCERTAIN のリストを返す。
    戻り値: (nsfw_items, uncertain_items)
    要素は {"path": str, "filename": str, "score": float, "details": str}
    """
    if not report_path.exists():
        print(f"ERROR: レポートファイルが見つかりません: {report_path}")
        sys.exit(1)

    content = report_path.read_text(encoding="utf-8")

    nsfw_items = []
    uncertain_items = []
    current_section = None
    current_item = None

    for line in content.splitlines():
        line = line.strip()

        if line.startswith("## NSFW"):
            current_section = "nsfw"
            continue
        if line.startswith("## UNCERTAIN"):
            current_section = "uncertain"
            continue
        if line.startswith("## "):
            current_section = None
            continue

        # ファイル名行: ### 1. `filename.jpg`
        m_file = re.match(r"###\s+\d+\.\s+`(.*?)`", line)
        if m_file:
            current_item = {"filename": m_file.group(1)}
            continue

        # パス行
        m_path = re.match(r"- パス: `(.*?)`", line)
        if m_path and current_item:
            current_item["path"] = m_path.group(1)
            continue

        # NSFWスコア行
        m_score = re.match(r"- NSFWスコア: ([\d.]+)", line)
        if m_score and current_item:
            current_item["score"] = float(m_score.group(1))
            continue

        # 全スコア行（詳細として保存）
        m_detail = re.match(r"- 全スコア: (.+)", line)
        if m_detail and current_item and current_section in ("nsfw", "uncertain"):
            current_item["details"] = m_detail.group(1)
            if current_section == "nsfw":
                nsfw_items.append(current_item)
            elif current_section == "uncertain":
                uncertain_items.append(current_item)
            current_item = None
            continue

    return nsfw_items, uncertain_items


def verify_paths(items: list[dict]) -> list[dict]:
    """存在するファイルのみを返す。"""
    existing = []
    for item in items:
        p = Path(item["path"])
        if p.exists():
            existing.append(item)
        else:
            print(f"   ⚠️  スキップ（存在しない）: {item['path']}")
    return existing


def move_files(items: list[dict], dest_dir: Path, log_lines: list, dry_run=True) -> int:
    """ファイルを dest_dir へ移動。戻り値は成功数。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = 0

    for item in items:
        src = Path(item["path"])
        dst = dest_dir / item["filename"]

        # 名前衝突回避
        counter = 1
        stem = dst.stem
        suffix = dst.suffix
        while dst.exists():
            dst = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if dry_run:
            print(f"   [DRY-RUN MOVE] {src.name} → {dest_dir.name}/{dst.name}")
            log_lines.append(f"DRYRUN_MOVE: {src} → {dst}")
        else:
            try:
                shutil.move(str(src), str(dst))
                print(f"   [MOVED] {src.name} → {dest_dir.name}/{dst.name}")
                log_lines.append(f"MOVED:   {src} → {dst}")
                moved += 1
            except Exception as e:
                print(f"   [ERROR] 移動失敗: {src} — {e}")
                log_lines.append(f"FAILED:  {src} → {dst} — {e}")

    return moved


def main():
    parser = argparse.ArgumentParser(description="NSFW判定レポートに基づく移動ツール（NSFW + UNCERTAIN対応）")
    parser.add_argument("dir_name", help="対象ディレクトリ名（例: pic-classifier → pic-classifier_nsfw_report.md を読み込む）")
    parser.add_argument("--yes", "-y", action="store_true", help="確認なしで即座に実行")
    parser.add_argument("--dry-run", "-n", action="store_true", help="実行予定のみ表示")
    parser.add_argument("--nsfw-only", action="store_true", help="NSFWのみ移動")
    parser.add_argument("--uncertain-only", action="store_true", help="UNCERTAINのみ移動")
    parser.add_argument("--log", default=None, help="操作ログの出力パス（デフォルト: {dir}_nsfw_apply_log.md）")
    args = parser.parse_args()

    if args.nsfw_only and args.uncertain_only:
        print("ERROR: --nsfw-only と --uncertain-only は同時に指定できません。")
        sys.exit(1)

    dir_name = args.dir_name
    report_path = Path(f"{dir_name}_nsfw_report.md")
    log_path = Path(args.log) if args.log else Path(f"{dir_name}_nsfw_apply_log.md")

    # nsfw_review_YYMMDD / uncertain_review_YYMMDD
    today_yyMMDD = datetime.now().strftime("%y%m%d")
    nsfw_dir = Path(f"./nsfw_review_{today_yyMMDD}").resolve()
    uncertain_dir = Path(f"./uncertain_review_{today_yyMMDD}").resolve()

    print(f"📄 レポート読み込み: {report_path}")

    nsfw_items, uncertain_items = parse_nsfw_report(report_path)

    print(f"\n📊 サマリー")
    print(f"   NSFW:      {len(nsfw_items)} ファイル")
    print(f"   UNCERTAIN: {len(uncertain_items)} ファイル")
    print(f"   NSFW移動先:      {nsfw_dir}")
    print(f"   UNCERTAIN移動先: {uncertain_dir}")

    # 移動対象の絞り込み
    move_nsfw = not args.uncertain_only
    move_uncertain = not args.nsfw_only

    if not move_nsfw and not move_uncertain:
        print("\n✅ 移動対象がありません。終了します。")
        sys.exit(0)

    # 存在確認
    nsfw_existing = verify_paths(nsfw_items) if move_nsfw else []
    uncertain_existing = verify_paths(uncertain_items) if move_uncertain else []

    # dry-run
    if args.dry_run:
        print(f"\n🔍 [DRY RUN] 実行予定:")
        if move_nsfw:
            print(f"   NSFW移動:      {len(nsfw_existing)} ファイル → {nsfw_dir}")
        if move_uncertain:
            print(f"   UNCERTAIN移動: {len(uncertain_existing)} ファイル → {uncertain_dir}")
        print(f"\n💡 実際の実行は --yes または -y を付けてください。")
        sys.exit(0)

    # 確認
    if not args.yes:
        print(f"\n⚠️  以下の操作を実行します。")
        if move_nsfw:
            print(f"   - NSFWを      {len(nsfw_existing)} ファイル移動 → {nsfw_dir}")
        if move_uncertain:
            print(f"   - UNCERTAINを {len(uncertain_existing)} ファイル移動 → {uncertain_dir}")
        answer = input("\n   本当に実行しますか？ [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("キャンセルしました。")
            sys.exit(0)

    # 実行
    log_lines = [f"# NSFW適用ログ - {datetime.now().isoformat()}", ""]

    total_moved = 0

    if move_nsfw and nsfw_existing:
        print(f"\n📁 NSFW画像の移動を実行中...")
        total_moved += move_files(nsfw_existing, nsfw_dir, log_lines, dry_run=False)

    if move_uncertain and uncertain_existing:
        print(f"\n📁 UNCERTAIN画像の移動を実行中...")
        total_moved += move_files(uncertain_existing, uncertain_dir, log_lines, dry_run=False)

    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"\n📝 操作ログ保存: {log_path}")
    print(f"\n✅ 完了: {total_moved} ファイルを移動しました。")


if __name__ == "__main__":
    main()
