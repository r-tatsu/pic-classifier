## Photo Deduplication Tools (Python)

`photo_dedup.py` detects duplicate images using a 3-stage pipeline:

1. SHA-256 exact match
2. Perceptual hash (pHash) for near-duplicates
3. ORB feature matching for crop/rotation variants

### Requirements

```sh
pip install pillow pillow-avif-plugin opencv-python-headless imagehash
```

### Usage

```sh
# Scan directory and generate report
python photo_dedup.py ./source --workers 4

# Apply deletion (confirmed duplicates only, auto mode)
python photo_dedup_apply.py source --auto --yes
```

### Safety Features

- Differential update: existing DB is reused, only new files are scanned
- 3-tier report separates confirmed duplicates from uncertain matches
- `photo_dedup_apply.py` only auto-deletes ORB 100% matches by default
- Dry-run mode available: `--dry-run`

