#!/usr/bin/env python3
"""
photo_dedup_apply.py - 重複検出レポートに基づく一括削除ツール（最終版）

使い方:
    python photo_dedup_apply.py source --auto --yes
    → ORB信頼度100%の「確定重複」のみを自動削除（推奨）

    python photo_dedup_apply.py source --yes
    → 全削除候補を削除（従来通り）

    python photo_dedup_apply.py source --auto --dry-run
    → 確定重複の削除予定のみ表示（実際には削除しない）
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime


def parse_report(report_path: Path) -> tuple[list[str], list[str], list[str]]:
    """
    レポートをパースして、保持ファイル・確定削除候補・その他削除候補を返す。
    戻り値: (keep_all, auto_delete_paths, manual_delete_paths)
    """
    if not report_path.exists():
        print(f"ERROR: レポートファイルが見つかりません: {report_path}")
        sys.exit(1)

    content = report_path.read_text(encoding="utf-8")

    keep_all = []
    auto_delete = []    # 確定重複セクションの削除候補
    manual_delete = []  # 要確認・参考セクションの削除候補

    current_section = None

    for line in content.splitlines():
        line = line.strip()

        # セクション判定
        if line.startswith("## 確定重複"):
            current_section = "auto"
            continue
        if line.startswith("## 要確認") or line.startswith("## 参考"):
            current_section = "manual"
            continue
        if line.startswith("## "):
            current_section = None
            continue

        # 保持
        m_keep = re.match(r"- 保持: `(.*?)`", line)
        if m_keep:
            keep_all.append(m_keep.group(1))
            continue

        # 削除候補
        m_del = re.match(r"- 削除候補: `(.*?)`", line)
        if m_del:
            path = m_del.group(1)
            if current_section == "auto":
                auto_delete.append(path)
            elif current_section == "manual":
                manual_delete.append(path)

    return keep_all, auto_delete, manual_delete


def verify_paths(paths: list[str]) -> list[Path]:
    """パスの存在確認。存在しないものは除外。重複も除去。"""
    seen = set()
    existing = []
    missing = []
    for p_str in paths:
        if p_str in seen:
            continue
        seen.add(p_str)
        p = Path(p_str)
        if p.exists():
            existing.append(p)
        else:
            missing.append(p_str)

    if missing:
        print(f"\n⚠️  以下のファイルは既に存在しません（無視されます）:")
        for m in missing:
            print(f"   {m}")

    return existing


def delete_files(paths: list[Path], log_path: Path | None = None) -> int:
    """ファイルを削除し、ログに記録。戻り値は削除成功数。"""
    deleted = 0
    log_lines = [f"# 削除ログ - {datetime.now().isoformat()}", ""]

    for p in paths:
        try:
            size = p.stat().st_size
            p.unlink()
            print(f"   ✅ 削除: {p}")
            log_lines.append(f"DELETED: {p} ({size} bytes)")
            deleted += 1
        except FileNotFoundError:
            # 既に削除済み（並列実行や重複リストによる）
            print(f"   ⚠️  スキップ（既に削除済み）: {p}")
        except Exception as e:
            print(f"   ❌ 失敗: {p} — {e}")
            log_lines.append(f"FAILED:  {p} — {e}")

    if log_path and deleted > 0:
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        print(f"\n📝 削除ログ保存: {log_path}")

    return deleted


def main():
    parser = argparse.ArgumentParser(
        description="重複検出レポートに基づく一括削除ツール"
    )
    parser.add_argument(
        "dir_name",
        help="対象ディレクトリ名（例: source → source_report.md を読み込む）"
    )
    parser.add_argument(
        "--auto", "-a",
        action="store_true",
        help="ORB信頼度100%の確定重複のみを削除（推奨）"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="確認なしで即座に削除"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="削除予定のみ表（実際には削除しない）"
    )
    parser.add_argument(
        "--log",
        default=None,
        help="削除ログの出力パス（デフォルト: {dir}_delete_log.md）"
    )
    args = parser.parse_args()

    dir_name = args.dir_name
    report_path = Path(f"{dir_name}_report.md")
    log_path = Path(args.log) if args.log else Path(f"{dir_name}_delete_log.md")

    print(f"📄 レポート読み込み: {report_path}")

    keep_all, auto_delete, manual_delete = parse_report(report_path)

    # --auto モードなら確定重複のみ、そうでなければ全削除候補
    if args.auto:
        target_delete = auto_delete
        mode_label = "確定重複（ORB 100%）"
    else:
        target_delete = auto_delete + manual_delete
        mode_label = "全削除候補"

    if not target_delete:
        print(f"\n✅ {mode_label}の削除候補はありません。終了します。")
        sys.exit(0)

    print(f"\n📊 サマリー")
    print(f"   保持ファイル数:     {len(keep_all)}")
    print(f"   確定重複削除候補:   {len(auto_delete)}")
    print(f"   要確認削除候補:     {len(manual_delete)}")
    print(f"   → 今回の削除対象: {mode_label} {len(target_delete)} ファイル")

    # 存在確認 + 重複除去
    to_delete = verify_paths(target_delete)

    if not to_delete:
        print("\n⚠️  削除可能なファイルがありません。終了します。")
        sys.exit(0)

    # dry-run
    if args.dry_run:
        print(f"\n🔍 [DRY RUN] 削除予定ファイル一覧（重複除去後: {len(to_delete)} 件）:")
        for p in to_delete:
            print(f"   {p}")
        print(f"\n💡 実際の削除は行われません。実行するには --yes または -y を付けてください。")
        sys.exit(0)

    # 確認
    if not args.yes:
        print(f"\n⚠️  上記 {len(to_delete)} 個のファイルを削除します。")
        answer = input("   本当に削除しますか？ [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("キャンセルしました。")
            sys.exit(0)

    # 削除実行
    print(f"\n🗑️  削除を実行中...")
    deleted_count = delete_files(to_delete, log_path)

    print(f"\n✅ 完了: {deleted_count}/{len(to_delete)} ファイルを削除しました。")


if __name__ == "__main__":
    main()
