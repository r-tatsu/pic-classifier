# pic-classifier

画像の分類と重複検出を行うツール群です。

## 構成

| ファイル | 役割 |
|---------|------|
| `classify_images.pl` | source ディレクトリ内の画像を縦長/横長に分類し、landscape/portrait/square に振り分ける |
| `photo_dedup.py` | 画像の重複を検出し、レポートを生成する（ファイルは一切触れない） |
| `photo_dedup_apply.py` | レポートに基づいて、確定重複の削除・候補の移動・リストアを実行する |

## classify_images.pl

source ディレクトリ内の画像を縦横比で分類し、以下のサブディレクトリに振り分けます。

- `landscape/` : 横長画像
- `portrait/` : 縦長画像
- `square/` : 正方形に近い画像

```sh
./classify_images.pl source
```

## photo_dedup（重複検出）

### 設計方針

- **確定重複**: ファイルハッシュ(SHA256)完全一致のみ。これだけが自動削除OK。
- **重複候補**: pHash近傍をORBで検証したもの。スコアは参考。すべて目視確認。
- **参考**: pHash近傍だがORBで一致しなかったもの。

### Requirements

```sh
pip install pillow pillow-avif-plugin opencv-python-headless imagehash
```

### Usage

#### 1. 画像分類

```sh
./classify_images.pl source
```

#### 2. 重複検出（レポート生成）

pic-classifier ディレクトリ全体を対象に重複を検出します。

```sh
python photo_dedup.py ./pic-classifier
```

`pic-classifier_report.md` と `pic-classifier_dedup.db` が生成されます。DBは差分更新されます。

#### 3. 削除・移動の確認（dry-run）

```sh
python photo_dedup_apply.py pic-classifier --dry-run
```

#### 4. 本番実行（確定重複を削除 + 候補を review へ移動）

```sh
python photo_dedup_apply.py pic-classifier --yes
```

重複候補は `review_YYYY-MM-DD/` に `group_xxx_` プレフィックス付きで移動されます。

#### 5. review 内で目視確認後、source へリストア

```sh
# dry-run で確認
python photo_dedup_apply.py pic-classifier --restore --review-dir ./review_YYYY-MM-DD --dry-run

# 本番リストア（group_xxx_ プレフィックスは自動除去）
python photo_dedup_apply.py pic-classifier --restore --review-dir ./review_YYYY-MM-DD --yes
```

リストア後、review ディレクトリが空になっていれば自動削除されます。

### Safety Features

- Differential update: existing DB is reused, only new files are scanned
- `photo_dedup.py` never touches the filesystem; it only generates reports
- `photo_dedup_apply.py` requires `--yes` for destructive operations
- `--dry-run` available for all destructive operations
- Restore function removes `group_xxx_` prefix and cleans up empty review directories
