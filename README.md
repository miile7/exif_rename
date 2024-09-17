# Exif Rename

Exif_rename is a command line program to automatically rename images and videos according to their recording time.

## Features

- Image support (`IMG_` prefix by default)
- Video support (`VID_` prefix by default)
- Timezone support (source and target timezone)
- Time modification support
- Glob support
- Filter by metadata

## Installation

```shell
git clone https://github.com/miile7/exif_rename.git
```

## Usage

```shell
python3 exif_rename <path>
```

```txt
usage: exif_rename.py [-h] [--recursive] [--glob] [--dry] [--ignore-timezone] [--target-timezone timezone] [--verbose] [--filter-meta exif-key value] [--list-meta]
                      [--modify-time {weeks|days|hours|minutes|seconds} value] [--prefix PREFIX] [--suffix SUFFIX] [--time-format TIME_FORMAT]
                      path

positional arguments:
  path                  Path to file or directory or the glob pattern if --glob is used.

options:
  -h, --help            show this help message and exit
  --recursive, -r       Recursively process all files if the PATH is a directory. If --glob is used, this flag sets whether recursive globbing is supported.
  --glob, -g            Use PATH as a glob pattern instead of a single file or directory.
  --dry                 Dry run, do not modify any file
  --ignore-timezone     Ignore timezone information in EXIF data and treat all times as UTC. Not recommended. Only use this if the timezone information is incorrect.
  --target-timezone timezone
                        Convert all times to the given timezone. The timezone must be a valid IANA timezone name or a plus (or minus) followed by one or two digits (e.g. +02) indicating hours or by     
                        four digits indicating hours and minutes (e.g. +0200). If this option is used, all times are converted to the given timezone.
  --verbose, -v
  --filter-meta exif-key value
                        Filter files by metadata tags. If passed multiple times, all filters must match
  --list-meta, -l       List all metadata tags
  --modify-time {weeks|days|hours|minutes|seconds} value
                        Modify the creation time of the files by the given value
  --prefix PREFIX       Change the prefix of the file names
  --suffix SUFFIX       Add a suffix to the file names
  --time-format TIME_FORMAT
                        Change the date format of the file names (python strftime format)
```