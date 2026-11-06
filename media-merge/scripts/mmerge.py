#!/usr/bin/env python3
"""mmerge —— 探测输入参数，选最省的拼接方案，打印或执行 ffmpeg 命令。

两条流水线共用一个骨架（探测 -> 定档 -> 逐段落盘 -> concat 拼接）：

    视频  copy / encode-minor / normalize，看几何与视频编码
    音频  copy / encode-minor / normalize，看编码、采样率、声道数

用法见 ``--help``。
"""

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TypeVar

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

T = TypeVar("T", bound=Hashable)

#: 无损编码。源全是无损时目标也走无损，绝不降到有损。
LOSSLESS = frozenset({"flac", "alac", "wavpack", "ape", "truehd", "mlp"})

#: 目标编码 -> 推荐封装
CONTAINER = {
    "mp3": "mp3",
    "aac": "m4a",
    "alac": "m4a",
    "flac": "flac",
    "vorbis": "ogg",
    "opus": "opus",
    "ac3": "ac3",
    "eac3": "eac3",
    "mp2": "mp2",
}

#: 重编码时需要显式指定的编码器（编码器名不一定等于编码名）
ENCODER = {"mp3": "libmp3lame", "aac": "aac", "flac": "flac"}

MP4_SUFFIX = frozenset({".mp4", ".m4a", ".m4v", ".mov"})

WORK_DIR = ".mmerge-tmp"


# --------------------------------------------------------------------------- #
# 探测
# --------------------------------------------------------------------------- #
@dataclass
class Info:
    """单个输入文件的探测结果。音频文件除时长与音频字段外均为占位值。"""

    path: Path
    kind: str
    vcodec: str
    profile: str
    width: int
    height: int
    fps: Fraction
    pix_fmt: str
    sar: str
    color: str
    tb_den: int
    frames: int
    duration: float
    acodec: str
    abitrate: int
    sample_rate: int
    channels: int

    @property
    def res(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def fps_text(self) -> str:
        """帧率的可读形式，整数帧率不带 /1。"""
        frac = self.fps
        if frac.denominator == 1:
            return str(frac.numerator)
        return f"{frac.numerator}/{frac.denominator}"

    @property
    def has_audio(self) -> bool:
        return self.acodec != "-"

    @property
    def audio_key(self) -> tuple[str, int, int]:
        """音频签名的三元组，判定一致性的最小集合。"""
        return (self.acodec, self.sample_rate, self.channels)

    @property
    def audio_text(self) -> str:
        if not self.has_audio:
            return "无音轨"
        return f"{self.acodec} {self.sample_rate}/{self.channels}ch"

    @property
    def kbps_text(self) -> str:
        return f"{self.abitrate // 1000}" if self.abitrate else "?"


def _split_den(text: str, default: int = 1) -> int:
    """把 "1/15360" 拆成 15360；无法解析时返回默认值。

    不用 try/except 捕获元组，是因为 ruff formatter 在 target-version 为
    py314 时会按 PEP 758 去掉括号，产出旧解释器无法解析的语法。
    """
    parts = str(text).split("/")
    if len(parts) != 2 or not parts[1].isdigit():
        return default
    value = int(parts[1])
    return value if value > 0 else default


def _parse_fps(text: str) -> Fraction:
    """解析 "30/1" 形式的帧率；无法解析或分母为零时返回 0。"""
    num, _, den = str(text).partition("/")
    if not num.isdigit() or not den.isdigit() or int(den) == 0:
        return Fraction(0)
    return Fraction(int(num), int(den))


def _int(source: object, default: int = 0) -> int:
    """把 ffprobe 里的字符串或整数转成 int，转不动给默认值。"""
    if source is None:
        return default
    try:
        return int(str(source))
    except ValueError:
        return default


def probe(path: Path) -> Info:
    """用 ffprobe 读一个文件的视频、音频与容器信息。"""
    result = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])

    # 封面图会以 mjpeg/png 视频流的形式挂在音频文件上，不能当真视频
    video = None
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition") or {}
        if _int(disposition.get("attached_pic")) == 1:
            continue
        video = stream
        break
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None and audio is None:
        raise SystemExit(f"错误：{path} 既没有视频流也没有音频流")

    fmt = data.get("format", {})
    duration = 0.0
    carrier = video if video is not None else audio
    for source in (fmt.get("duration"), (carrier or {}).get("duration")):
        if source is None:
            continue
        try:
            duration = float(source)
        except ValueError:
            continue
        break

    if video is None:
        stream = audio or {}
        return Info(
            path=path,
            kind="audio",
            vcodec="-",
            profile="-",
            width=0,
            height=0,
            fps=Fraction(0),
            pix_fmt="-",
            sar="-",
            color="-",
            tb_den=_split_den(stream.get("time_base", "1/44100"), 44100),
            frames=0,
            duration=duration,
            acodec=str(stream.get("codec_name", "?")),
            abitrate=_int(stream.get("bit_rate")),
            sample_rate=_int(stream.get("sample_rate")),
            channels=_int(stream.get("channels")),
        )

    sar = video.get("sample_aspect_ratio") or "1:1"
    if sar == "N/A":
        sar = "1:1"

    return Info(
        path=path,
        kind="video",
        vcodec=str(video.get("codec_name", "?")),
        profile=str(video.get("profile", "?")),
        width=_int(video.get("width")),
        height=_int(video.get("height")),
        fps=_parse_fps(video.get("r_frame_rate", "0/1")),
        pix_fmt=str(video.get("pix_fmt", "?")),
        sar=sar,
        color=str(video.get("color_space") or "unknown"),
        tb_den=_split_den(video.get("time_base", "1/15360")),
        frames=_int(video.get("nb_frames")),
        duration=duration,
        acodec=str(audio.get("codec_name", "-")) if audio else "-",
        abitrate=_int(audio.get("bit_rate")) if audio else 0,
        sample_rate=_int(audio.get("sample_rate")) if audio else 0,
        channels=_int(audio.get("channels")) if audio else 0,
    )


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def hms(seconds: float) -> str:
    """秒数转 h:mm:ss.ss。"""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _uniq(values: list) -> list:
    """按首次出现顺序去重。"""
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _same(values: list) -> bool:
    """所有值是否一致。"""
    return len(_uniq(values)) == 1


def majority[T: Hashable](infos: list[Info], key: Callable[[Info], T]) -> T | None:
    """按累计时长占比选出主参数。"""
    totals: dict[T, float] = {}
    for info in infos:
        totals[key(info)] = totals.get(key(info), 0.0) + info.duration
    if not totals:
        return None
    return max(totals.items(), key=lambda kv: kv[1])[0]


def is_lossless(codec: str) -> bool:
    """是否无损编码；WAV 一类是 pcm_* 前缀。"""
    return codec in LOSSLESS or codec.startswith("pcm_")


def natural_container(codec: str) -> str:
    """按编码选一个能装的封装。未知编码退到 mka。"""
    if codec.startswith("pcm_"):
        return "wav"
    return CONTAINER.get(codec, "mka")


def guess_level(width: int, height: int, fps: float) -> str:
    """按像素数与帧率估一个够用的 H.264 level。"""
    pixels = width * height
    if pixels <= 921_600 and fps <= 30:
        return "3.1"
    if pixels <= 2_073_600 and fps <= 30:
        return "4.0"
    if pixels <= 2_073_600:
        return "4.2"
    return "5.1"


def even(value: int) -> int:
    """yuv420p 要求偶数边长，奇数就补一。"""
    return value + (value % 2)


def _p(path: Path) -> str:
    """绝对路径的统一写法：正斜杠，Windows 与 POSIX 下都能直接喂给 ffmpeg。"""
    return path.resolve().as_posix()


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
GEOMETRY_FIELDS = ("width", "height", "fps", "pix_fmt", "sar", "color")


def decide_video_mode(infos: list[Info]) -> str:
    """视频三档判定。

    音轨也必须一致：`-c copy` 拼接要求同一条轨道里编码相同。只差音轨时若让
    视频段直接 copy，成品会是一条混编码音轨，所以音轨差异一律走 normalize。
    """
    geometry_same = all(
        _same([getattr(info, field) for info in infos]) for field in GEOMETRY_FIELDS
    )
    audio_same = _same([info.audio_key for info in infos])
    if geometry_same and audio_same and _same([i.vcodec for i in infos]):
        return "copy"
    if geometry_same and audio_same:
        return "encode-minor"
    return "normalize"


def decide_audio_mode(infos: list[Info]) -> str:
    """音频三档：签名全同 copy；仅编码不同只重编少数；采样率或声道不同全重编。"""
    if _same([info.audio_key for info in infos]):
        return "copy"
    if _same([info.acodec for info in infos]):
        return "normalize"
    if _same([(info.sample_rate, info.channels) for info in infos]):
        return "encode-minor"
    return "normalize"


# --------------------------------------------------------------------------- #
# 目标与计划
# --------------------------------------------------------------------------- #
@dataclass
class AudioTarget:
    """音频目标参数。encoder 为 None 表示流拷贝。"""

    codec: str
    encoder: str | None
    sample_rate: int
    channels: int
    bitrate_k: int | None
    lossless: bool

    @property
    def container(self) -> str:
        return natural_container(self.codec)

    @property
    def summary(self) -> str:
        if self.encoder is None:
            return f"{self.codec}（流拷贝）"
        if self.bitrate_k is None:
            return f"{self.codec}（{self.encoder}，无损）"
        return f"{self.codec}（{self.encoder}，{self.bitrate_k}k）"


@dataclass
class VideoTarget:
    """视频目标参数。"""

    codec: str
    canvas: tuple[int, int]
    fps: Fraction
    level: str
    preset: str
    crf: int
    tb_den: int
    pix_fmt: str
    audio: AudioTarget | None


@dataclass
class Plan:
    """判定结果与执行计划。"""

    kind: str
    mode: str
    out: Path
    reason: str
    duration: float
    target: VideoTarget | AudioTarget
    encodes: list[list[str]]
    concat: list[str]
    list_file: Path | None
    entries: list[str]


def _audio_bitrate(infos: list[Info], codec: str) -> int:
    """目标码率：跟住源码率并留 25% 余量，再夹到该编码的合理区间。

    只参考有损源——WAV 的 768 kbps 是 PCM 的裸速率，拿它定档会把 128k 的 mp3
    重编成 320k，白占体积又补不回信息。
    """
    lossy = [info.abitrate for info in infos if not is_lossless(info.acodec)]
    source = max(lossy, default=0) // 1000
    base = round(source * 1.25) if source else 192
    cap = 320 if codec == "mp3" else 256
    return min(max(base, 128), cap)


def _video_audio_target(infos: list[Info]) -> AudioTarget | None:
    """视频流水线的音轨目标：统一 AAC，采样率与声道取多数派。"""
    present = [info for info in infos if info.has_audio]
    if len(present) != len(infos):
        return None
    sample_rate = majority(infos, lambda i: i.sample_rate) or 44_100
    channels = majority(infos, lambda i: i.channels) or 2
    return AudioTarget(
        codec="aac",
        encoder="aac",
        sample_rate=sample_rate,
        channels=channels,
        bitrate_k=192,
        lossless=False,
    )


def build_video_plan(
    infos: list[Info],
    out: Path,
    canvas: str | None,
    crf: int,
    preset: str,
) -> Plan:
    """按视频探测结果组装转码与拼接命令。"""
    mode = decide_video_mode(infos)
    main = max(infos, key=lambda i: i.duration)

    width, height = main.width, main.height
    if canvas:
        width, height = (int(part) for part in canvas.lower().split("x"))
    width, height = even(width), even(height)

    vcodec = majority(infos, lambda i: i.vcodec) or main.vcodec
    track = [
        info
        for info in infos
        if info.vcodec == vcodec
        and (info.width, info.height) == (main.width, main.height)
    ]
    tb_den = (track[0] if track else main).tb_den
    level = guess_level(width, height, float(main.fps))
    audio = _video_audio_target(infos)
    if mode == "copy":
        reason = "参数一致，直接流拷贝，视频与音轨都原样保留"
    elif audio is None:
        reason = "部分文件无音轨，输出改为纯视频（-an）"
    else:
        reason = f"音轨统一为 {audio.summary}"

    work = out.parent / WORK_DIR
    seg_dir = work / "segs"
    list_file = work / "list.txt"

    encodes: list[list[str]] = []
    entries: list[str] = []

    if mode == "copy":
        entries = [_p(info.path) for info in infos]
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"flags=bicubic,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={main.fps_text},format={main.pix_fmt}"
        )
        for info in infos:
            if mode == "encode-minor" and info.vcodec == vcodec:
                entries.append(_p(info.path))
                continue
            target = seg_dir / f"{info.path.stem}.mp4"
            cmd = [FFMPEG, "-y", "-i", _p(info.path)]
            if mode == "normalize":
                cmd += ["-vf", vf]
            cmd += [
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-profile:v",
                "high",
                "-level",
                level,
                "-pix_fmt",
                str(main.pix_fmt),
            ]
            if audio is None:
                cmd += ["-an"]
            else:
                cmd += [
                    "-c:a",
                    str(audio.encoder),
                    "-b:a",
                    f"{audio.bitrate_k}k",
                    "-ar",
                    str(audio.sample_rate),
                    "-ac",
                    str(audio.channels),
                ]
            if target.suffix in MP4_SUFFIX:
                cmd += ["-video_track_timescale", str(tb_den)]
            cmd += [_p(target)]
            encodes.append(cmd)
            entries.append(_p(target))

    concat = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", _p(list_file)]
    concat += ["-c", "copy"]
    if out.suffix.lower() in MP4_SUFFIX:
        concat += ["-movflags", "+faststart", "-video_track_timescale", str(tb_den)]
    concat += [_p(out)]

    return Plan(
        kind="video",
        mode=mode,
        out=out,
        reason=reason,
        duration=sum(info.duration for info in infos),
        target=VideoTarget(
            codec=vcodec,
            canvas=(width, height),
            fps=main.fps,
            level=level,
            preset=preset,
            crf=crf,
            tb_den=tb_den,
            pix_fmt=str(main.pix_fmt),
            audio=audio,
        ),
        encodes=encodes,
        concat=concat,
        list_file=list_file,
        entries=entries,
    )


def build_audio_plan(
    infos: list[Info],
    out: Path,
    bitrate_k: int | None,
) -> Plan:
    """按音频探测结果组装转码与拼接命令。"""
    missing = [info.path.name for info in infos if not info.has_audio]
    if missing:
        raise SystemExit(f"错误：以下文件没有音频流 -> {', '.join(missing)}")

    mode = decide_audio_mode(infos)
    main = majority(infos, lambda i: i.acodec) or infos[0].acodec
    sample_rate = max(info.sample_rate for info in infos)
    channels = max(info.channels for info in infos)

    codec, encoder, bit = main, None, None
    if mode == "copy":
        reason = "音频参数一致，直接流拷贝"
    elif all(is_lossless(info.acodec) for info in infos):
        codec, encoder, bit = "flac", "flac", None
        reason = "源均为无损，目标保持无损（flac）"
    elif main == "mp3" and sample_rate <= 48_000:
        codec, encoder = "mp3", "libmp3lame"
        bit = bitrate_k or _audio_bitrate(infos, codec)
        reason = f"主编码为 mp3，重编码仍回到 mp3（{bit}k）"
    else:
        codec, encoder = "aac", "aac"
        bit = bitrate_k or _audio_bitrate(infos, codec)
        reason = f"重编码统一为 aac（{bit}k）"

    if bitrate_k is not None and encoder is not None:
        bit = bitrate_k
        reason = f"按指定码率重编码为 {codec}（{bit}k）"

    container = natural_container(codec)
    if out.suffix.lower().lstrip(".") != container:
        out = out.with_suffix(f".{container}")

    audio = AudioTarget(
        codec=codec,
        encoder=encoder,
        sample_rate=sample_rate,
        channels=channels,
        bitrate_k=bit,
        lossless=encoder == "flac",
    )

    work = out.parent / WORK_DIR
    seg_dir = work / "segs"
    list_file = work / "list.txt"

    encodes: list[list[str]] = []
    entries: list[str] = []

    if mode == "copy":
        entries = [_p(info.path) for info in infos]
    else:
        for info in infos:
            if mode == "encode-minor" and info.acodec == codec:
                entries.append(_p(info.path))
                continue
            target = seg_dir / f"{info.path.stem}.{container}"
            cmd = [FFMPEG, "-y", "-i", _p(info.path)]
            cmd += ["-map", "0:a:0", "-c:a", str(encoder)]
            if bit is not None:
                cmd += ["-b:a", f"{bit}k"]
            if codec == "flac":
                cmd += ["-compression_level", "8"]
            cmd += [
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-af",
                "aresample=async=1:first_pts=0",
                _p(target),
            ]
            encodes.append(cmd)
            entries.append(_p(target))

    concat = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", _p(list_file)]
    concat += ["-c", "copy", "-map", "0:a:0"]
    if out.suffix.lower() in MP4_SUFFIX:
        concat += ["-movflags", "+faststart"]
    concat += [_p(out)]
    joined_by_filter = False

    # raw flac 封装不接受拼接后的比特流：-c copy 会把原文件 STREAMINFO 的块长套到
    # 后续数据上，解码器报 blocksize 越界，成品只认第一段。改走 concat 滤镜，
    # flac 对 flac 重编是无损的，代价可忽略。
    if codec == "flac":
        joined_by_filter = True
        graph = "".join(f"[{index}:a]" for index in range(len(entries)))
        graph += f"concat=n={len(entries)}:v=0:a=1[a]"
        concat = [FFMPEG, "-y"]
        for entry in entries:
            concat += ["-i", entry]
        concat += ["-filter_complex", graph, "-map", "[a]"]
        concat += ["-c:a", "flac", "-compression_level", "8", _p(out)]
        reason += "；拼接走 concat 滤镜（raw flac 无法 copy 拼接）"

    return Plan(
        kind="audio",
        mode=mode,
        out=out,
        reason=reason,
        duration=sum(info.duration for info in infos),
        target=audio,
        encodes=encodes,
        concat=concat,
        list_file=None if joined_by_filter else list_file,
        entries=entries,
    )


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _table_video(infos: list[Info]) -> None:
    head = (
        f"{'#':<3} {'FILE':<28} {'CODEC':<10} {'RES':<11} {'FPS':<7} {'DUR':<11} AUDIO"
    )
    print(head)
    print("-" * 78)
    for index, info in enumerate(infos, 1):
        name = info.path.name
        if len(name) > 27:
            name = name[:24] + "..."
        print(
            f"{index:<3} {name:<28} {info.vcodec:<10} {info.res:<11} "
            f"{info.fps_text:<7} {hms(info.duration):<11} {info.audio_text}"
        )


def _table_audio(infos: list[Info]) -> None:
    head = f"{'#':<3} {'FILE':<30} {'CODEC':<11} {'RATE':<8} {'CH':<4} {'KBPS':<6} DUR"
    print(head)
    print("-" * 78)
    for index, info in enumerate(infos, 1):
        name = info.path.name
        if len(name) > 29:
            name = name[:26] + "..."
        print(
            f"{index:<3} {name:<30} {info.acodec:<11} {info.sample_rate:<8} "
            f"{info.channels:<4} {info.kbps_text:<6} {hms(info.duration)}"
        )


def _diff_rows(infos: list[Info], kind: str) -> list[tuple[str, list, bool]]:
    """差异检测行：标签、取值列表、是否参与定档。"""
    if kind == "video":
        return [
            ("video codec", [i.vcodec for i in infos], True),
            ("resolution", [i.res for i in infos], True),
            ("framerate", [i.fps_text for i in infos], True),
            ("pix_fmt", [i.pix_fmt for i in infos], True),
            ("SAR", [i.sar for i in infos], True),
            ("colorspace", [i.color for i in infos], True),
            ("audio codec", [i.acodec for i in infos], True),
            ("sample rate", [i.sample_rate for i in infos], True),
            ("channels", [i.channels for i in infos], True),
        ]
    return [
        ("audio codec", [i.acodec for i in infos], True),
        ("sample rate", [i.sample_rate for i in infos], True),
        ("channels", [i.channels for i in infos], True),
        ("bitrate", [i.kbps_text for i in infos], False),
    ]


def _plan_lines(plan: Plan) -> list[str]:
    """判定结果的可读描述。"""
    lines: list[str] = []
    if plan.mode == "copy":
        lines.append("  模式        [copy] 参数一致 -> 流拷贝，零重编码")
        lines.append("  参数        -c copy")
    elif plan.mode == "encode-minor":
        lines.append("  模式        [encode-minor] 仅编码不同 -> 只重编少数段")
    else:
        scope = "几何或音轨" if plan.kind == "video" else "采样率或声道"
        lines.append(f"  模式        [normalize] {scope}不一致 -> 逐段归一化")

    target = plan.target
    if isinstance(target, VideoTarget):
        width, height = target.canvas
        encode = (
            f"-c:v libx264 -preset {target.preset} -crf {target.crf} "
            f"-profile:v high -level {target.level}"
        )
        if plan.mode == "copy":
            lines.append(f"  分辨率      {width}x{height}（原样）")
            lines.append(f"  帧率        {target.fps} fps")
            lines.append("  音频        原样保留")
        else:
            lines.append(f"  画布        {width}x{height}（等比缩放 + 居中补黑边）")
            lines.append(f"  帧率        {target.fps} fps CFR")
            lines.append(f"  编码参数    {encode}")
            audio = target.audio
            if audio is None:
                lines.append("  音频        无（-an）")
            else:
                lines.append(
                    f"  音频        {audio.summary} {audio.sample_rate}/{audio.channels}ch"
                )
        lines.append(f"  时间基      -video_track_timescale {target.tb_den}")
    else:
        lines.append(f"  编码        {target.summary}")
        lines.append(f"  采样率      {target.sample_rate}")
        lines.append(f"  声道        {target.channels}")
        lines.append(f"  封装        .{target.container}")
    lines.append(f"  说明        {plan.reason}")
    return lines


def report(infos: list[Info], plan: Plan) -> None:
    """打印分析表、差异检测、判定结果与确切命令。"""
    total = sum(info.duration for info in infos)
    label = "视频" if plan.kind == "video" else "音频"
    print("=" * 78)
    print(f" 输入分析（{len(infos)} 段，合计 {hms(total)}）")
    print("=" * 78)
    if plan.kind == "video":
        _table_video(infos)
    else:
        _table_audio(infos)
    print()

    print("-" * 78)
    print(" 差异检测")
    print("-" * 78)
    for name, values, decisive in _diff_rows(infos, plan.kind):
        unique = _uniq(values)
        flag = ("OK  " if len(unique) == 1 else "DIFF") if decisive else "info"
        shown = " ".join(str(value) for value in unique)
        print(f"  [{flag}] {name:<13} {shown}")
    print()

    print("-" * 78)
    print(f" 方案判定（{label}）")
    print("-" * 78)
    for line in _plan_lines(plan):
        print(line)
    print()

    print("-" * 78)
    print(" 生成命令")
    print("-" * 78)
    for cmd in plan.encodes:
        print("  " + shlex.join(cmd))
    if plan.encodes and plan.list_file is not None:
        print()
        print(f"  # concat 清单 -> {plan.list_file}")
        for entry in plan.entries:
            print(f"  file '{entry}'")
        print()
    print("  " + shlex.join(plan.concat))
    print()
    print(f"  输出 -> {plan.out}")


def execute(plan: Plan) -> int:
    """执行计划：先逐段转码，再拼接。"""
    # segs/ 必须无条件建好：ffmpeg 打不开输出文件时返回 127，看起来像「找不到命令」，
    # 实际原因是父目录不存在，排查时极易误判。
    seg_dir = plan.out.parent / WORK_DIR / "segs"
    seg_dir.mkdir(parents=True, exist_ok=True)
    if plan.list_file is not None:
        plan.list_file.write_text(
            "".join(f"file '{entry}'\n" for entry in plan.entries), encoding="utf-8"
        )
    for cmd in [*plan.encodes, plan.concat]:
        shown = Path(cmd[-1]).name
        print(f"[执行] {shown}")
        code = subprocess.run(cmd, check=False).returncode
        if code != 0:
            print(f"!! 失败（退出码 {code}）：{shlex.join(cmd)}", file=sys.stderr)
            return code
    return 0


def verify(plan: Plan, out: Path) -> None:
    """拼接后读一遍输出，核对参数与时长。"""
    info = probe(out)
    size = out.stat().st_size / 1e6
    print()
    print("=" * 78)
    print(" 结果校验")
    print("=" * 78)
    if plan.kind == "video":
        print(
            f"  编码        {info.vcodec} {info.profile} {info.res} {info.fps_text} fps"
        )
        print(f"  帧数        {info.frames}")
    else:
        print(f"  编码        {info.acodec} {info.sample_rate}Hz {info.channels}ch")
    print(f"  时长        {hms(info.duration)}")
    print(f"  体积        {size:.1f} MB")
    print(f"  音轨        {info.audio_text}")

    drift = info.duration - plan.duration
    tolerance = max(0.25, plan.duration * 0.005)
    flag = "OK  " if abs(drift) <= tolerance else "WARN"
    print(f"  时长偏差    [{flag}] {drift:+.2f}s（源合计 {hms(plan.duration)}）")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mmerge",
        description=(
            "探测帧率、分辨率或采样率，选最省的 ffmpeg 拼接参数。"
            "默认只打印，加 --run 才执行。"
        ),
    )
    parser.add_argument("inputs", nargs="+", help="按播放顺序给出的输入文件")
    parser.add_argument("-o", "--out", default=None, help="输出文件，默认按类型推导")
    parser.add_argument(
        "-c", "--canvas", default=None, help="强制画布 WxH，如 1080x1920（仅视频）"
    )
    parser.add_argument("-q", "--crf", type=int, default=18, help="视频 CRF，默认 18")
    parser.add_argument("-p", "--preset", default="slow", help="视频 preset，默认 slow")
    parser.add_argument(
        "-b",
        "--audio-bitrate",
        type=int,
        default=None,
        help="重编码音频码率（kbps），默认跟住源码率",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="只合并音轨；视频文件会先抽掉画面",
    )
    parser.add_argument("--run", action="store_true", help="真正执行")
    args = parser.parse_args(argv)

    for tool in (FFMPEG, FFPROBE):
        if shutil.which(tool) is None:
            print(f"错误：找不到 {tool}，请先安装 ffmpeg", file=sys.stderr)
            return 127

    paths = [Path(name) for name in args.inputs]
    for path in paths:
        if not path.is_file():
            print(f"错误：文件不存在 -> {path}", file=sys.stderr)
            return 1

    infos = [probe(path) for path in paths]
    kinds = _uniq([info.kind for info in infos])
    if len(kinds) > 1 and not args.audio_only:
        mixed = "、".join(f"{i.path.name}({i.kind})" for i in infos)
        print(
            "错误：视频与纯音频文件不能拼进同一条轨道。\n"
            f"  探测结果：{mixed}\n"
            "  两条路：加 --audio-only 只合并音轨；或先给纯音频配上画面。",
            file=sys.stderr,
        )
        return 2

    kind = "audio" if args.audio_only else str(kinds[0])
    if kind == "video":
        out = Path(args.out) if args.out else Path("merged.mp4")
        plan = build_video_plan(infos, out, args.canvas, args.crf, args.preset)
    else:
        out = Path(args.out) if args.out else Path("merged.mka")
        plan = build_audio_plan(infos, out, args.audio_bitrate)

    report(infos, plan)

    if not args.run:
        print("  以上为预览。加 --run 实际执行：")
        print(f"    mmerge.py -o {plan.out.name} --run {' '.join(args.inputs)}")
        return 0

    print()
    code = execute(plan)
    if code != 0:
        return code
    verify(plan, plan.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
