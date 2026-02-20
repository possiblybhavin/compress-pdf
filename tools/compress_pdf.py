#!/usr/bin/env python3
"""
PDF Scan Compressor
===================
Compresses scanned-document PDFs by applying the same techniques
used by services like smallpdf.com:

1. DOWNSAMPLE oversized images to a target DPI (the #1 savings)
2. RE-ENCODE JPEGs at an optimal quality level  
3. MOZJPEG lossless optimization of JPEG streams (Huffman + progressive)
4. WRAP JPEG streams with FlateDecode for additional compression
5. STRIP ICC profiles -> use DeviceRGB/DeviceGray
6. RE-COMPRESS Flate streams with better zlib settings
7. CLEANUP unreferenced objects and recompress all streams

Usage:
    python compress_pdf.py input.pdf output.pdf [--dpi 150] [--quality 75] [--no-mozjpeg]
"""

import pikepdf
from pikepdf import Name, Array, Dictionary
from PIL import Image
import io
import zlib
import argparse
import os
import sys
import time

# Try to import mozjpeg; gracefully degrade if not available
try:
    import mozjpeg_lossless_optimization
    HAS_MOZJPEG = True
except ImportError:
    HAS_MOZJPEG = False


class CompressionStats:
    """Track per-stage compression statistics."""

    def __init__(self):
        self.stages = {}
        self.per_image = []

    def record(self, stage, before_bytes, after_bytes, elapsed_ms):
        if stage not in self.stages:
            self.stages[stage] = []
        self.stages[stage].append((before_bytes, after_bytes, elapsed_ms))

    def add_image_summary(self, page, name, width, height, original, final, stages_applied):
        self.per_image.append({
            'page': page, 'name': name, 'width': width, 'height': height,
            'original': original, 'final': final, 'stages': stages_applied,
        })

    def print_report(self):
        print(f"\n{'='*70}")
        print(f"  COMPRESSION PROFILE BY STAGE")
        print(f"{'='*70}")
        for stage, records in self.stages.items():
            total_before = sum(r[0] for r in records)
            total_after = sum(r[1] for r in records)
            total_time = sum(r[2] for r in records)
            count = len(records)
            saved = total_before - total_after
            pct = (saved / total_before * 100) if total_before else 0
            print(f"\n  {stage}:")
            print(f"    Applied to: {count} image(s)")
            print(f"    Total before: {total_before:,} bytes")
            print(f"    Total after:  {total_after:,} bytes")
            print(f"    Saved:        {saved:,} bytes ({pct:.1f}%)")
            print(f"    Time:         {total_time:.1f} ms")

        if self.per_image:
            print(f"\n{'='*70}")
            print(f"  PER-IMAGE SUMMARY")
            print(f"{'='*70}")
            print(f"  {'Page':<6} {'Name':<10} {'Dims':<14} {'Original':>10} {'Final':>10} {'Saved':>8} {'Stages'}")
            print(f"  {'-'*6} {'-'*10} {'-'*14} {'-'*10} {'-'*10} {'-'*8} {'-'*30}")
            for img in self.per_image:
                saved = img['original'] - img['final']
                pct = (saved / img['original'] * 100) if img['original'] else 0
                dims = f"{img['width']}x{img['height']}"
                stages = ', '.join(img['stages']) if img['stages'] else 'none'
                print(f"  {img['page']:<6} {img['name']:<10} {dims:<14} "
                      f"{img['original']:>10,} {img['final']:>10,} {pct:>7.1f}% {stages}")


def estimate_image_dpi(width_px, height_px, page_width_pts, page_height_pts):
    """Estimate DPI from image pixel dimensions and page size in points (72pt = 1 inch)."""
    if page_width_pts and page_height_pts:
        dpi_x = width_px / (page_width_pts / 72.0)
        dpi_y = height_px / (page_height_pts / 72.0)
        return max(dpi_x, dpi_y)
    return None


def extract_jpeg_data(stream_obj):
    """Extract the raw JPEG data from a PDF image stream, handling filter chains."""
    filt = stream_obj.get(Name.Filter, None)
    filt_str = str(filt)

    if 'FlateDecode' in filt_str and 'DCTDecode' in filt_str:
        raw = stream_obj.read_raw_bytes()
        return zlib.decompress(raw)
    elif 'DCTDecode' in filt_str:
        return stream_obj.read_raw_bytes()
    else:
        return None


def compress_image(stream_obj, target_dpi=150, jpeg_quality=75,
                   page_mediabox=None, use_mozjpeg=True, stats=None):
    """
    Compress a single PDF image XObject.
    Returns result dict with compressed data, or None if no improvement.
    """
    obj_dict = dict(stream_obj)
    width = int(obj_dict.get(Name.Width, 0))
    height = int(obj_dict.get(Name.Height, 0))
    filt = obj_dict.get(Name.Filter, None)
    filt_str = str(filt) if filt else "None"
    bpc = int(str(obj_dict.get(Name.BitsPerComponent, 8)))

    is_jpeg = 'DCTDecode' in filt_str
    is_flate = 'FlateDecode' in filt_str and 'DCTDecode' not in filt_str

    # Determine current effective DPI
    current_dpi = None
    if page_mediabox:
        page_w_pts = float(page_mediabox[2]) - float(page_mediabox[0])
        page_h_pts = float(page_mediabox[3]) - float(page_mediabox[1])
        current_dpi = estimate_image_dpi(width, height, page_w_pts, page_h_pts)

    original_size = len(stream_obj.read_raw_bytes())
    stages_applied = []

    if is_jpeg:
        jpeg_data = extract_jpeg_data(stream_obj)
        if jpeg_data is None:
            return None

        needs_resample = current_dpi and current_dpi > target_dpi * 1.1

        if needs_resample:
            # --- Stage: Downsample ---
            t0 = time.perf_counter()
            img = Image.open(io.BytesIO(jpeg_data))
            scale = target_dpi / current_dpi
            new_w = max(int(width * scale), 1)
            new_h = max(int(height * scale), 1)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(f"      Resampled: {width}x{height} -> {new_w}x{new_h} "
                  f"(DPI: {current_dpi:.0f} -> ~{target_dpi})")

            buf = io.BytesIO()
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.save(buf, format='JPEG', quality=jpeg_quality, optimize=True,
                     subsampling='4:2:0')
            jpeg_bytes = buf.getvalue()
            color_mode = img.mode
            elapsed = (time.perf_counter() - t0) * 1000

            if stats:
                stats.record('1_downsample', len(jpeg_data), len(jpeg_bytes), elapsed)
            print(f"      Downsample+encode: {len(jpeg_data):,} -> {len(jpeg_bytes):,} bytes "
                  f"[{elapsed:.0f}ms]")
            stages_applied.append('downsample')

        else:
            # No resampling -- keep original JPEG data
            jpeg_bytes = jpeg_data
            new_w, new_h = width, height
            img = Image.open(io.BytesIO(jpeg_data))
            color_mode = img.mode
            img.close()

        # --- Stage: MozJPEG lossless optimization ---
        if use_mozjpeg and HAS_MOZJPEG:
            t0 = time.perf_counter()
            before_moz = len(jpeg_bytes)
            try:
                jpeg_bytes = mozjpeg_lossless_optimization.optimize(jpeg_bytes)
                elapsed = (time.perf_counter() - t0) * 1000
                after_moz = len(jpeg_bytes)
                saved_moz = before_moz - after_moz
                pct_moz = (saved_moz / before_moz * 100) if before_moz else 0

                if stats:
                    stats.record('2_mozjpeg', before_moz, after_moz, elapsed)
                if saved_moz > 0:
                    print(f"      MozJPEG:   {before_moz:,} -> {after_moz:,} bytes "
                          f"(-{saved_moz:,}, {pct_moz:.1f}%) [{elapsed:.0f}ms]")
                    stages_applied.append('mozjpeg')
                else:
                    print(f"      MozJPEG:   no improvement [{elapsed:.0f}ms]")
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"      MozJPEG:   failed ({e}) [{elapsed:.0f}ms]")

        # --- Stage: FlateDecode wrapping ---
        t0 = time.perf_counter()
        flate_bytes = zlib.compress(jpeg_bytes, 9)
        elapsed = (time.perf_counter() - t0) * 1000

        if len(flate_bytes) < len(jpeg_bytes) - 100:
            if stats:
                stats.record('3_flate_wrap', len(jpeg_bytes), len(flate_bytes), elapsed)
            print(f"      Flate:     {len(jpeg_bytes):,} -> {len(flate_bytes):,} bytes "
                  f"(-{len(jpeg_bytes) - len(flate_bytes):,}) [{elapsed:.0f}ms]")
            final_data = flate_bytes
            final_filter = Array([Name.FlateDecode, Name.DCTDecode])
            stages_applied.append('flate_wrap')
        else:
            final_data = jpeg_bytes
            final_filter = Name.DCTDecode

        new_size = len(final_data)
        print(f"      Total:     {original_size:,} -> {new_size:,} bytes "
              f"({(1 - new_size / original_size) * 100:.1f}% reduction)")

        return {
            'data': final_data,
            'filter': final_filter,
            'width': new_w,
            'height': new_h,
            'colorspace': Name.DeviceRGB if color_mode == 'RGB' else Name.DeviceGray,
            'bpc': 8,
            'changed': True,
            'stages': stages_applied,
        }

    elif is_flate:
        # --- Stage: Flate re-compression ---
        try:
            t0 = time.perf_counter()
            decoded = bytes(stream_obj.read_bytes())
            recompressed = zlib.compress(decoded, 9)
            elapsed = (time.perf_counter() - t0) * 1000

            if len(recompressed) < original_size * 0.95:
                if stats:
                    stats.record('4_flate_recompress', original_size, len(recompressed), elapsed)
                print(f"      Flate re-compressed: {original_size:,} -> {len(recompressed):,} bytes "
                      f"[{elapsed:.0f}ms]")
                return {
                    'data': recompressed,
                    'filter': Name.FlateDecode,
                    'width': width,
                    'height': height,
                    'colorspace': None,
                    'bpc': bpc,
                    'changed': True,
                    'stages': ['flate_recompress'],
                }
        except Exception as e:
            print(f"      Flate re-compression failed: {e}")

    return None


def compress_pdf(input_path, output_path, target_dpi=150, jpeg_quality=75,
                 use_mozjpeg=True):
    """
    Compress a PDF containing scanned document images.

    Parameters:
        input_path: Path to input PDF
        output_path: Path to output PDF
        target_dpi: Target DPI for image downsampling (default: 150)
        jpeg_quality: JPEG quality 1-100 (default: 75)
        use_mozjpeg: Use mozjpeg lossless optimization (default: True)
    """
    total_start = time.perf_counter()
    pdf = pikepdf.open(input_path)

    input_size = os.path.getsize(input_path)
    print(f"Input: {input_path} ({input_size:,} bytes / {input_size / 1024 / 1024:.1f} MB)")
    print(f"Settings: target_dpi={target_dpi}, jpeg_quality={jpeg_quality}, "
          f"mozjpeg={'enabled' if use_mozjpeg and HAS_MOZJPEG else 'disabled'}")
    if use_mozjpeg and not HAS_MOZJPEG:
        print(f"  Warning: mozjpeg requested but not installed. "
              f"Install with: pip install mozjpeg-lossless-optimization")
    print(f"Pages: {len(pdf.pages)}")
    print()

    stats = CompressionStats()
    total_saved = 0
    images_processed = 0

    for page_idx, page in enumerate(pdf.pages):
        mediabox = page.get(Name.MediaBox, None)
        resources = page.get(Name.Resources, {})
        xobjects = resources.get(Name.XObject, {})

        for name, ref in list(dict(xobjects).items()):
            try:
                obj = ref
                if not isinstance(obj, pikepdf.Stream):
                    continue
                obj_dict = dict(obj)
                if obj_dict.get(Name.Subtype) != Name.Image:
                    continue

                w = int(obj_dict.get(Name.Width, 0))
                h = int(obj_dict.get(Name.Height, 0))

                name_str = str(name)
                print(f"  Page {page_idx + 1} / {name_str}: {w}x{h}")

                original_size = len(obj.read_raw_bytes())

                result = compress_image(obj, target_dpi, jpeg_quality,
                                        mediabox, use_mozjpeg, stats)

                if result and result['changed']:
                    new_size = len(result['data'])

                    if new_size < original_size:
                        new_stream = pdf.make_stream(result['data'])
                        new_stream[Name.Type] = Name.XObject
                        new_stream[Name.Subtype] = Name.Image
                        new_stream[Name.Width] = result['width']
                        new_stream[Name.Height] = result['height']
                        new_stream[Name.BitsPerComponent] = result['bpc']
                        new_stream[Name.Filter] = result['filter']
                        if result['colorspace']:
                            new_stream[Name.ColorSpace] = result['colorspace']
                        else:
                            new_stream[Name.ColorSpace] = obj_dict.get(Name.ColorSpace)

                        page_xobjects = page[Name.Resources][Name.XObject]
                        page_xobjects[name] = new_stream

                        total_saved += (original_size - new_size)
                        images_processed += 1
                        stats.add_image_summary(
                            page_idx + 1, name_str, w, h,
                            original_size, new_size, result.get('stages', []))
                    else:
                        print(f"      Skipped (re-encoded is larger: "
                              f"{original_size:,} -> {new_size:,})")
                        stats.add_image_summary(
                            page_idx + 1, name_str, w, h,
                            original_size, original_size, [])
                else:
                    print(f"      Skipped (no improvement possible)")
                    stats.add_image_summary(
                        page_idx + 1, name_str, w, h,
                        original_size, original_size, [])

            except Exception as e:
                print(f"      Error processing {name}: {e}")

    # --- Stage: PDF-level optimization ---
    print(f"\nOptimizing PDF structure...")
    t0 = time.perf_counter()
    pdf.remove_unreferenced_resources()
    pdf.save(output_path,
             object_stream_mode=pikepdf.ObjectStreamMode.generate,
             compress_streams=True,
             recompress_flate=True,
             fix_metadata_version=True)
    save_elapsed = (time.perf_counter() - t0) * 1000
    total_elapsed = (time.perf_counter() - total_start) * 1000

    output_size = os.path.getsize(output_path)

    # Print profiling report
    stats.print_report()

    print(f"\n{'='*70}")
    print(f"  FINAL RESULTS")
    print(f"{'='*70}")
    print(f"  Input:            {input_size:,} bytes ({input_size / 1024 / 1024:.1f} MB)")
    print(f"  Output:           {output_size:,} bytes ({output_size / 1024:.0f} KB)")
    print(f"  Reduction:        {(1 - output_size / input_size) * 100:.1f}%")
    print(f"  Images processed: {images_processed}")
    print(f"  Save/optimize:    {save_elapsed:.0f} ms")
    print(f"  Total time:       {total_elapsed:.0f} ms ({total_elapsed / 1000:.1f}s)")
    print(f"{'='*70}")

    return output_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Compress scanned-document PDFs')
    parser.add_argument('input', help='Input PDF path')
    parser.add_argument('output', help='Output PDF path')
    parser.add_argument('--dpi', type=int, default=150,
                        help='Target DPI for downsampling (default: 150)')
    parser.add_argument('--quality', type=int, default=75,
                        help='JPEG quality 1-100 (default: 75)')
    parser.add_argument('--no-mozjpeg', action='store_true',
                        help='Disable mozjpeg lossless optimization')

    args = parser.parse_args()
    compress_pdf(args.input, args.output, args.dpi, args.quality,
                 use_mozjpeg=not args.no_mozjpeg)
