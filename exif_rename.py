from argparse import ArgumentParser, Namespace
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from glob import glob
from logging import DEBUG, ERROR, INFO, basicConfig, getLogger
from os import getcwd
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Sequence, Tuple, Type

try:
    from piexif import load as piexif_load
except ModuleNotFoundError:
    piexif_load = None

try:
    from ffmpeg import (
        probe,
        input as ffmpeg_input,
        output as ffmpeg_output,
        Error as FfmpegError,
    )
except ModuleNotFoundError:
    probe = None


ROOT = Path(__file__).parent
SUPPORTED_TIME_MODIFICATION_KEYS = ("weeks", "days", "hours", "minutes", "seconds")


logger = getLogger("rename")


class Status(Enum):
    ERROR = 0
    RENAMED = 1
    UNCHANGED = 2


class Extractable:
    path: Path
    args: Namespace
    prefix: str | None = None
    suffix: str | None = None
    time_format: str = "%Y%m%d_%H%M%S"
    extensions: List[str] = []

    def __init__(self, path: Path, args: Namespace) -> None:
        self.path = path
        self.args = args

    @staticmethod
    def add_args(parser: ArgumentParser) -> None:
        pass

    def get_creation_time(self) -> datetime:
        raise NotImplementedError()

    def use(self) -> bool:
        logger.debug(f"Checking {self.path.suffix.lower()} part of {self.extensions}")
        return self.path.suffix.lower() in self.extensions

    def matches(self, filters: Dict[str, Any]) -> bool:
        exif_dict = self.list()
        for key, value in filters.items():
            if key in exif_dict:
                if str(exif_dict[key]) != str(value):
                    return False
            else:
                return False

        return True

    def list(self) -> Dict[str, Any]:
        raise NotImplementedError()

    def get_name(
        self,
        dt: datetime,
        counter: int | None = None,
        dir: Path | None = None,
    ) -> Path:
        fmt = self.time_format

        if self.prefix:
            fmt = f"{self.prefix}{fmt}"

        if counter:
            fmt = f"{fmt}_{counter}"

        if self.suffix:
            fmt = f"{fmt}{self.suffix}"

        if self.extensions:
            extensions = self.extensions
        else:
            extensions = filter(lambda p: p and p != ".", self.path.suffixes)

        fmt = f"{fmt}{''.join(extensions)}"

        if not dir:
            dir = self.path.parent

        return dir / dt.strftime(fmt)

    def before_rename(
        self, dt: datetime, dry: bool = False
    ) -> Tuple[Status, Path | None, datetime]:
        return Status.UNCHANGED, self.path, dt

    def after_rename(
        self, status: Status, path: Path, dt: datetime, dry: bool = False
    ) -> Tuple[Status, Path | None]:
        return Status.UNCHANGED, path

    def rename(self, dt: datetime, dry: bool = False) -> Tuple[Status, Path | None]:
        status, path, dt = self.before_rename(dt, dry)

        if status == Status.ERROR or path is None:
            return status, path

        target_name = self.get_name(dt)

        if target_name == path:
            logger.debug(f"Name {target_name.name} already correct")
            return self.after_rename(Status.UNCHANGED, path, dt, dry)

        i = 2
        while target_name.exists():
            target_name = self.get_name(dt, counter=i)
            if target_name == path:
                logger.debug(f"Name {target_name.name} already correct")
                return self.after_rename(Status.UNCHANGED, path, dt, dry)
            i += 1

        if not dry:
            return self.after_rename(Status.RENAMED, path.rename(target_name), dt, dry)

        return self.after_rename(Status.UNCHANGED, path, dt, dry)


class Video(Extractable):
    def __init__(self, path: Path, args: Namespace) -> None:
        super().__init__(path, args)
        self.prefix = "VID_"
        self.extensions = [".mp4"]

    @staticmethod
    def add_args(parser: ArgumentParser) -> None:
        parser.add_argument(
            "--video-thumbnail-skip-creation",
            action="store_true",
            default=False,
            help="Skip thumbnail creation for videos",
        )
        parser.add_argument(
            "--video-thumbnail-width",
            type=int,
            default=320,
            help="Width of the thumbnail",
        )

    def get_creation_time(self) -> datetime:
        tags = self.list()
        try:
            iso_time = tags["streams"][0]["tags"]["creation_time"]
        except KeyError as e:
            raise LookupError(
                f"Could not find creation time tag for file {self.path.name}"
            )

        return datetime.strptime(iso_time, "%Y-%m-%dT%H:%M:%S.%f%z")

    def list(self) -> Dict[str, Any]:
        if probe is None:
            raise NotImplementedError("ffmpeg is not installed")

        try:
            return probe(str(self.path))
        except FfmpegError as e:
            logger.error(e.stderr.decode())
            raise e

    def after_rename(
        self, status: Status, path: Path, dt: datetime, dry: bool = False
    ) -> Tuple[Status, Path | None]:
        status, new_path = super().after_rename(status, path, dt, dry)

        if new_path is None or status == Status.ERROR:
            return status, new_path

        if not self.args.video_thumbnail_skip_creation:
            logger.debug(f"Creating thumbnail for {new_path}")
            tmp_path = new_path.with_suffix(f".output{new_path.suffix}")

            if not dry:
                try:
                    thumbnail_path = new_path.with_suffix(".jpg")
                    (
                        ffmpeg_input(str(new_path), ss=0.1)
                        .filter("thumbnail")
                        .output(str(thumbnail_path), vframes=1)
                        .overwrite_output()
                        .run(capture_stdout=True, capture_stderr=True)
                    )
                    (
                        ffmpeg_output(
                            ffmpeg_input(str(new_path)),
                            ffmpeg_input(str(thumbnail_path)),
                            str(tmp_path),
                            c="copy",
                            map_metadata=0,
                            **{"c:v:1": "mjpeg", "disposition:v:1": "attached_pic"},
                        )
                        .global_args("-map", "0", "-map", "1")
                        .overwrite_output()
                        .run(capture_stdout=True, capture_stderr=True)
                    )
                except FfmpegError as e:
                    logger.error(e.stderr.decode())
                    return Status.ERROR, new_path

            if tmp_path.exists() and tmp_path.stat().st_size >= new_path.stat().st_size:
                thumbnail_path.unlink()
                new_path.unlink()
                tmp_path.rename(new_path)

        return status, new_path


class Image(Extractable):
    def __init__(self, path: Path, args: Namespace) -> None:
        super().__init__(path, args)
        self.prefix = "IMG_"
        self.extensions = [".jpg", ".jpeg", ".png"]

        self.date_keys = OrderedDict(
            [
                ("Exif", [("DateTimeOriginal", 36867), ("DateTimeDigitized", 36868)]),
                ("GPS", [("GPSDateStamp", 29)]),
                ("0th", [("DateTime", 306)]),
                ("1st", [("DateTime", 306)]),
            ]
        )
        self.date_formats = [
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y%m%d%H%M%S",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]
        self.timezone_keys = {
            "0th": [("TimeZoneOffset", 34858)],
            "1st": [("TimeZoneOffset", 34858)],
            "Exif": [
                ("OffsetTime", 36880),
                ("OffsetTimeOriginal", 36881),
                ("OffsetTimeDigitized", 36882),
            ],
        }
        self.timezone_formats = ["+%H:%M", "%H:%M", "+%H", "%H"]

    def _get_time_zone(self, group: str, exif_dict: Dict[str, Any]) -> timezone | None:
        if group in self.timezone_keys:
            for _, key in self.timezone_keys[group]:
                if key in exif_dict[group]:
                    timezone_str = exif_dict[group][key].decode("utf-8")
                    for format in self.timezone_formats:
                        try:
                            tz_dt = datetime.strptime(timezone_str, format)
                            return timezone(
                                timedelta(hours=tz_dt.hour, minutes=tz_dt.minute)
                            )
                        except ValueError:
                            pass
        return None

    def get_creation_time(self) -> datetime:
        exif_dict = self.list()

        for group, keys in self.date_keys.items():
            for _, key in keys:
                if group in exif_dict and key in exif_dict[group]:
                    time_str = exif_dict[group][key].decode("utf-8")
                    for format in self.date_formats:
                        try:
                            dt = datetime.strptime(time_str, format)
                        except ValueError:
                            pass

                        tzinfo = self._get_time_zone(group, exif_dict)
                        if tzinfo:
                            dt = dt.replace(tzinfo=tzinfo)
                        return dt

        raise LookupError(f"Could not find any time tag for file {self.path.name}")

    def list(self) -> Dict[str, Any]:
        if piexif_load is None:
            raise NotImplementedError("piexif is not installed")

        exif_dict = piexif_load(str(self.path))
        del exif_dict["thumbnail"]
        return exif_dict


PARSERS: List[Type[Extractable]] = [Image, Video]


def parse_timedelta(args: List[str]) -> timedelta | None:
    if not args:
        return None

    key, value = args
    if key not in SUPPORTED_TIME_MODIFICATION_KEYS:
        logger.error(f"Unsupported time modification key {key}")
        return None

    return timedelta(**{key: int(value)})


def parse_timezone(timezone_expr: str | None) -> timezone | None:
    if not timezone_expr:
        return None

    timezone_expr = timezone_expr.strip()
    if timezone_expr[0] == "+":
        timezone_expr = timezone_expr[1:]

        if len(timezone_expr) == 1 or len(timezone_expr) == 2:
            return timezone(timedelta(hours=int(timezone_expr)))
        elif len(timezone_expr) >= 4:
            tz_dt = datetime.strptime(timezone_expr, "%z")
            return timezone(timedelta(hours=tz_dt.hour, minutes=tz_dt.minute))
        else:
            raise ValueError("Invalid timezone format")

    tz_dt = datetime.strptime(timezone_expr, "%Z")
    return timezone(timedelta(hours=tz_dt.hour, minutes=tz_dt.minute))


def parse_filters(args: List[Tuple[str, str]]) -> Dict[str, Any] | None:
    if not args:
        return None

    return dict(args)


def print_meta(meta: Dict[str, Any], prefix: List[str] = []) -> None:
    for key, value in meta.items():
        path = prefix + [str(key)]
        if isinstance(value, dict):
            print_meta(value, path)
        else:
            print(f"  {'/'.join(path)}: {value}")


def execute_by_path(
    path: Path,
    args: Namespace,
    modify_time: timedelta | None,
    target_timezone: timezone | None,
    filters: Dict[str, Any] | None,
) -> Status:
    for parser in PARSERS:
        parser_inst = parser(path, args)
        if args.prefix:
            parser_inst.prefix = args.prefix
        if args.suffix:
            parser_inst.suffix = args.suffix
        if args.time_format:
            parser_inst.time_format = args.time_format

        if parser_inst.use() and (not filters or parser_inst.matches(filters)):
            creation_time = parser_inst.get_creation_time()
            if modify_time:
                creation_time += modify_time
            if target_timezone:
                creation_time = creation_time.astimezone(target_timezone)
            elif args.ignore_timezone:
                creation_time = creation_time.astimezone(timezone.utc)

            if args.list_meta:
                logger.info(path)
                logger.info(f"  *Used creation time*: {creation_time}")
                print_meta(parser_inst.list())
                return Status.UNCHANGED
            else:
                status, new_path = parser_inst.rename(creation_time, dry=args.dry)
                if status == Status.RENAMED and new_path:
                    logger.info(f"[{path.parent}]: {path.name} -> {new_path.name}")
                return status

    return Status.ERROR


def _walk(path: Path) -> Generator[Path, None, None]:
    if path.is_dir():
        for p in path.iterdir():
            yield from _walk(p)
    else:
        yield path


def collect_files(
    path: str,
    args: Namespace,
) -> Generator[Path, None, None]:
    if args.glob:
        for path in glob(path, root_dir=getcwd(), recursive=args.recursive):
            yield Path(path)
    elif args.recursive:
        yield from _walk(Path(path))
    elif Path(path).is_dir():
        yield from filter(lambda p: p.is_file(), Path(path).iterdir())
    else:
        yield Path(path)


def get_arguments(args: Sequence[str] | None = None) -> Namespace:
    parser = ArgumentParser()

    parser.add_argument(
        "path",
        help=("Path to file or directory or the glob pattern if --glob is used."),
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=False,
        help=(
            "Recursively process all files if the PATH is a directory. If --glob is used, this "
            "flag sets whether recursive globbing is supported."
        ),
    )
    parser.add_argument(
        "--glob",
        "-g",
        action="store_true",
        default=False,
        help="Use PATH as a glob pattern instead of a single file or directory.",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        default=False,
        help="Dry run, do not modify any file",
    )
    parser.add_argument(
        "--ignore-timezone",
        action="store_true",
        default=False,
        help=(
            "Ignore timezone information in EXIF data and treat all times as UTC. Not recommended. "
            "Only use this if the timezone information is incorrect."
        ),
    )
    parser.add_argument(
        "--target-timezone",
        metavar="timezone",
        help=(
            "Convert all times to the given timezone. The timezone must be a valid IANA timezone "
            "name or a plus (or minus) followed by one or two digits (e.g. +02) indicating hours "
            "or by four digits indicating hours and minutes (e.g. +0200). If this option is used, "
            "all times are converted to the given timezone."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", dest="level", action="store_const", const=DEBUG, default=INFO
    )
    parser.add_argument(
        "--filter-meta",
        action="append",
        nargs=2,
        metavar=("exif-key", "value"),
        help="Filter files by metadata tags. If passed multiple times, all filters must match",
    )
    parser.add_argument(
        "--list-meta",
        "-l",
        action="store_true",
        default=False,
        help="List all metadata tags",
    )
    parser.add_argument(
        "--modify-time",
        nargs=2,
        metavar=(f"{{{'|'.join(SUPPORTED_TIME_MODIFICATION_KEYS)}}}", "value"),
        help="Modify the creation time of the files by the given value",
    )
    parser.add_argument(
        "--prefix",
        help="Change the prefix of the file names",
    )
    parser.add_argument(
        "--suffix",
        help="Add a suffix to the file names",
    )
    parser.add_argument(
        "--time-format",
        help="Change the date format of the file names (python strftime format)",
    )

    for p in PARSERS:
        if hasattr(p, "add_args"):
            p.add_args(parser)

    return parser.parse_args(args)


def main(debug_args: Sequence[str] | None = None) -> None:
    args = get_arguments(debug_args)

    if args.level == INFO:
        basicConfig(level=args.level, format="%(message)s")
    else:
        basicConfig(level=args.level, format="%(levelname)s: %(message)s")

    stats = defaultdict(int)
    for file in collect_files(args.path, args):
        status = execute_by_path(
            file,
            args,
            parse_timedelta(args.modify_time),
            parse_timezone(args.target_timezone),
            parse_filters(args.filter_meta),
        )
        stats[status] += 1

    logger.info(f"Processed {sum(stats.values())} files")
    for status, count in stats.items():
        logger.info(f"  {status.name}: {count}")


if __name__ == "__main__":
    main()
