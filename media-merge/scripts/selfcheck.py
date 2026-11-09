#!/usr/bin/env python3
"""media-merge 包自检：一条命令跑完，只用标准库，离线可用。

三类检查：
    1. 包内一致性 —— frontmatter 与目录名、references 指针、入口可运行
    2. 真实数据往返 —— 用 ffmpeg 造样本，把视频三档与音频三档各跑一遍
    3. 外部基线对照 —— level 估算结果不得低于 H.264 官方限值表

用法：
    python scripts/selfcheck.py [-v]
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "SKILL.md"
ENTRY = HERE / "mmerge.py"
VERBOSE = "-v" in sys.argv

# 由 main() 解析成绝对路径后填入，避免 S607（不完整的可执行文件路径）
FFMPEG = ""
FFPROBE = ""

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    """记录一条检查结果。"""
    results.append((ok, label))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {label}"
    if detail and (VERBOSE or not ok):
        line += f" -- {detail}"
    print(line)


def run_entry(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """跑一次入口脚本，返回完整结果。"""
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def probe_json(path: Path) -> dict:
    """读文件的流与容器信息。"""
    proc = subprocess.run(
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
    return json.loads(proc.stdout)


def stream_of(data: dict, kind: str) -> dict:
    """取第一条指定类型的流，跳过封面图。"""
    for stream in data["streams"]:
        if stream.get("codec_type") != kind:
            continue
        if (stream.get("disposition") or {}).get("attached_pic") == 1:
            continue
        return stream
    return {}


def seconds(path: Path) -> float:
    """容器时长。"""
    return float(probe_json(path)["format"]["duration"])


# --------------------------------------------------------------------------- #
# 1. 包内一致性
# --------------------------------------------------------------------------- #
def parse_frontmatter(text: str) -> dict[str, str]:
    """取出 SKILL.md 的 frontmatter 键值（只处理单行键值）。"""
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def check_identity() -> None:
    """frontmatter 与目录名、允许键集合、references 与 scripts 指针。"""
    if not SKILL.is_file():
        check(False, "SKILL.md 存在", str(SKILL))
        return
    text = SKILL.read_text(encoding="utf-8")
    fields = parse_frontmatter(text)

    check(bool(fields), "frontmatter 可解析", f"{len(fields)} 个键")
    check(
        fields.get("name") == ROOT.name,
        "name 等于目录名",
        f"name={fields.get('name')!r} dir={ROOT.name!r}",
    )
    check(
        len(fields.get("description", "")) >= 40,
        "description 足够长",
        f"{len(fields.get('description', ''))} 字符",
    )
    allowed = {"name", "description", "agent_created", "disable-model-invocation"}
    extra = set(fields) - allowed
    check(not extra, "frontmatter 无多余键", f"多余: {sorted(extra)}")

    missing = [
        ref
        for ref in re.findall(r"references/([\w.-]+\.md)", text)
        if not (ROOT / "references" / ref).is_file()
    ]
    check(not missing, "references 指针均存在", f"缺失: {missing}")

    scripts = [
        name
        for name in re.findall(r"scripts/([\w.-]+\.py)", text)
        if not (HERE / name).is_file()
    ]
    check(not scripts, "scripts 指针均存在", f"缺失: {scripts}")


def check_entry() -> None:
    """入口脚本能被解释器加载，且 --help 正常退出。"""
    proc = run_entry("--help")
    check(proc.returncode == 0, "入口脚本 --help 正常", proc.stderr.strip()[:200])


# --------------------------------------------------------------------------- #
# 2. 真实数据往返：视频
# --------------------------------------------------------------------------- #
def make_video(
    target: Path,
    size: str,
    rate: int,
    seconds: int,
    vcodec: str,
    profile: str = "",
    preset: str = "ultrafast",
) -> bool:
    """造一段带音轨的小样本。

    ``preset`` 会限制档位：ultrafast 关掉 CABAC 与 8x8dct，x264 即使被指定
    ``high`` 也只写 Constrained Baseline。要造 High 样本必须用 veryfast 以上。
    """
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate={rate}:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v",
        vcodec,
        "-preset",
        preset,
        "-crf",
        "35",
    ]
    if profile:
        cmd += ["-profile:v", profile]
    cmd += [
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-shortest",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode == 0


def check_video_roundtrip(work: Path) -> None:
    """同参数走 copy，异构走 normalize，核对时长、帧数与画布。"""
    first = work / "one.mp4"
    second = work / "two.mp4"
    odd = work / "odd.mp4"

    if not (
        make_video(first, "640x360", 30, 2, "libx264")
        and make_video(second, "640x360", 30, 2, "libx264")
        and make_video(odd, "320x240", 24, 2, "libx264")
    ):
        check(False, "视频样本生成", "ffmpeg 造样本失败")
        return
    check(True, "视频样本生成（2 段同参数 + 1 段异构）")

    out_copy = work / "v-copy.mp4"
    proc = run_entry("-o", str(out_copy), "--run", str(first), str(second))
    if proc.returncode != 0 or not out_copy.is_file():
        check(False, "视频 copy 档往返", proc.stderr.strip()[-300:])
    else:
        data = probe_json(out_copy)
        video = stream_of(data, "video")
        duration = float(data["format"]["duration"])
        frames = int(video.get("nb_frames", 0))
        check(
            abs(duration - 4.0) < 0.15 and frames == 120,
            "视频 copy 档往返（4s / 120 帧）",
            f"duration={duration:.2f} frames={frames}",
        )

    out_norm = work / "v-norm.mp4"
    proc = run_entry("-o", str(out_norm), "--run", str(first), str(odd))
    if proc.returncode != 0 or not out_norm.is_file():
        check(False, "视频 normalize 档往返", proc.stderr.strip()[-300:])
        return
    data = probe_json(out_norm)
    video = stream_of(data, "video")
    duration = float(data["format"]["duration"])
    matched = "normalize" in proc.stdout and video.get("width") == 640
    check(
        matched and abs(duration - 4.0) < 0.3,
        "视频 normalize 档往返（画布 640x360，4s）",
        f"mode={'normalize' if 'normalize' in proc.stdout else '?'} "
        f"{video.get('width')}x{video.get('height')} duration={duration:.2f}",
    )


# --------------------------------------------------------------------------- #
# 2. 真实数据往返：分组与表格
# --------------------------------------------------------------------------- #
def check_groups(work: Path) -> None:
    """分组出片：两组各自成片，且组内模式可以不同。"""
    home = work / "groups"
    home.mkdir(parents=True, exist_ok=True)
    files = [home / f"g{index}.mp4" for index in range(1, 5)]
    ok = (
        make_video(files[0], "640x360", 30, 2, "libx264")
        and make_video(files[1], "640x360", 30, 2, "libx264")
        and make_video(files[2], "320x240", 25, 2, "libx264")
        and make_video(files[3], "320x240", 25, 2, "libx264")
    )
    if not ok:
        check(False, "分组样本生成", "ffmpeg 造样本失败")
        return
    check(True, "分组样本生成（2 段 640x360 + 2 段 320x240）")

    proc = run_entry("--groups", "1-2,3-4", "--run", *[str(f) for f in files], cwd=home)
    first = home / "merged-01-02.mp4"
    second = home / "merged-03-04.mp4"
    if proc.returncode != 0 or not (first.is_file() and second.is_file()):
        check(False, "分组出片（01-02 / 03-04）", proc.stderr.strip()[-300:])
    else:
        frames = [
            int(stream_of(probe_json(path), "video").get("nb_frames", 0))
            for path in (first, second)
        ]
        check(
            frames == [120, 100],
            "分组出片（640 组 120 帧 / 320 组 100 帧）",
            f"frames={frames}",
        )

    # --uniform 让两组共用一套目标参数，后续才能再把两组接起来
    proc = run_entry(
        "--uniform", "--groups", "1-2,3-4", *[str(f) for f in files], cwd=home
    )
    rows = [line for line in proc.stdout.splitlines() if line.startswith("0")]
    check(
        len(rows) == 2 and all("640x360" in row for row in rows),
        "--uniform 使组间目标一致",
        f"rows={rows}",
    )

    # 分组区间必须拦截重叠，否则同一段会被写进两个成品
    proc = run_entry("--groups", "1-3,3-4", *[str(f) for f in files], cwd=home)
    check(
        proc.returncode == 1 and "不重叠" in proc.stderr,
        "拒绝重叠分组（退出码 1）",
        f"code={proc.returncode} err={proc.stderr.strip()[:120]}",
    )

    # -o 不带扩展名：视频档必须补 .mp4，否则 ffmpeg 猜不出封装直接失败，
    # 而且 suffix 为空会让 +faststart 与 video_track_timescale 一起被跳过。
    proc = run_entry("-o", "noext", "--run", str(files[0]), str(files[1]), cwd=home)
    fixed = home / "noext.mp4"
    check(
        proc.returncode == 0 and fixed.is_file() and "-movflags" in proc.stdout,
        "-o 缺扩展名时补 .mp4 且保留 faststart",
        f"code={proc.returncode} exists={fixed.is_file()} "
        f"err={proc.stderr.strip()[:120]}",
    )


def check_profile_row(work: Path) -> None:
    """同编码混档位：差异行要列全，但不该升级成 DIFF 去逼出全量重编。"""
    home = work / "profile"
    home.mkdir(parents=True, exist_ok=True)
    low = home / "lo.mp4"
    high = home / "hi.mp4"
    ok = make_video(
        low, "320x240", 30, 1, "libx264", profile="baseline"
    ) and make_video(
        high, "320x240", 30, 1, "libx264", profile="high", preset="veryfast"
    )
    if not ok:
        check(False, "档位样本生成", "ffmpeg 造样本失败")
        return
    proc = run_entry(str(low), str(high), cwd=home)
    line = next((ln for ln in proc.stdout.splitlines() if "档位" in ln), "")
    check(
        "[info]" in line and "Baseline" in line and "High" in line,
        "档位分歧列入 info 而非 DIFF",
        f"line={line.strip()[:90]}",
    )


def check_markdown_table(work: Path) -> None:
    """md 表格每行的竖线数必须一致，否则贴进对话里就是坏表。"""
    sample = work / "one.mp4"
    proc = run_entry("-f", "md", str(sample), str(sample), cwd=work)
    rows = [line for line in proc.stdout.splitlines() if line.startswith("|")]
    widths = {line.count("|") for line in rows}
    aligned = bool(rows) and len(widths) == 1 and "---:" in rows[1]
    check(
        aligned,
        "md 表格列数一致且带对齐行",
        f"pipes={widths} head={rows[0][:40] if rows else ''}",
    )


# --------------------------------------------------------------------------- #
# 2. 真实数据往返：音频
# --------------------------------------------------------------------------- #
def make_audio(
    target: Path, codec: str, rate: int, channels: int, seconds: int
) -> bool:
    """造一段纯音频样本。"""
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:a",
        codec,
        "-ar",
        str(rate),
        "-ac",
        str(channels),
        str(target),
    ]
    if codec in {"libmp3lame", "aac"}:
        cmd += ["-b:a", "128k"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode == 0


def check_audio_roundtrip(work: Path) -> None:
    """音频三档各跑一遍：同参 copy、采样率不同 normalize、无损 flac 拼接。"""
    one = work / "au1.mp3"
    two = work / "au2.mp3"
    mono = work / "au3.wav"
    lossless = work / "au4.flac"

    if not (
        make_audio(one, "libmp3lame", 44_100, 2, 2)
        and make_audio(two, "libmp3lame", 44_100, 2, 2)
        and make_audio(mono, "pcm_s16le", 48_000, 1, 2)
        and make_audio(lossless, "flac", 44_100, 2, 2)
    ):
        check(False, "音频样本生成", "ffmpeg 造样本失败")
        return
    check(True, "音频样本生成（mp3×2 / wav 单声道 / flac）")

    out_copy = work / "a-copy.mp3"
    proc = run_entry("-o", str(out_copy), "--run", str(one), str(two))
    if proc.returncode != 0 or not out_copy.is_file():
        check(False, "音频 copy 档往返", proc.stderr.strip()[-300:])
    else:
        data = probe_json(out_copy)
        audio = stream_of(data, "audio")
        check(
            abs(seconds(out_copy) - 4.0) < 0.3 and audio.get("codec_name") == "mp3",
            "音频 copy 档往返（mp3，4s）",
            f"codec={audio.get('codec_name')} duration={seconds(out_copy):.2f}",
        )

    # 采样率与声道都不同，必须逐段重编；顺带验证纯音频不会被当成视频
    out_norm = work / "a-norm.mp3"
    proc = run_entry("-o", str(out_norm), "--run", str(one), str(mono))
    if proc.returncode != 0 or not out_norm.is_file():
        check(False, "音频 normalize 档往返", proc.stderr.strip()[-300:])
    else:
        audio = stream_of(probe_json(out_norm), "audio")
        ok = (
            "方案判定（音频" in proc.stdout
            and "normalize" in proc.stdout
            and abs(seconds(out_norm) - 4.0) < 0.3
            and audio.get("channels") == 2
        )
        check(
            ok,
            "音频 normalize 档往返（单声道升立体声，4s）",
            f"channels={audio.get('channels')} duration={seconds(out_norm):.2f}",
        )

    # raw flac 不能 -c copy 拼接：块长越界会让成品只认第一段
    out_flac = work / "a-join.flac"
    proc = run_entry("-o", str(out_flac), "--run", str(lossless), str(lossless))
    if proc.returncode != 0 or not out_flac.is_file():
        check(False, "无损 flac 拼接", proc.stderr.strip()[-300:])
        return
    decode = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(out_flac), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    audio = stream_of(probe_json(out_flac), "audio")
    check(
        abs(seconds(out_flac) - 4.0) < 0.2 and not decode.stderr.strip(),
        "无损 flac 拼接（4s，解码零错误）",
        f"duration={seconds(out_flac):.2f} stderr={decode.stderr.strip()[:160]} "
        f"codec={audio.get('codec_name')}",
    )


# --------------------------------------------------------------------------- #
# 3. 外部基线对照
# --------------------------------------------------------------------------- #
# H.264 level 上限：每帧最大宏块数、每秒最大宏块数（ITU-T H.264 Table A-1）
LEVELS = [
    ("3.1", 3600, 108_000),
    ("4.0", 8192, 245_760),
    ("4.2", 8704, 522_240),
    ("5.1", 36_864, 983_040),
]

CASES = [
    (1280, 720, 30),
    (1280, 720, 60),
    (1920, 1080, 30),
    (1920, 1080, 60),
    (3840, 2160, 30),
]


def baseline_level(width: int, height: int, fps: int) -> str:
    """按官方限值表取最小的可行 level。"""
    macroblocks = ((width + 15) // 16) * ((height + 15) // 16)
    for level, per_frame, per_second in LEVELS:
        if macroblocks <= per_frame and macroblocks * fps <= per_second:
            return level
    return LEVELS[-1][0]


def check_levels() -> None:
    """估算的 level 必须不低于官方限值表算出的下限（高一档无害，低一档是错）。"""
    sys.path.insert(0, str(HERE))
    import mmerge

    order = {name: index for index, (name, _, _) in enumerate(LEVELS)}
    bad = []
    for width, height, fps in CASES:
        got = mmerge.guess_level(width, height, fps)
        floor = baseline_level(width, height, fps)
        if got not in order:
            bad.append(f"{width}x{height}@{fps}: 未知 level {got}")
        elif order[got] < order[floor]:
            bad.append(f"{width}x{height}@{fps}: got={got} < 下限 {floor}")
    check(not bad, f"level 估算不低于官方下限（{len(CASES)} 例）", "; ".join(bad))


# --------------------------------------------------------------------------- #
def main() -> int:
    global FFMPEG, FFPROBE
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ffmpeg 不可用，跳过运行时检查")
        return 2
    FFMPEG, FFPROBE = ffmpeg, ffprobe

    check_identity()
    check_entry()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        check_video_roundtrip(work)
        check_audio_roundtrip(work)
        check_groups(work)
        check_profile_row(work)
        check_markdown_table(work)
    check_levels()

    failed = [label for ok, label in results if not ok]
    print()
    print(f"合计 {len(results)} 项，失败 {len(failed)} 项")
    for label in failed:
        print(f"  失败: {label}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
