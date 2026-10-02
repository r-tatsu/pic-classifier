#!/usr/bin/env python3
"""
photo_nsfw_scan.py - NSFW判定スキャンツール（nsfwpy使用）

対象ディレクトリ配下の全画像をスキャンし、nsfwpy で判定した結果をレポートに出力する。
ファイルは一切触れない（検出のみ）。

使い方:
    python photo_nsfw_scan.py ./pic-classifier
    → pic-classifier_nsfw_report.md が生成される

判定基準:
    - NSFW: hentai + porn >= 0.60
    - UNCERTAIN: 0.30 <= hentai + porn < 0.60
    - SFW: hentai + porn < 0.30
    - sexy は NSFW 判定に含めない
"""

import sys
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
    ".avif", ".heic", ".heif", ".raw", ".cr2", ".dng", ".nef",
}

NSFW_THRESHOLD = 0.60
UNCERTAIN_THRESHOLD = 0.30

# ワーカープロセごとに1回だけ初期化されるモデル
_worker_detector = None


def check_nsfwpy():
    """nsfwpy がインストールされているか確認。なければエラー表示して停止。"""
    try:
        from nsfwpy import NSFW
        return True
    except ImportError:
        print("=" * 60)
        print("ERROR: nsfwpy がインストールされていません。")
        print("-" * 60)
        print("以下のコマンドでインストールしてくさい:")
        print("    pip install nsfwpy")
        print("=" * 60)
        return False


def init_worker():
    """ワーカープロセス起動時に1回だけモデルをロードする。"""
    global _worker_detector
    from nsfwpy import NSFW
    _worker_detector = NSFW(device="coreml")


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def scan_nsfw(filepath: Path) -> dict | None:
    """nsfwpy で画像を判定。戻り値はスコア辞書、失敗時は None。"""
    global _worker_detector
    try:
        result = _worker_detector.predict_image(str(filepath))
        return result
    except Exception as e:
        print(f"   [WARN] NSFW判定失敗 {filepath}: {e}")
        return None


def classify_nsfw(result: dict) -> tuple[str, float]:
    """
    nsfwpy の結果から判定。
    戻り値: (category, score)
    category: "nsfw" | "uncertain" | "sfw"
    score: hentai + porn
    """
    hentai = result.get("hentai", 0.0)
    porn = result.get("porn", 0.0)
    score = hentai + porn

    if score >= NSFW_THRESHOLD:
        return "nsfw", score
    elif score >= UNCERTAIN_THRESHOLD:
        return "uncertain", score
    else:
        return "sfw", score


def collect_images(base_dir: Path) -> list[Path]:
    """対象ディレクトリ配下の全画像ファイルを収集する。"""
    images = []
    for p in base_dir.rglob("*"):
        if p.is_file() and is_image_file(p):
            images.append(p)
    return images


def scan_single_image(filepath: Path) -> dict | None:
    """1ファイルをスキャンして結果を返す。"""
    result = scan_nsfw(filepath)
    if result is None:
        return None

    category, score = classify_nsfw(result)
    return {
        "path": str(filepath),
        "filename": filepath.name,
        "category": category,
        "score": score,
        "drawing": result.get("drawing", 0.0),
        "hentai": result.get("hentai", 0.0),
        "neutral": result.get("neutral", 0.0),
        "porn": result.get("porn", 0.0),
        "sexy": result.get("sexy", 0.0),
    }


def generate_report(results: list[dict], output_path: Path):
    nsfw_items = [r for r in results if r["category"] == "nsfw"]
    uncertain_items = [r for r in results if r["category"] == "uncertain"]
    sfw_items = [r for r in results if r["category"] == "sfw"]

    # スコア降順でソート
    nsfw_items.sort(key=lambda x: x["score"], reverse=True)
    uncertain_items.sort(key=lambda x: x["score"], reverse=True)

    lines = []
    lines.append("# NSFW判定レポート")
    lines.append(f"生成日時: {datetime.now().isoformat()}")
    lines.append(f"判定基準: hentai + porn >= {NSFW_THRESHOLD:.2f} → NSFW")
    lines.append(f"          {UNCERTAIN_THRESHOLD:.2f} <= hentai + porn < {NSFW_THRESHOLD:.2f} → UNCERTAIN")
    lines.append(f"          hentai + porn < {UNCERTAIN_THRESHOLD:.2f} → SFW")
    lines.append(f"（sexy は NSFW 判定に含めません）\n")

    lines.append("## サマリー")
    lines.append(f"- 総画像数: {len(results)} 枚")
    lines.append(f"- NSFW: {len(nsfw_items)} 枚")
    lines.append(f"- UNCERTAIN: {len(uncertain_items)} 枚")
    lines.append(f"- SFW: {len(sfw_items)} 枚\n")

    if nsfw_items:
        lines.append("## NSFW（移動対象）")
        lines.append(f"以下の {len(nsfw_items)} 枚は NSFW と判定されました。")
        lines.append("`photo_nsfw_apply.py` で指定ディレクトリへ移動してください。\n")
        for i, r in enumerate(nsfw_items, 1):
            lines.append(f"### {i}. `{r['filename']}`")
            lines.append(f"- パス: `{r['path']}`")
            lines.append(f"- NSFWスコア: {r['score']:.3f} (hentai={r['hentai']:.3f}, porn={r['porn']:.3f})")
            lines.append(f"- 全スコア: drawing={r['drawing']:.3f}, neutral={r['neutral']:.3f}, sexy={r['sexy']:.3f}")
            lines.append("")

    if uncertain_items:
        lines.append("## UNCERTAIN（目視確認推奨）")
        lines.append(f"以下の {len(uncertain_items)} 枚は判定が曖昧です。目視で確認してくだい。\n")
        for i, r in enumerate(uncertain_items, 1):
            lines.append(f"### {i}. `{r['filename']}`")
            lines.append(f"- パス: `{r['path']}`")
            lines.append(f"- NSFWスコア: {r['score']:.3f} (hentai={r['hentai']:.3f}, porn={r['porn']:.3f})")
            lines.append(f"- 全スコア: drawing={r['drawing']:.3f}, neutral={r['neutral']:.3f}, sexy={r['sexy']:.3f}")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📝 レポート保存: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="NSFW判定スキャンツール（nsfwpy使用）")
    parser.add_argument("base_dir", help="基準ディレクトリ（pic-classifier など）")
    parser.add_argument("--report", default=None, help="レポート出力パス（デフォルト: {base_dir}_nsfw_report.md）")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数（デフォルト: 4）")
    args = parser.parse_args()

    # nsfwpy の事前チェック
    if not check_nsfwpy():
        sys.exit(1)

    base_dir = Path(args.base_dir).expanduser().resolve()
    if not base_dir.exists():
        print(f"ERROR: ディレクトリが存在しません: {base_dir}")
        sys.exit(1)

    report_path = Path(args.report if args.report else f"{base_dir.name}_nsfw_report.md")

    print(f"📂 基準ディレクトリ: {base_dir}")
    print(f"📝 レポート出力: {report_path}")

    images = collect_images(base_dir)
    if not images:
        print("ERROR: 対象画像が見つかりませんでした。")
        sys.exit(1)

    print(f"📁 対象画像数: {len(images)} 枚")
    print(f"   並列ワーカー: {args.workers}（各ワーカーでモデルを1回だけ初期化）\n")

    results = []
    processed = 0

    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker) as executor:
        futures = {executor.submit(scan_single_image, img): img for img in images}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
            processed += 1
            if processed % 50 == 0:
                print(f"   ... {processed}/{len(images)} 枚処理完了")

    print(f"\n✅ スキャン完了: {len(results)}/{len(images)} 枚を判定")
    generate_report(results, report_path)
    print("\n💡 移動は photo_nsfw_apply.py で行ってください。")


if __name__ == "__main__":
    main()
