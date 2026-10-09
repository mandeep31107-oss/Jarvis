"""Tests for the dependency-free PNG codec and the frame analysis built on it.

The decoder is exercised against three independent sources of truth:

1. PNGs built by hand in these tests with ``zlib`` and explicit filter bytes, so
   the decoder is not being checked against my own writer's assumptions.
2. Round-trips through :func:`write_png`.
3. ImageMagick, when it is installed - the only genuinely external oracle here.
   Those tests skip rather than pass vacuously when the tool is absent.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from jarvis.vision.analysis import (
    FrameStats,
    MotionReport,
    difference,
    dominant_colour,
    frame_stats,
    has_motion,
    histogram,
    luma,
)
from jarvis.vision.png import (
    PNG_SIGNATURE,
    Image,
    PngError,
    make_image,
    read_png,
    write_png,
)

# --------------------------------------------------------------------- helpers


def build_png(
    width: int,
    height: int,
    channels: int,
    scanlines: list[tuple[int, bytes]],
    *,
    bit_depth: int = 8,
    colour_type: int | None = None,
    palette: list[tuple[int, int, int]] | None = None,
) -> bytes:
    """Assemble a PNG from explicit scanlines, independent of write_png.

    ``scanlines`` is a list of ``(filter_type, filtered_bytes)`` pairs, so a test
    can demand that a specific filter path be exercised.
    """
    ctype = 0 if channels == 1 else (3 if palette else (6 if channels == 4 else 2))
    if colour_type is not None:
        ctype = colour_type
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, ctype, 0, 0, 0)
    chunks = [chunk(b"IHDR", ihdr)]
    if palette:
        flat = b"".join(bytes(c) for c in palette)
        chunks.append(chunk(b"PLTE", flat))
    raw = b"".join(bytes([f]) + data for f, data in scanlines)
    chunks.append(chunk(b"IDAT", zlib.compress(raw, 6)))
    chunks.append(chunk(b"IEND", b""))
    return PNG_SIGNATURE + b"".join(chunks)


def chunk(ctype: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + ctype
        + body
        + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)
    )


def flat(width: int, height: int, channels: int, colour: tuple[int, ...]) -> bytes:
    return bytes(colour[:channels]) * (width * height)


def solid(width: int, height: int, channels: int, colour: tuple[int, ...], *, filter_type: int = 0):
    """One ``(filter, row_bytes)`` entry per row - PNG filters are per scanline."""
    row = bytes(colour[:channels]) * width
    return [(filter_type, row) for _ in range(height)]


# ----------------------------------------------------------------- png reading


def test_a_simple_rgb_image_decodes_pixel_for_pixel(tmp_path: Path) -> None:
    path = tmp_path / "solid.png"
    path.write_bytes(build_png(4, 3, 3, solid(4, 3, 3, (12, 34, 56))))

    image = read_png(path)

    assert (image.width, image.height, image.channels) == (4, 3, 3)
    assert image.pixel(0, 0) == (12, 34, 56)
    assert image.pixel(3, 2) == (12, 34, 56)
    assert len(image.data) == 4 * 3 * 3


def test_rgba_keeps_its_alpha_channel(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    path.write_bytes(build_png(2, 2, 4, solid(2, 2, 4, (255, 0, 0, 77))))

    image = read_png(path)

    assert image.channels == 4
    assert image.pixel(1, 1) == (255, 0, 0, 77)


def test_greyscale_is_expanded_to_three_channels(tmp_path: Path) -> None:
    path = tmp_path / "gray.png"
    path.write_bytes(build_png(2, 2, 1, solid(2, 2, 1, (200,))))

    image = read_png(path)

    assert image.channels == 3
    assert image.pixel(0, 0) == (200, 200, 200)


def test_a_paletted_image_is_expanded_through_its_palette(tmp_path: Path) -> None:
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    # Four pixels: indices 0, 1, 2, 0
    path = tmp_path / "pal.png"
    path.write_bytes(
        build_png(4, 1, 1, [(0, bytes([0, 1, 2, 0]))], colour_type=3, palette=palette)
    )

    image = read_png(path)

    assert image.pixel(0, 0) == (255, 0, 0)
    assert image.pixel(1, 0) == (0, 255, 0)
    assert image.pixel(2, 0) == (0, 0, 255)
    assert image.pixel(3, 0) == (255, 0, 0)


@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_every_scanline_filter_reconstructs_the_same_image(
    tmp_path: Path, filter_type: int
) -> None:
    """Each filter type must produce the identical pixels.

    Filtering is the easiest part of PNG decoding to get subtly wrong: a broken
    filter still yields a plausible-looking image that is quietly shifted or
    washed out. Applying all five to the same source pixels pins that down.
    """
    width, height, channels = 8, 6, 3
    stride = width * channels
    original = bytearray()
    for y in range(height):
        for x in range(width):
            original += bytes([(x * 20) % 256, (y * 40) % 256, 128])

    # Produce the filtered bytes for the requested filter type.
    scanlines: list[tuple[int, bytes]] = []
    for y in range(height):
        row = original[y * stride : (y + 1) * stride]
        above = original[(y - 1) * stride : y * stride] if y else bytes(stride)
        out = bytearray(stride)
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = above[i]
            up_left = above[i - channels] if i >= channels else 0
            if filter_type == 0:
                out[i] = row[i]
            elif filter_type == 1:
                out[i] = (row[i] - left) & 0xFF
            elif filter_type == 2:
                out[i] = (row[i] - up) & 0xFF
            elif filter_type == 3:
                out[i] = (row[i] - ((left + up) >> 1)) & 0xFF
            else:
                out[i] = (row[i] - _paeth_predictor(left, up, up_left)) & 0xFF
        scanlines.append((filter_type, bytes(out)))

    path = tmp_path / f"filter{filter_type}.png"
    path.write_bytes(build_png(width, height, channels, scanlines))

    image = read_png(path)

    assert bytes(image.data) == bytes(original), f"filter {filter_type} decoded wrong"


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def test_sub_eight_bit_depths_are_scaled_to_the_full_range(tmp_path: Path) -> None:
    """A 2-bit maximum must become 255, not 3.

    Without the scaling, every optimised greyscale image comes out near black.
    """
    # 2-bit greyscale, 4 pixels packed into one byte: 0, 1, 2, 3
    path = tmp_path / "2bit.png"
    path.write_bytes(
        build_png(4, 1, 1, [(0, bytes([0b00_01_10_11]))], bit_depth=2, colour_type=0)
    )

    image = read_png(path)

    values = [image.pixel(x, 0)[0] for x in range(4)]
    assert values == [0, 85, 170, 255]


def test_a_one_bit_image_decodes(tmp_path: Path) -> None:
    path = tmp_path / "1bit.png"
    path.write_bytes(
        build_png(8, 1, 1, [(0, bytes([0b10101010]))], bit_depth=1, colour_type=0)
    )

    image = read_png(path)

    assert [image.pixel(x, 0)[0] for x in range(8)] == [255, 0] * 4


# -------------------------------------------------------------- png rejection


def test_a_non_png_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "text.png"
    path.write_bytes(b"this is plainly not a png file")
    with pytest.raises(PngError, match="not a PNG"):
        read_png(path)


def test_a_file_with_a_broken_crc_is_refused_rather_than_mis_decoded(tmp_path: Path) -> None:
    """Silently decoding damaged pixels is worse than refusing."""
    path = tmp_path / "good.png"
    path.write_bytes(build_png(4, 4, 3, [(0, flat(4, 4, 3, (9, 9, 9)))]))
    raw = path.read_bytes()
    broken = raw[:29] + bytes([raw[29] ^ 0xFF]) + raw[30:]

    with pytest.raises(PngError, match="CRC"):
        read_png(broken)


def test_a_truncated_file_reports_the_reason(tmp_path: Path) -> None:
    """A cut-off file names what is missing rather than returning half an image."""
    with pytest.raises(PngError, match="truncated"):
        read_png(PNG_SIGNATURE + struct.pack(">I", 13) + b"IHDR")


def test_a_file_with_no_pixel_data_says_so() -> None:
    body = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    with pytest.raises(PngError, match="no IDAT"):
        read_png(PNG_SIGNATURE + chunk(b"IHDR", body) + chunk(b"IEND", b""))


def test_interlaced_images_are_refused_because_mis_decoding_is_worse(tmp_path: Path) -> None:
    body = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 1)
    raw = PNG_SIGNATURE + chunk(b"IHDR", body)
    with pytest.raises(PngError, match="[Ii]nterlac"):
        read_png(raw)


def test_a_sixteen_bit_image_is_refused(tmp_path: Path) -> None:
    """Refused rather than mis-read as 8-bit, which would halve every sample."""
    row = b"\x00" * (2 * 3 * 2)  # 2 px x 3 ch x 2 bytes
    blob = build_png(2, 2, 3, [(0, row), (0, row)], bit_depth=16)
    with pytest.raises(PngError, match="16-bit"):
        read_png(blob)


# ----------------------------------------------------------------- png writing


def test_a_written_file_reads_back_byte_identical(tmp_path: Path) -> None:
    image = make_image(9, 7, channels=4, fill=(10, 20, 30, 255))
    image.set_pixel(4, 3, (250, 240, 230, 128))
    path = tmp_path / "roundtrip.png"

    write_png(path, image)
    back = read_png(path)

    assert (back.width, back.height, back.channels) == (9, 7, 4)
    assert back.data == image.data


def test_written_bytes_are_a_valid_png_container(tmp_path: Path) -> None:
    path = tmp_path / "container.png"
    write_png(path, make_image(3, 3, channels=3, fill=(1, 2, 3)))
    raw = path.read_bytes()

    assert raw.startswith(PNG_SIGNATURE)
    assert raw[12:16] == b"IHDR"
    assert raw.endswith(b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF))


def test_rewriting_an_existing_file_replaces_it(tmp_path: Path) -> None:
    path = tmp_path / "overwrite.png"
    write_png(path, make_image(2, 2, channels=3, fill=(0, 0, 0)))
    write_png(path, make_image(5, 5, channels=3, fill=(255, 255, 255)))

    image = read_png(path)
    assert (image.width, image.height) == (5, 5)
    assert image.pixel(4, 4) == (255, 255, 255)


def test_an_image_rejects_an_invalid_channel_count() -> None:
    with pytest.raises(PngError, match="channels"):
        Image(2, 2, 5, bytearray(20))


def test_an_image_rejects_a_buffer_that_does_not_match_its_dimensions() -> None:
    """A short buffer would IndexError deep inside analysis instead."""
    with pytest.raises(PngError, match="needs"):
        Image(4, 4, 3, bytearray(10))


# ------------------------------------------------------- external cross-check


@pytest.mark.skipif(shutil.which("convert") is None, reason="ImageMagick is not installed")
def test_the_decoder_agrees_with_imagemagick(tmp_path: Path) -> None:
    """The only external oracle available here.

    ImageMagick writes PNGs using its own encoder with its own filter choices,
    so agreement is real evidence rather than my code agreeing with itself.
    """
    source = tmp_path / "gradient.png"
    subprocess.run(
        [
            "convert", "-size", "24x20", "gradient:red-blue",
            "-colorspace", "sRGB", "-depth", "8", "PNG32:" + str(source),
        ],
        check=True,
        capture_output=True,
    )

    image = read_png(source)
    assert (image.width, image.height) == (24, 20)

    for x, y in [(0, 0), (5, 4), (11, 9), (23, 19)]:
        reported = subprocess.run(
            ["convert", f"{source}[1x1+{x}+{y}]", "-format", "%[pixel:p{0,0}]", "info:"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        numbers = [int(n) for n in "".join(c if c.isdigit() else " " for c in reported).split()]
        assert image.pixel(x, y)[:3] == tuple(numbers[:3]), f"mismatch at ({x},{y})"


@pytest.mark.skipif(shutil.which("convert") is None, reason="ImageMagick is not installed")
def test_imagemagick_can_read_what_the_writer_produces(tmp_path: Path) -> None:
    """The other direction: my encoder is checked by someone else's decoder."""
    image = make_image(12, 8, channels=4, fill=(255, 0, 0, 255))
    image.set_pixel(6, 4, (0, 200, 0, 255))
    path = tmp_path / "written.png"
    write_png(path, image)

    info = subprocess.run(
        ["convert", str(path), "-format", "%m %wx%h", "info:"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert info == "PNG 12x8"

    reported = subprocess.run(
        ["convert", f"{path}[1x1+6+4]", "-format", "%[pixel:p{0,0}]", "info:"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "0,200,0" in reported.replace(" ", "")


# -------------------------------------------------------------------- analysis


def test_luma_uses_perceptual_weights() -> None:
    """Pure blue must read darker than pure green.

    A channel average would call them equal, which makes 'is the screen dark'
    answers wrong.
    """
    assert luma(0, 0, 255) < luma(0, 255, 0)
    assert luma(255, 255, 255) == pytest.approx(1.0)
    assert luma(0, 0, 0) == 0.0


def test_frame_stats_describe_a_uniform_frame() -> None:
    image = make_image(10, 10, channels=3, fill=(255, 255, 255))
    stats = frame_stats(image)

    assert isinstance(stats, FrameStats)
    assert stats.brightness == pytest.approx(1.0)
    assert stats.darkest == stats.brightest
    assert stats.dominant_colour == (255, 255, 255)
    assert stats.dominant_share == pytest.approx(1.0)
    assert sum(stats.histogram) == pytest.approx(1.0)
    assert stats.sampled == 100


def test_frame_stats_detect_a_mostly_dark_frame_with_one_bright_region() -> None:
    """The mean alone would hide a bright dialog on a dark desktop."""
    image = make_image(20, 20, channels=3, fill=(0, 0, 0))
    for y in range(8, 12):
        for x in range(8, 12):
            image.set_pixel(x, y, (255, 255, 255))

    stats = frame_stats(image)

    assert stats.brightness < 0.1
    assert stats.brightest == pytest.approx(1.0)


def test_sampling_reduces_the_pixel_count_and_says_so() -> None:
    image = make_image(20, 20, channels=3, fill=(5, 5, 5))

    full = frame_stats(image, step=1)
    sampled = frame_stats(image, step=4)

    assert full.sampled == 400
    assert sampled.sampled == 25
    assert sampled.brightness == pytest.approx(full.brightness)


def test_histogram_sums_to_one_and_places_black_in_the_first_bin() -> None:
    image = make_image(8, 8, channels=3, fill=(0, 0, 0))
    buckets = histogram(image, bins=8)

    assert sum(buckets) == pytest.approx(1.0)
    assert buckets[0] == pytest.approx(1.0)


def test_dominant_colour_reports_the_majority_colour_and_its_share() -> None:
    image = make_image(10, 10, channels=3, fill=(1, 2, 3))
    for x in range(10):
        image.set_pixel(x, 0, (200, 100, 50))

    colour, share = dominant_colour(image)

    assert colour == (1, 2, 3)
    assert share == pytest.approx(0.9)


def test_identical_frames_show_no_motion() -> None:
    image = make_image(16, 16, channels=3, fill=(90, 90, 90))
    report = difference(image, image)

    assert isinstance(report, MotionReport)
    assert report.score == 0.0
    assert report.changed_share == 0.0
    assert has_motion(image, image) is False


def test_a_changed_region_is_detected_and_localised() -> None:
    before = make_image(20, 20, channels=3, fill=(10, 10, 10))
    after = make_image(20, 20, channels=3, fill=(10, 10, 10))
    for y in range(14, 18):
        for x in range(14, 18):
            after.set_pixel(x, y, (250, 250, 250))

    report = difference(before, after)

    assert has_motion(before, after) is True
    assert report.score > 0.0
    # The change is in the bottom-right quadrant, so the centre must land there.
    assert report.centre[0] > 0.5
    assert report.centre[1] > 0.5


def test_comparing_frames_of_different_sizes_is_an_error_not_a_quiet_crop() -> None:
    with pytest.raises(ValueError, match="different sizes"):
        difference(make_image(4, 4, channels=3), make_image(8, 8, channels=3))


def test_a_small_change_below_the_threshold_is_not_motion() -> None:
    before = make_image(10, 10, channels=3, fill=(100, 100, 100))
    after = make_image(10, 10, channels=3, fill=(101, 100, 100))

    assert has_motion(before, after, threshold=0.08) is False
    assert has_motion(before, after, threshold=0.001) is True


def test_analysis_results_serialise_to_json_safe_dicts() -> None:
    import json

    image = make_image(6, 6, channels=3, fill=(40, 40, 40))
    stats = frame_stats(image)
    report = difference(image, image)

    json.dumps(stats.as_dict())
    json.dumps(report.as_dict())
    assert stats.as_dict()["sampled"] == 36


def test_frames_with_different_channel_counts_can_still_be_compared() -> None:
    """An RGB frame against an RGBA one is legitimate, and used to IndexError.

    The old code read the second frame using the first frame's stride, which ran
    off the end of the shorter buffer.
    """
    rgba = make_image(8, 8, channels=4, fill=(10, 10, 10, 255))
    rgb = make_image(8, 8, channels=3, fill=(10, 10, 10))

    report = difference(rgba, rgb)

    assert report.score == 0.0
    assert report.compared == 64


def test_a_greyscale_image_can_be_measured_without_an_index_error() -> None:
    """1-channel images have no data[i+1] to read."""
    grey = Image(6, 6, 1, bytearray([128] * 36))

    stats = frame_stats(grey)
    assert stats.brightness == pytest.approx(luma(128, 128, 128))
    assert histogram(grey)[4] > 0.0
    assert difference(grey, grey).score == 0.0


# ------------------------------------------------------------- agent wiring


def test_the_vision_agent_measures_a_real_image_file(tmp_path: Path) -> None:
    """The agent reports measurements, not a guess about the scene."""
    from jarvis.agents.base import AgentRequest
    from jarvis.agents.perception import VisionAgent
    from jarvis.host import detect_host

    path = tmp_path / "sample.png"
    write_png(path, make_image(10, 10, channels=3, fill=(200, 200, 200)))

    agent = VisionAgent(host=detect_host())
    result = agent.run(AgentRequest("vision", "what is this", {"image_path": str(path)}))

    assert result.ok is True
    assert result.data["width"] == 10 and result.data["height"] == 10
    assert result.data["brightness"] > 0.7
    #: Explicitly records that no interpretation was attempted.
    assert result.data["interpreted"] is False
    assert any("has not interpreted" in c.statement for c in result.claims)


def test_the_vision_agent_still_refuses_sensitive_inferences(tmp_path: Path) -> None:
    """Having a real image to look at must not weaken the section 17 limit."""
    from jarvis.agents.base import AgentRequest
    from jarvis.agents.perception import VisionAgent
    from jarvis.host import detect_host

    path = tmp_path / "person.png"
    write_png(path, make_image(8, 8, channels=3, fill=(120, 90, 80)))

    agent = VisionAgent(host=detect_host())
    result = agent.run(
        AgentRequest("vision", "is this person ill?", {"image_path": str(path)})
    )

    assert result.ok is False
    assert "does not infer" in result.summary


def test_the_vision_agent_refuses_a_file_it_cannot_read(tmp_path: Path) -> None:
    """A damaged or absent file is refused, never described from imagination."""
    from jarvis.agents.base import AgentRequest
    from jarvis.agents.perception import VisionAgent
    from jarvis.host import detect_host

    agent = VisionAgent(host=detect_host())

    missing = agent.run(
        AgentRequest("vision", "x", {"image_path": str(tmp_path / "nope.png")})
    )
    assert missing.ok is False
    assert "Could not read" in missing.summary

    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not a png at all, just text")
    result = agent.run(AgentRequest("vision", "x", {"image_path": str(junk)}))
    assert result.ok is False
    assert "Could not read" in result.summary


def test_the_vision_agent_reports_motion_between_two_frames(tmp_path: Path) -> None:
    from jarvis.agents.base import AgentRequest
    from jarvis.agents.perception import VisionAgent
    from jarvis.host import detect_host

    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    write_png(before, make_image(12, 12, channels=3, fill=(10, 10, 10)))
    changed = make_image(12, 12, channels=3, fill=(10, 10, 10))
    for y in range(8, 12):
        for x in range(8, 12):
            changed.set_pixel(x, y, (255, 255, 255))
    write_png(after, changed)

    agent = VisionAgent(host=detect_host())
    result = agent.run(
        AgentRequest(
            "vision", "x", {"image_path": str(before), "compare_with": str(after)}
        )
    )

    assert result.data["motion"]["score"] > 0.0
    assert result.data["motion"]["changed_share"] > 0.0


def test_the_vision_agent_declares_no_missing_dependencies() -> None:
    """Measurement is pure stdlib, so the agent must not report itself unready."""
    from jarvis.agents.perception import VisionAgent
    from jarvis.host import detect_host

    agent = VisionAgent(host=detect_host())
    available, _reason = agent.available()

    assert agent.requires == ()
    assert available is True
