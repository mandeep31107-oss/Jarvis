"""A PNG decoder and encoder on the standard library.

Vision needs pixels, and every library that produces them is a heavy dependency.
So this reads and writes PNG directly: ``zlib`` for the pixel data, ``struct``
for the chunks, and an implementation of the five scanline filters from the PNG
specification.

Supported for reading: 8-bit greyscale, greyscale+alpha, RGB and RGBA,
non-interlaced, plus 8-bit palette images. That covers screenshots, camera
frames and anything a browser saves. Interlaced (Adam7) and 16-bit images are
reported as unsupported rather than silently mis-decoded - a wrong image is worse
than no image.

Supported for writing: 8-bit RGB and RGBA, non-interlaced. Enough to save a
processed frame.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Colour type -> channels (before any palette expansion).
_COLOUR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: What each colour type means, for honest error messages.
_COLOUR_NAMES = {
    0: "greyscale",
    2: "RGB",
    3: "indexed (palette)",
    4: "greyscale + alpha",
    6: "RGBA",
}


class PngError(ValueError):
    """The file is not a PNG this decoder can read, and why."""


@dataclass
class Image:
    """Decoded pixels as a flat byte array, ``channels`` bytes per pixel."""

    width: int
    height: int
    channels: int
    data: bytearray
    #: Palette, when the source was indexed. Kept so provenance is not lost.
    palette: list[tuple[int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.channels not in (1, 2, 3, 4):
            raise PngError(f"channels must be 1, 2, 3 or 4, not {self.channels!r}")
        if self.width <= 0 or self.height <= 0:
            raise PngError(f"invalid dimensions {self.width}x{self.height}")
        expected = self.width * self.height * self.channels
        if len(self.data) != expected:
            raise PngError(
                f"pixel buffer is {len(self.data)} bytes but {self.width}x{self.height}"
                f"x{self.channels} needs {expected}"
            )

    @property
    def mode(self) -> str:
        return {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}.get(self.channels, "?")

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def pixel(self, x: int, y: int) -> tuple[int, ...]:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError(f"({x}, {y}) is outside {self.width}x{self.height}")
        start = (y * self.width + x) * self.channels
        return tuple(self.data[start : start + self.channels])

    def set_pixel(self, x: int, y: int, value: tuple[int, ...]) -> None:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError(f"({x}, {y}) is outside {self.width}x{self.height}")
        start = (y * self.width + x) * self.channels
        self.data[start : start + self.channels] = bytes(value[: self.channels])

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "mode": self.mode,
            "bytes": len(self.data),
        }


# ----------------------------------------------------------------- primitives
def _paeth(a: int, b: int, c: int) -> int:
    """The Paeth predictor from the PNG spec, section 9.4."""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(
    raw: bytes, stride: int, height: int, pixel_bytes: int
) -> list[bytes]:
    """Reverse the per-scanline filters, returning one packed line per row.

    Filters operate on *bytes*, so "left" is the byte ``pixel_bytes`` back - for
    a 1-bit greyscale image that is one byte, not one pixel.

    Each line is built in its own buffer and copied into place: slicing a
    bytearray yields a copy, not a view, so writing through a slice silently
    discards the result. That bug produced an image of the right dimensions
    where every pixel was zero.
    """
    lines: list[bytes] = []
    pos = 0
    previous = bytes(stride)
    for y in range(height):
        if pos >= len(raw):
            raise PngError("truncated pixel data: fewer scanlines than IHDR declares")
        filter_type = raw[pos]
        pos += 1
        line = raw[pos : pos + stride]
        pos += stride
        if len(line) != stride:
            raise PngError(f"scanline {y} is {len(line)} bytes, expected {stride}")

        target = bytearray(stride)
        if filter_type == 0:  # None
            target[:] = line
        elif filter_type == 1:  # Sub
            for i in range(stride):
                left = target[i - pixel_bytes] if i >= pixel_bytes else 0
                target[i] = (line[i] + left) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                target[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = target[i - pixel_bytes] if i >= pixel_bytes else 0
                target[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = target[i - pixel_bytes] if i >= pixel_bytes else 0
                up_left = previous[i - pixel_bytes] if i >= pixel_bytes else 0
                target[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
        else:
            raise PngError(f"unknown scanline filter type {filter_type}")

        lines.append(bytes(target))
        previous = target
    return lines


def _unpack_line(line: bytes, samples: int, bit_depth: int) -> bytearray:
    """Expand one packed scanline into ``samples`` bytes, scaled to 0-255.

    Scaling matters: a 2-bit maximum of 3 must become 255, not 3, or every
    optimised greyscale image comes out almost black.
    """
    maximum = (1 << bit_depth) - 1
    out = bytearray(samples)
    if bit_depth == 8:
        out[:] = line[:samples]
        return out

    index = 0
    for byte in line:
        for shift in range(8 - bit_depth, -1, -bit_depth):
            if index >= samples:
                return out
            value = (byte >> shift) & maximum
            out[index] = (value * 255 + maximum // 2) // maximum
            index += 1
    return out


# --------------------------------------------------------------------- reader
def read_png(source: str | Path | bytes) -> Image:
    """Decode a PNG into an :class:`Image`.

    ``source`` may be a path or the file's bytes. Raises :class:`PngError` with a
    specific reason rather than returning a partially decoded image.
    """
    raw = Path(source).read_bytes() if not isinstance(source, bytes) else source
    if not raw.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG file (bad signature)")

    pos = len(PNG_SIGNATURE)
    width = height = bit_depth = colour_type = interlace = 0
    idat = bytearray()
    palette: list[tuple[int, int, int]] = []
    have_ihdr = False

    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos : pos + 4])
        ctype = raw[pos + 4 : pos + 8]
        body = raw[pos + 8 : pos + 8 + length]
        stored_crc = raw[pos + 8 + length : pos + 12 + length]
        pos += 12 + length
        if len(body) != length:
            raise PngError(f"chunk {ctype!r} is truncated")
        if len(stored_crc) != 4:
            raise PngError(f"chunk {ctype!r} has no CRC field - the file is truncated")
        #: Verify the CRC over type+body, as the spec requires. Without this a
        #: file with damaged pixels decodes silently into the wrong image, which
        #: is worse than refusing to read it.
        if struct.unpack(">I", stored_crc)[0] != zlib.crc32(ctype + body) & 0xFFFFFFFF:
            raise PngError(
                f"the {ctype.decode('latin-1')} chunk failed its CRC check - "
                "the file is damaged"
            )

        if ctype == b"IHDR":
            if len(body) < 13:
                raise PngError("IHDR is too short")
            width, height, bit_depth, colour_type, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body[:13]
            )
            have_ihdr = True
            if width == 0 or height == 0:
                raise PngError(f"invalid dimensions {width}x{height}")
            if colour_type not in _COLOUR_CHANNELS:
                raise PngError(f"unknown colour type {colour_type}")
            if bit_depth not in (1, 2, 4, 8):
                #: 16-bit samples would need a different unpacking path. Falling
                #: through instead produces an all-black image, which is worse
                #: than an error.
                raise PngError(
                    f"{bit_depth}-bit images are not supported (8-bit only)"
                )
            if interlace:
                raise PngError(
                    "interlaced (Adam7) PNGs are not supported; a mis-decoded image "
                    "is worse than no image"
                )
        elif ctype == b"PLTE":
            palette = [
                (body[i], body[i + 1], body[i + 2]) for i in range(0, len(body) - 2, 3)
            ]
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break

    if not have_ihdr:
        raise PngError("no IHDR chunk")
    if not idat:
        raise PngError("no IDAT chunk: the file has no pixel data")

    try:
        decompressed = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"pixel data is not valid zlib: {exc}") from exc

    src_channels = _COLOUR_CHANNELS[colour_type]
    #: Bytes per scanline and per pixel, packed. Sub-8-bit images pack several
    #: samples per byte and pad the final byte, hence the ceiling divisions.
    stride = -(-width * src_channels * bit_depth // 8)
    pixel_bytes = max(1, -(-src_channels * bit_depth // 8))
    samples_per_pixel = src_channels

    lines = _unfilter(decompressed, stride, height, pixel_bytes)
    pixels = bytearray()
    for line in lines:
        pixels += _unpack_line(line, width * samples_per_pixel, bit_depth)

    # Expand palettes and greyscale into a consistent channel count so callers
    # never have to branch on the source colour type.
    if colour_type == 3:
        if not palette:
            raise PngError("indexed PNG with no PLTE chunk")
        out = bytearray(width * height * 3)
        for i in range(width * height):
            index = pixels[i]
            if index >= len(palette):
                raise PngError(f"palette index {index} out of range ({len(palette)} entries)")
            out[i * 3 : i * 3 + 3] = bytes(palette[index])
        return Image(width, height, 3, out, palette=palette)

    if colour_type == 0:
        out = bytearray(width * height * 3)
        for i in range(width * height):
            v = pixels[i]
            out[i * 3] = out[i * 3 + 1] = out[i * 3 + 2] = v
        return Image(width, height, 3, out)

    return Image(width, height, src_channels, pixels)


# --------------------------------------------------------------------- writer
def _chunk(ctype: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + ctype
        + body
        + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)
    )


def write_png(
    destination: str | Path,
    image: Image,
    *,
    compress_level: int = 6,
) -> Path:
    """Encode an :class:`Image` as a PNG.

    Only 8-bit RGB and RGBA are written. Filter type 0 (None) is used: it
    compresses slightly worse than adaptive filtering, but it is the one filter
    that cannot be implemented incorrectly, and correctness matters more here.
    """
    if image.channels not in (3, 4):
        raise PngError(
            f"cannot write {image.channels}-channel images; convert to RGB or RGBA first"
        )
    if len(image.data) != image.width * image.height * image.channels:
        raise PngError(
            f"pixel data is {len(image.data)} bytes, expected "
            f"{image.width * image.height * image.channels}"
        )

    colour_type = 2 if image.channels == 3 else 6
    stride = image.width * image.channels
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)  # filter: None
        raw += image.data[y * stride : (y + 1) * stride]

    ihdr = struct.pack(
        ">IIBBBBB", image.width, image.height, 8, colour_type, 0, 0, 0
    )
    payload = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), compress_level))
        + _chunk(b"IEND", b"")
    )
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def make_image(
    width: int, height: int, channels: int = 3, fill: tuple[int, ...] = (0, 0, 0)
) -> Image:
    """An image filled with one colour. Useful for tests and for placeholders."""
    if channels not in (3, 4):
        raise PngError("channels must be 3 (RGB) or 4 (RGBA)")
    pixel = bytes(fill[:channels]) + bytes(max(0, channels - len(fill)))
    return Image(width, height, channels, bytearray(pixel * width * height))


__all__ = ["Image", "PngError", "PNG_SIGNATURE", "make_image", "read_png", "write_png"]
