Script for Classifying Images into Portrait, Landscape, and Square Formats

This script classifies image files in a specified directory into portrait, landscape, and square formats, then moves them to their respective directories.

Requirements

Perl 5.x
The following Perl modules:
File::Basename
File::Copy
Image::Size

```sh
sudo cpanm File::Basename File::Copy Image::Size
```

You need to grant execution permissions:
```sh
chmod +x /Users/december6701/pic-classifier/classify_images.pl
```

To run the script, use the following command:
```sh
./main.pl [source_dir]
```
