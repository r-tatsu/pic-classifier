#!/usr/bin/env perl
use strict;
use warnings;
use File::Basename;
use File::Copy;
use Image::Size;

# Directories
my $source_dir = $ARGV[0] // '';
if (!$source_dir) {
    print "source?: ";
    chomp($source_dir = <STDIN>);
}
my $portrait_dir = './portrait';
my $landscape_dir = './landscape';

# Create directories if they don't exist
mkdir $portrait_dir unless -d $portrait_dir;
mkdir $landscape_dir unless -d $landscape_dir;

# Open source directory
opendir(my $dh, $source_dir) or die "Cannot open directory: $!";
my @files = grep { /\.(png|jpg)$/i && -f "$source_dir/$_" } readdir($dh);
closedir($dh);

foreach my $file (@files) {
    my $file_path = "$source_dir/$file";
    my ($width, $height) = imgsize($file_path);

    if ($width && $height) {
        if ($height > $width) {
            move($file_path, "$portrait_dir/$file") or die "Failed to move $file: $!";
        } else {
            move($file_path, "$landscape_dir/$file") or die "Failed to move $file: $!";
        }
    } else {
        warn "Could not determine size of $file_path\n";
    }
}