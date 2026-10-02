#!/usr/bin/env python3
"""
photo_dedup_apply.py - 重複検出レポートに基づく削除・移動・リストアツール

設計方針:
  - 確定重複（ファイルハッシュ完全一致）の削除対象を削除
  - 重複候補（目視確認必須） review_<日付> ディレクトリへ移動
  - 確認後、review ディレクトリから source へリストア（group_xxx_ プレフィックス除去）

使い方:
    # 1. dry-run で確認
    python photo_dedup_apply.py pic-classifier

    # 2. 本番実行（削除 + 移動）
    python photo_dedup_apply.py pic-classifier --yes

    # 3. review から source へリストア（dry-run）
    python photo_dedup_apply.py pic-classifier --restore --review-dir ./review_2026-10-02

    # 4. review から source へリストア（本番）
    python photo_dedup_apply.py pic-classifier --restore --review-dir ./review_2026-10-02 --yes
"""

import re
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime


def parse_report(report_path: Path) -> tuple[list[str], list[str], list[list[str]]]:
    """
    レポートをパースして以下を返す:
    - keep_exact:   確定重複の保持推奨ファイル一覧
    - delete_exact: 確定重複削除対象ファイル一覧
    - candidate_groups: 重複候補のグループ（各グループはファイルパスのリスト）
    """
    if not report_path.exists():
        print(f"ERROR: レポートファイルが見つかりません: {report_path}")
        sys.exit(1)

    content = report_path.read_text(encoding="utf-8")

    keep_exact = []
    delete_exact = []
    candidate_groups = []

    current_section = None
    current_group = []

    for line in content.splitlines():
        line = line.strip()

        # セクション判定
        if line.startswith("## 確定重複"):
            current_section = "exact"
            continue
        if line.startswith("## 重複候補"):
            current_section = "candidate"
            if current_group:
                candidate_groups.append(current_group)
                current_group = []
            continue
        if line.startswith("## "):
            if current_section == "candidate" and current_group:
                candidate_groups.append(current_group)
                current_group = []
            current_section = None
            continue

        # 空行でグループ区切り（候補セクション内）
        if current_section == "candidate" and line == "":
            if current_group:
                candidate_groups.append(current_group)
                current_group = []
            continue

        # 確定重複: 保持推奨
        m_keep = re.match(r"- 保持推奨: `(.*?)`", line)
        if m_keep and current_section == "exact":
            keep_exact.append(m_keep.group(1))
            continue

        # 確定重複: 削除対象
        m_del = re.match(r"- 削除対象: `(.*?)`", line)
        if m_del and current_section == "exact":
            delete_exact.append(m_del.group(1))
            continue

        # 重複候補: 保持候補・削除候補（どちらもグループに含める）
        m_keep_c = re.match(r"- 保持候補: `(.*?)`", line)
        if m_keep_c and current_section == "candidate":
            current_group.append(m_keep_c.group(1))
            continue

        m_del_c = re.match(r"- 削除候補: `(.*?)`", line)
        if m_del_c and current_section == "candidate":
            current_group.append(m_del_c.group(1))
            continue

    # 最後のグループを追加
    if current_section == "candidate" and current_group:
        candidate_groups.append(current_group)

    return keep_exact, delete_exact, candidate_groups


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
        print(f"\n⚠️  以下ファイルは既に存在しません（無視されます）:")
        for m in missing:
            print(f"   {m}")

    return existing


def delete_files(paths: list[Path], log_lines: list) -> int:
    """ファイルを削除し、ログに記録。戻り値は削除成功数。"""
    deleted = 0
    for p in paths:
        try:
            size = p.stat().st_size
            p.unlink()
            print(f"   ✅ 削除: {p}")
            log_lines.append(f"DELETED: {p} ({size} bytes)")
            deleted += 1
        except FileNotFoundError:
            print(f"   ⚠️  スキップ（既に削除済み）: {p}")
        except Exception as e:
            print(f"   ❌ 失敗: {p} — {e}")
            log_lines.append(f"FAILED:  {p} — {e}")
    return deleted


def move_candidates(groups: list[list[str]], review_dir: Path, log_lines: list, dry_run=True) -> int:
    """
    重複候補グループを review_dir へ移動する。
    ファイル名に group_###_ プレフィックスを付与。
    戻り値は移動成功したファイル数。
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    moved = 0

    for gi, group in enumerate(groups, 1):
        prefix = f"group_{gi:03d}"
        for p_str in group:
            src = Path(p_str)
            if not src.exists():
                print(f"   ⚠️  スキップ（存在しない）: {src}")
                log_lines.append(f"SKIP:    {src} (not found)")
                continue

            dst_name = f"{prefix}_{src.name}"
            dst = review_dir / dst_name

            # 名前衝突回避
            counter = 1
            stem = dst.stem
            suffix = dst.suffix
            while dst.exists():
                dst = review_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            if dry_run:
                print(f"   [DRY-RUN MOVE] {src.name} → {review_dir.name}/{dst.name}")
                log_lines.append(f"DRYRUN_MOVE: {src} → {dst}")
            else:
                try:
                    shutil.move(str(src), str(dst))
                    print(f"   [MOVED] {src.name} → {review_dir.name}/{dst.name}")
                    log_lines.append(f"MOVED:   {src} → {dst}")
                    moved += 1
                except Exception as e:
                    print(f"   [ERROR] 移動失敗: {src} — {e}")
                    log_lines.append(f"FAILED:  {src} → {dst} — {e}")

    return moved


def restore_from_review(review_dir: Path, source_dir: Path, log_lines: list, dry_run=True) -> int:
    """
    review_dir 内のファイルを source_dir へ戻す。
    group_xxx_ プレフィックスを除去して元のファイル名を復元する。
    戻り値はリストア成功したファイル数。
    """
    if not review_dir.exists():
        print(f"ERROR: reviewディレクトリが見つかりません: {review_dir}")
        sys.exit(1)

    source_dir.mkdir(parents=True, exist_ok=True)

    restored = 0
    for src in review_dir.iterdir():
        if not src.is_file():
            continue

        # group_xxx_ プレフィックスを除去
        name = src.name
        m = re.match(r"group_\d+_(.+)", name)
        if m:
            original_name = m.group(1)
        else:
            original_name = name  # プレフィックスがなければそのまま

        dst = source_dir / original_name

        # 名前衝突回避
        counter = 1
        stem = dst.stem
        suffix = dst.suffix
        while dst.exists():
            dst = source_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if dry_run:
            print(f"   [DRY-RUN RESTORE] {src.name} → source/{dst.name}")
            log_lines.append(f"DRYRUN_RESTORE: {src} → {dst}")
        else:
            try:
                shutil.move(str(src), str(dst))
                print(f"   [RESTORED] {src.name} → source/{dst.name}")
                log_lines.append(f"RESTORED: {src} → {dst}")
                restored += 1
            except Exception as e:
                print(f"   [ERROR] リストア失敗: {src} — {e}")
                log_lines.append(f"FAILED:   {src} → {dst} — {e}")

    return restored


def main():
    parser = argparse.ArgumentParser(
        description="重複検出レポートに基づく削除・移動・リストアツール"
    )
    parser.add_argument(
        "dir_name",
        help="対象ディレクトリ名（例: pic-classifier → pic-classifier_report.md を読み込む）"
    )
    parser.add_argument(
        "--review-dir", "-r",
        default=None,
        help="重複候補の移動先 / リストア元ディレクトリ（デフォルト: ./review_YYYY-MM-DD）"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="確認なしで即座に実行"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="実行予定のみ表示（実際には削除・移動・リストアしない）"
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="review_dir から source ディレクトリへファイルをリストア（group_xxx_ プレフィックス除去）"
    )
    parser.add_argument(
        "--log",
        default=None,
        help="操作ログの出力パス（デフォルト: {dir}_apply_log.md）"
    )
    args = parser.parse_args()

    dir_name = args.dir_name
    report_path = Path(f"{dir_name}_report.md")
    log_path = Path(args.log) if args.log else Path(f"{dir_name}_apply_log.md")

    # reviewディレクトリ決定
    if args.review_dir:
        review_dir = Path(args.review_dir).expanduser().resolve()
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        review_dir = Path(f"./review_{today}").resolve()

    # リストアモード
    if args.restore:
        source_dir = Path("./source").resolve()
        print(f"📁 リストア元: {review_dir}")
        print(f"📂 リストア先: {source_dir}")

        log_lines = [f"# リストアログ - {datetime.now().isoformat()}", ""]

        # dry-run
        if args.dry_run:
            print(f"\n🔍 [DRY RUN] リストア予定:")
            restore_from_review(review_dir, source_dir, log_lines, dry_run=True)
            print(f"\n💡 実際の実行は --yes または -y を付けてください。")
            sys.exit(0)

        # 確認
        if not args.yes:
            # 先に dry-run 相当の表示
            print(f"\n🔍 リストア対象ファイル:")
            restore_from_review(review_dir, source_dir, log_lines, dry_run=True)
            answer = input("\n   本当にリストアしますか？ [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("キャンセルしました。")
                sys.exit(0)
            # ログをリセット
            log_lines = [f"# リストアログ - {datetime.now().isoformat()}", ""]

        print(f"\n📁 リストアを実行中...")
        total_restored = restore_from_review(review_dir, source_dir, log_lines, dry_run=False)

        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        print(f"\n📝 操作ログ保存: {log_path}")
        print(f"\n✅ 完了: {total_restored} ファイルを {source_dir} へリストアしました。")

        # 空になったreviewディレクトリを削除
        remaining = [p for p in review_dir.iterdir() if p.is_file()]
        if not remaining:
            try:
                review_dir.rmdir()
                print(f"\n🗑️  空のレビューディレクトリを削除しました: {review_dir}")
                log_lines.append(f"REMOVED_EMPTY_DIR: {review_dir}")
            except OSError as e:
                print(f"\n⚠️  レビューディレクトリの削除に失敗しました: {e}")
        else:
            print(f"\n📁 レビューディレクトリに {len(remaining)} ファイルが残っています: {review_dir}")

        sys.exit(0)

    # --- 通常モード（削除 + 移動） ---
    print(f"📄 レポート読み込み: {report_path}")

    keep_exact, delete_exact, candidate_groups = parse_report(report_path)

    print(f"\n📊 サマリー")
    print(f"   確定重複 保持推奨:   {len(keep_exact)} ファイル")
    print(f"   確定重複 削除対象:   {len(delete_exact)} ファイル")
    print(f"   重複候補 グループ数: {len(candidate_groups)} グループ")
    total_candidates = sum(len(g) for g in candidate_groups)
    print(f"   重複候補 ファイル数: {total_candidates} ファイル")
    print(f"   レビューディレクトリ: {review_dir}")

    if not delete_exact and not candidate_groups:
        print("\n✅ 削除対象・移動対象がありません。終了します。")
        sys.exit(0)

    # 存在確認
    to_delete = verify_paths(delete_exact)
    candidate_paths = []
    for g in candidate_groups:
        candidate_paths.extend(g)
    verify_paths(candidate_paths)

    # dry-run
    if args.dry_run:
        print(f"\n🔍 [DRY RUN] 実行予定:")
        if to_delete:
            print(f"   削除: {len(to_delete)} ファイル")
            for p in to_delete:
                print(f"      {p}")
        if candidate_groups:
            print(f"   移動: {len(candidate_groups)} グループ → {review_dir}")
        print(f"\n💡 実際の実行は --yes または -y を付けてください。")
        sys.exit(0)

    # 確認
    if not args.yes:
        print(f"\n⚠️  以下の操作を実行します。")
        if to_delete:
            print(f"   - 確定重複を {len(to_delete)} ファイル削除")
        if candidate_groups:
            print(f"   - 重複候補を {len(candidate_groups)} グループ（{total_candidates}ファイル）移動")
            print(f"     → {review_dir}")
        answer = input("\n   本当に実行しますか？ [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("キャンセルしました。")
            sys.exit(0)

    # 実行
    log_lines = [f"# 適用ログ - {datetime.now().isoformat()}", ""]
    total_deleted = 0
    total_moved = 0

    if to_delete:
        print(f"\n🗑️  確定重複の削除を実行中...")
        total_deleted = delete_files(to_delete, log_lines)

    if candidate_groups:
        print(f"\n📁 重複候補の移動を実行中...")
        total_moved = move_candidates(candidate_groups, review_dir, log_lines, dry_run=False)

    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"\n📝 操作ログ保存: {log_path}")

    print(f"\n✅ 完了:")
    if to_delete:
        print(f"   削除: {total_deleted}/{len(to_delete)} ファイル")
    if candidate_groups:
        print(f"   移動: {total_moved}/{total_candidates} ファイル → {review_dir}")


if __name__ == "__main__":
    main()
