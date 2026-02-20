# PDF Scan Compressor

A Python tool that compresses scanned-document PDFs using the same techniques as services like smallpdf.com. Achieves 85-90% file size reduction on typical scanned documents while maintaining readable quality.

## How It Works

The tool analyzes each image embedded in a PDF and applies up to seven compression techniques:

### 1. Image Downsampling (biggest impact)

Scanned documents often contain images at full camera resolution (e.g., 5712×4284 from a 24MP phone camera) when only ~150 DPI is needed for readable output. The tool detects oversized images by calculating their effective DPI from the image pixel dimensions and the PDF page size, then downsamples them using Lanczos resampling.

This single technique is responsible for ~90% of the file size savings on typical documents.

### 2. JPEG Re-encoding

Downsampled images are re-encoded as optimized JPEGs at a configurable quality level (default 75). Images that don't need downsampling keep their original JPEG data untouched to avoid generation loss.

### 3. MozJPEG Lossless Optimization

All JPEG streams (both re-encoded and preserved originals) are passed through MozJPEG's lossless optimizer, which improves Huffman coding tables and converts to progressive scan order. This provides 7-21% savings per image with zero quality loss. The optimization is applied after downsampling/re-encoding, so even Pillow-encoded JPEGs benefit.

### 4. FlateDecode Wrapping

JPEG streams are wrapped in an additional zlib/Flate compression layer (chained PDF filter `[/FlateDecode, /DCTDecode]`). JPEG headers and low-entropy regions compress well under Flate, saving an additional 1-5% per image.

### 5. ICC Profile Stripping

Embedded ICC color profiles (often 2-3 KB each) are replaced with simple `/DeviceRGB` or `/DeviceGray` color space references.

### 6. Flate Stream Re-compression

Non-JPEG images (PNG-style Flate-encoded) are re-compressed with maximum zlib settings (level 9), saving 30-60% on images originally compressed with weaker settings.

### 7. PDF Object Optimization

Unreferenced objects are removed and all compressed streams are re-compressed during the final save pass.

## Installation

```bash
pip install pikepdf Pillow mozjpeg-lossless-optimization
```

MozJPEG is optional but recommended. The tool will work without it (with slightly larger output) and print a warning if it's not installed.

## Usage

```bash
python compress_pdf.py input.pdf output.pdf [--dpi 150] [--quality 75] [--no-mozjpeg]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dpi` | 150 | Target DPI for image downsampling. Images above this DPI (with 10% tolerance) are downsampled. 150 DPI is sufficient for readable document scans. Use 200-300 for documents where fine detail matters. |
| `--quality` | 75 | JPEG quality (1-100) for re-encoded images. Only affects images that are downsampled; images already at or below the target DPI keep their original JPEG data. |
| `--no-mozjpeg` | off | Disable MozJPEG lossless optimization. Reduces processing time by ~250ms at the cost of ~38 KB larger output. |

### Examples

```bash
# Default settings — good balance of size and quality
python compress_pdf.py scan.pdf scan_compressed.pdf

# Aggressive compression — matches smallpdf.com output closely
python compress_pdf.py scan.pdf scan_small.pdf --dpi 120 --quality 65

# High quality — for documents where fine print matters
python compress_pdf.py scan.pdf scan_hq.pdf --dpi 200 --quality 85

# Fast mode — skip mozjpeg for quicker processing
python compress_pdf.py scan.pdf scan_fast.pdf --no-mozjpeg
```

## Profiling Output

The tool prints a per-stage compression profile showing exactly where savings come from:

```
======================================================================
  COMPRESSION PROFILE BY STAGE
======================================================================

  1_downsample:
    Applied to: 1 image(s)
    Total before: 6,464,787 bytes
    Total after:  254,985 bytes
    Saved:        6,209,802 bytes (96.1%)
    Time:         449.5 ms

  2_mozjpeg:
    Applied to: 6 image(s)
    Total before: 742,260 bytes
    Total after:  683,014 bytes
    Saved:        59,246 bytes (8.0%)
    Time:         257.1 ms

  3_flate_wrap:
    Applied to: 5 image(s)
    Total before: 436,276 bytes
    Total after:  431,190 bytes
    Saved:        5,086 bytes (1.2%)
    Time:         10.8 ms

  4_flate_recompress:
    Applied to: 6 image(s)
    Total before: 33,011 bytes
    Total after:  22,178 bytes
    Saved:        10,833 bytes (32.8%)
    Time:         52.9 ms
```

And a per-image summary:

```
======================================================================
  PER-IMAGE SUMMARY
======================================================================
  Page   Name       Dims             Original      Final    Saved Stages
  ------ ---------- -------------- ---------- ---------- -------- ----------------------
  1      /Im1       826x1148           87,120     79,195     9.1% mozjpeg, flate_wrap
  3      /Im3       838x1099           70,118     57,800    17.6% mozjpeg, flate_wrap
  5      /Im1       5712x4284       6,464,787    246,738    96.2% downsample, mozjpeg
  9      /Im6       850x1050           62,380     47,052    24.6% mozjpeg, flate_wrap
```

## Benchmark Results

Tested on a 15-page PDF containing passport scans, certificates, and shipping labels (7.2 MB original, 883 KB from smallpdf.com):

| Configuration | Output Size | Reduction | Time |
|---------------|-------------|-----------|------|
| `--dpi 150 --quality 75` (with mozjpeg) | **989 KB** | **86.5%** | 1.3s |
| `--dpi 150 --quality 75 --no-mozjpeg` | 1,027 KB | 86.0% | 1.0s |
| `--dpi 120 --quality 65` (with mozjpeg) | ~880 KB | ~88.3% | 1.2s |
| smallpdf.com | 883 KB | 88.3% | — |

### MozJPEG Impact

| Metric | Without | With | Delta |
|--------|---------|------|-------|
| Output size | 1,027 KB | 989 KB | **-38 KB** |
| Processing time | 960 ms | 1,272 ms | +312 ms |

MozJPEG's biggest wins were on images with suboptimal original Huffman tables: page 9 saw a 20.8% lossless reduction, and page 3 saw 16.1%.

## Analysis Scripts

Two additional scripts are included for analyzing and comparing PDF image compression:

### `analyze_pdfs.py`

Structural comparison of two PDFs — lists every image XObject per page with dimensions, filters, and raw stream sizes. Useful for understanding what's inside a PDF and how images are stored.

### `analyze_deep.py`

Digs into JPEG internals: quantization table values, SOF markers (baseline vs progressive), chroma subsampling factors, estimated DPI, and compression ratios. Useful for understanding *why* one JPEG is smaller than another.

Both scripts have hardcoded file paths that you'll need to update for your files.

## Further Optimization

The remaining ~100 KB gap versus smallpdf at default settings (or ~0 KB at `--dpi 120 --quality 65`) comes primarily from the JPEG encoder used for downsampled images. Pillow uses standard libjpeg, which produces larger files than more advanced encoders at equivalent visual quality.

### jpegli (Google)

A full JPEG encoder from Google (originally part of the JPEG XL project) that uses perceptually-optimized adaptive quantization, floating-point precision throughout the pipeline, and content-aware dead-zone quantization. Produces significantly smaller JPEGs at equivalent visual quality compared to Pillow/libjpeg. Best for images you're re-encoding from pixels (downsampled images).

Requires building from source: https://github.com/google/jpegli

jpegli and mozjpeg-lossless-optimization are complementary: use jpegli for encoding from raw pixels, and mozjpeg as a final lossless optimization pass.

## How the Techniques Were Discovered

The compression techniques were reverse-engineered by comparing the internal structure of an original PDF against its smallpdf.com-compressed version using `pikepdf`. The key findings were:

- The single largest image (page 5, a 5712×4284 passport photo) accounted for 83% of the original file at 6.4 MB. Smallpdf downsampled it to 1210×908 (~150 DPI), reducing it to 141 KB.
- Smallpdf preserved the original JPEG data on pages where images were already at reasonable resolution, but wrapped the JPEG streams in FlateDecode for a few percent additional savings.
- ICC profiles were replaced with DeviceRGB references.
- Non-JPEG images (shipping label graphics) were re-compressed with better Flate settings.

## Dependencies

- **pikepdf** — PDF manipulation (reading/writing streams, object replacement)
- **Pillow** — Image decoding, resizing, and JPEG encoding
- **mozjpeg-lossless-optimization** — Lossless JPEG optimization (optional, recommended)
- **Python 3.8+**
