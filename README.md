# PDF Scan Compressor

A Python tool that compresses scanned-document PDFs using the same techniques as services like smallpdf.com. Achieves 85-90% file size reduction on typical scanned documents while maintaining readable quality.

## How It Works

The tool analyzes each image embedded in a PDF and applies up to five compression techniques:

### 1. Image Downsampling (biggest impact)

Scanned documents often contain images at full camera resolution (e.g., 5712×4284 from a 24MP phone camera) when only ~150 DPI is needed for readable output. The tool detects oversized images by calculating their effective DPI from the image pixel dimensions and the PDF page size, then downsamples them using Lanczos resampling.

This single technique is responsible for ~90% of the file size savings on typical documents.

### 2. JPEG Re-encoding

Downsampled images are re-encoded as optimized JPEGs at a configurable quality level (default 75). Images that don't need downsampling keep their original JPEG data untouched to avoid generation loss.

### 3. FlateDecode Wrapping

JPEG streams are wrapped in an additional zlib/Flate compression layer (chained PDF filter `[/FlateDecode, /DCTDecode]`). JPEG headers and low-entropy regions compress well under Flate, saving an additional 3-10% per image. This is applied to both re-encoded and preserved original JPEGs.

### 4. ICC Profile Stripping

Embedded ICC color profiles (often 2-3 KB each) are replaced with simple `/DeviceRGB` or `/DeviceGray` color space references.

### 5. PDF Object Optimization

Unreferenced objects are removed and Flate-compressed streams are re-compressed with maximum zlib settings.

## Installation

```bash
pip install pikepdf Pillow
```

## Usage

```bash
python compress_pdf.py input.pdf output.pdf [--dpi 150] [--quality 75]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dpi` | 150 | Target DPI for image downsampling. Images above this DPI (with 10% tolerance) are downsampled. 150 DPI is sufficient for readable document scans. Use 200-300 for documents where fine detail matters. |
| `--quality` | 75 | JPEG quality (1-100) for re-encoded images. Only affects images that are downsampled; images already at or below the target DPI keep their original JPEG data. |

### Examples

```bash
# Default settings — good balance of size and quality
python compress_pdf.py scan.pdf scan_compressed.pdf

# Aggressive compression — matches smallpdf.com output closely
python compress_pdf.py scan.pdf scan_small.pdf --dpi 120 --quality 65

# High quality — for documents where fine print matters
python compress_pdf.py scan.pdf scan_hq.pdf --dpi 200 --quality 85
```

## Results

Tested on a 15-page PDF containing passport scans, certificates, and shipping labels (7.2 MB original, 883 KB from smallpdf.com):

| Settings | Output Size | Reduction |
|----------|-------------|-----------|
| `--dpi 150 --quality 75` | 1,027 KB | 86.0% |
| `--dpi 130 --quality 70` | 953 KB | 87.0% |
| `--dpi 120 --quality 65` | 915 KB | 87.6% |
| smallpdf.com | 883 KB | 88.3% |

The remaining ~30 KB gap versus smallpdf is attributable to their use of a more advanced JPEG encoder (likely MozJPEG or jpegli). See "Further Optimization" below.

## Analysis Scripts

Two additional scripts are included for analyzing and comparing PDF image compression:

### `analyze_pdfs.py`

Structural comparison of two PDFs — lists every image XObject per page with dimensions, filters, and raw stream sizes. Useful for understanding what's inside a PDF and how images are stored.

### `analyze_deep.py`

Digs into JPEG internals: quantization table values, SOF markers (baseline vs progressive), chroma subsampling factors, estimated DPI, and compression ratios. Useful for understanding *why* one JPEG is smaller than another.

Both scripts have hardcoded file paths that you'll need to update for your files.

## Further Optimization

To close the remaining gap with commercial tools, consider integrating one or both of these:

### mozjpeg-lossless-optimization

A Python library that losslessly optimizes existing JPEG streams by improving Huffman tables and converting to progressive scan order. Zero quality loss, 2-10% size reduction. Best for images where you're preserving the original JPEG data (not downsampling).

```bash
pip install mozjpeg-lossless-optimization
```

```python
import mozjpeg_lossless_optimization
optimized = mozjpeg_lossless_optimization.optimize(jpeg_bytes)
```

### jpegli (Google)

A full JPEG encoder that uses perceptually-optimized adaptive quantization, floating-point precision throughout the pipeline, and content-aware dead-zone quantization. Produces significantly smaller JPEGs at equivalent visual quality compared to Pillow/libjpeg. Best for images you're re-encoding from pixels (downsampled images).

Requires building from source: https://github.com/google/jpegli

The two approaches are complementary: use jpegli for encoding from raw pixels, and mozjpeg-lossless-optimization as a final pass on preserved JPEGs.

## How the Techniques Were Discovered

The compression techniques were reverse-engineered by comparing the internal structure of an original PDF against its smallpdf.com-compressed version using `pikepdf`. The key findings were:

- The single largest image (page 5, a 5712×4284 passport photo) accounted for 83% of the original file at 6.4 MB. Smallpdf downsampled it to 1210×908 (~150 DPI), reducing it to 141 KB.
- Smallpdf preserved the original JPEG data on pages where images were already at reasonable resolution, but wrapped the JPEG streams in FlateDecode for a few percent additional savings.
- ICC profiles were replaced with DeviceRGB references.
- Non-JPEG images (shipping label graphics) were re-compressed with better Flate settings.

## Dependencies

- **pikepdf** — PDF manipulation (reading/writing streams, object replacement)
- **Pillow** — Image decoding, resizing, and JPEG encoding
- **Python 3.8+**
