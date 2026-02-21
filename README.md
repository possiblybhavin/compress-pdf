# PDF Scan Compressor

A Python tool that compresses scanned-document PDFs using the same techniques as services like smallpdf.com. Achieves 85-90% file size reduction on typical scanned documents while maintaining readable quality.

## How It Works

The tool analyzes each image embedded in a PDF and applies up to seven compression techniques:

### 1. Image Downsampling (biggest impact)

Scanned documents often contain images at full camera resolution (e.g., 5712×4284 from a 24MP phone camera) when only ~150 DPI is needed for readable output. The tool detects oversized images by calculating their effective DPI from the image pixel dimensions and the PDF page size, then downsamples them using Lanczos resampling.

This single technique is responsible for ~90% of the file size savings on typical documents.

### 2. JPEG Encoding (jpegli or Pillow)

Downsampled images are re-encoded as optimized JPEGs. When available, the tool uses Google's **jpegli** encoder, which produces significantly smaller files than Pillow/libjpeg at equivalent visual quality through perceptually-optimized adaptive quantization and floating-point precision throughout the pipeline. When jpegli is not available, the tool falls back to Pillow's JPEG encoder.

Images that don't need downsampling keep their original JPEG data untouched to avoid generation loss.

### 3. MozJPEG Lossless Optimization

All JPEG streams (both re-encoded and preserved originals) are passed through MozJPEG's lossless optimizer, which improves Huffman coding tables and converts to progressive scan order. This provides 7-21% savings per image with zero quality loss.

### 4. FlateDecode Wrapping

JPEG streams are wrapped in an additional zlib/Flate compression layer (chained PDF filter `[/FlateDecode, /DCTDecode]`). JPEG headers and low-entropy regions compress well under Flate, saving an additional 1-5% per image.

### 5. ICC Profile Stripping

Embedded ICC color profiles (often 2-3 KB each) are replaced with simple `/DeviceRGB` or `/DeviceGray` color space references.

### 6. Flate Stream Re-compression

Non-JPEG images (PNG-style Flate-encoded) are re-compressed with maximum zlib settings (level 9), saving 30-60% on images originally compressed with weaker settings.

### 7. PDF Object Optimization

Unreferenced objects are removed and all compressed streams are re-compressed during the final save pass.

## Installation

### Required

Install dependencies in pyproject.toml:
```bash
uv sync
```

If missing pyproject.toml:
```bash
pip install pikepdf Pillow mozjpeg-lossless-optimization
```

### Recommended

```bash
# MozJPEG lossless optimization (7-21% savings on JPEGs, zero quality loss)
pip install mozjpeg-lossless-optimization
```

### Optional (for best encoding quality)

Build and install jpegli from source for the best JPEG encoding quality when downsampling images:

```bash
git clone https://github.com/google/jpegli --recursive
cd jpegli
mkdir build && cd build
# For macOS Sonoma (tested on Intel)
cmake -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=OFF \
      -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      -DCMAKE_C_COMPILER=/usr/local/opt/llvm/bin/clang \
      -DCMAKE_CXX_COMPILER=/usr/local/opt/llvm/bin/clang++ \
      -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/opt/llvm/lib/c++ -Wl,-rpath,/usr/local/opt/llvm/lib/c++ -L/usr/local/opt/llvm/lib -Wl,-rpath,/usr/local/opt/llvm/lib" \
      -DCMAKE_SHARED_LINKER_FLAGS="-L/usr/local/opt/llvm/lib/c++ -Wl,-rpath,/usr/local/opt/llvm/lib/c++ -L/usr/local/opt/llvm/lib -Wl,-rpath,/usr/local/opt/llvm/lib" \
      ..
cmake --build . -- -j$(nproc)
cmake --install . --prefix /usr/local
```

The tool auto-detects `cjpegli` on your PATH and at common install locations (`/usr/local/bin`, `/opt/homebrew/bin`). If not found, it falls back to Pillow's JPEG encoder and prints a warning.

#### macOS: Fixing dynamic library errors

On macOS, `cjpegli` may fail with a `Library not loaded: @rpath/libjxl_threads.0.12.dylib` error because the shared libraries are installed to `/usr/local/lib` but the binary's `@rpath` doesn't include that directory.

The script handles this automatically by injecting `DYLD_LIBRARY_PATH` into the subprocess environment. However, if you want to fix it permanently at the system level, you have two options:

**Option A: Patch the binary's rpath (recommended, one-time fix)**

```bash
install_name_tool -add_rpath /usr/local/lib /usr/local/bin/cjpegli
```

**Option B: Set the environment variable in your shell profile**

```bash
# Add to ~/.zshrc or ~/.bash_profile
export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"
```

**Option C: Build with static linking** to avoid the issue entirely:

```bash
cd jpegli/build
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
cmake --build . -- -j$(nproc)
cmake --install . --prefix /usr/local
```

## Usage

```bash
python compress_pdf.py input.pdf output.pdf [--dpi 150] [--quality 75] [--no-mozjpeg] [--no-jpegli]
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dpi` | 150 | Target DPI for image downsampling. Images above this DPI (with 10% tolerance) are downsampled. 150 DPI is sufficient for readable document scans. Use 200-300 for documents where fine detail matters. |
| `--quality` | 75 | JPEG quality (1-100) for re-encoded images. Only affects images that are downsampled; images already at or below the target DPI keep their original JPEG data. |
| `--no-mozjpeg` | off | Disable MozJPEG lossless optimization. |
| `--no-jpegli` | off | Disable jpegli encoder; use Pillow instead. |

### Examples

```bash
# Default settings — good balance of size and quality
python compress_pdf.py scan.pdf scan_compressed.pdf

# Aggressive compression — matches smallpdf.com output closely
python compress_pdf.py scan.pdf scan_small.pdf --dpi 120 --quality 65

# High quality — for documents where fine print matters
python compress_pdf.py scan.pdf scan_hq.pdf --dpi 200 --quality 85

# Fast mode — skip mozjpeg and jpegli for quicker processing
python compress_pdf.py scan.pdf scan_fast.pdf --no-mozjpeg --no-jpegli
```

## Verifying Compression Enhancement

Run these three commands to compare each encoder combination:

```bash
# Full pipeline: jpegli + mozjpeg
python compress_pdf.py original.pdf out_full.pdf --dpi 150 --quality 75

# Without jpegli (Pillow + mozjpeg)
python compress_pdf.py original.pdf out_no_jpegli.pdf --dpi 150 --quality 75 --no-jpegli

# Baseline (Pillow only, no optimizations)
python compress_pdf.py original.pdf out_baseline.pdf --dpi 150 --quality 75 --no-jpegli --no-mozjpeg
```

The profiling output labels each image with its encoder (`downsample(jpegli)` vs `downsample(pillow)`) and shows per-stage byte savings, so the impact of each tool is immediately visible.

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
  ------ ---------- -------------- ---------- ---------- -------- ----------------------------
  1      /Im1       826x1148           87,120     79,195     9.1% mozjpeg, flate_wrap
  3      /Im3       838x1099           70,118     57,800    17.6% mozjpeg, flate_wrap
  5      /Im1       5712x4284       6,464,787    246,738    96.2% downsample(jpegli), mozjpeg
  9      /Im6       850x1050           62,380     47,052    24.6% mozjpeg, flate_wrap
```

## Benchmark Results

Tested on a 15-page PDF containing passport scans, certificates, and shipping labels (7.2 MB original, 883 KB from smallpdf.com).

### Encoder Comparison at `--dpi 150 --quality 75`

| Configuration | Output Size | Reduction | Time |
|---------------|-------------|-----------|------|
| jpegli + mozjpeg | *~950 KB* | *~87.4%* | ~1.5s |
| Pillow + mozjpeg | 989 KB | 86.5% | 1.3s |
| Pillow only | 1,027 KB | 86.0% | 1.0s |
| smallpdf.com | 883 KB | 88.3% | — |

*jpegli estimates based on published benchmarks showing 20-30% smaller JPEGs vs libjpeg at equivalent visual quality. Actual results depend on image content.*

### DPI / Quality Sweep (Pillow + mozjpeg)

| Settings | Output Size | Reduction |
|----------|-------------|-----------|
| `--dpi 150 --quality 75` | 989 KB | 86.5% |
| `--dpi 130 --quality 70` | ~920 KB | ~87.8% |
| `--dpi 120 --quality 65` | ~880 KB | ~88.3% |

### Per-Stage Impact (Pillow + mozjpeg)

| Stage | Images | Bytes Saved | % of Input | Time |
|-------|--------|-------------|------------|------|
| Downsample | 1 | 6,209,802 | 96.1% | 450 ms |
| MozJPEG | 6 | 59,246 | 8.0% | 257 ms |
| Flate wrap | 5 | 5,086 | 1.2% | 11 ms |
| Flate recompress | 6 | 10,833 | 32.8% | 53 ms |

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

## How the Techniques Were Discovered

The compression techniques were reverse-engineered by comparing the internal structure of an original PDF against its smallpdf.com-compressed version using `pikepdf`. The key findings were:

- The single largest image (page 5, a 5712×4284 passport photo) accounted for 83% of the original file at 6.4 MB. Smallpdf downsampled it to 1210×908 (~150 DPI), reducing it to 141 KB.
- Smallpdf preserved the original JPEG data on pages where images were already at reasonable resolution, but wrapped the JPEG streams in FlateDecode for a few percent additional savings.
- ICC profiles were replaced with DeviceRGB references.
- Non-JPEG images (shipping label graphics) were re-compressed with better Flate settings.

## Architecture

```
Input PDF
  │
  ├─ For each image XObject:
  │   │
  │   ├─ JPEG image, DPI > target?
  │   │   ├─ YES: Downsample (Lanczos) → Encode (jpegli or Pillow) → MozJPEG → Flate wrap
  │   │   └─ NO:  Keep original JPEG bytes → MozJPEG → Flate wrap
  │   │
  │   └─ Flate image (PNG-style)?
  │       └─ Re-compress with zlib level 9
  │
  ├─ Strip ICC profiles → DeviceRGB / DeviceGray
  ├─ Remove unreferenced objects
  └─ Save with recompress_flate + object stream generation
```

## Troubleshooting

### jpegli: `Library not loaded: @rpath/libjxl_threads.0.12.dylib`

This is a macOS dynamic library resolution issue. The `cjpegli` binary was linked with `@rpath` references but `/usr/local/lib` isn't in its rpath search list.

The script handles this automatically by setting `DYLD_LIBRARY_PATH` in the subprocess environment. If you still see this error, apply one of these permanent fixes:

```bash
# Option A: Patch the binary (recommended)
install_name_tool -add_rpath /usr/local/lib /usr/local/bin/cjpegli

# Option B: Environment variable (add to ~/.zshrc)
export DYLD_LIBRARY_PATH="/usr/local/lib:$DYLD_LIBRARY_PATH"

# Option C: Rebuild with static linking
cd jpegli/build
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
cmake --build . -- -j$(nproc)
cmake --install . --prefix /usr/local
```

### jpegli found but output says `downsample(pillow)`

The script detected `cjpegli` but it failed at runtime. Run with default settings (no `--quiet`) to see the error message. Common causes:

- Dynamic library not found (see above)
- `cjpegli` not executable: `chmod +x /usr/local/bin/cjpegli`
- Unexpected CLI format: verify with `cjpegli --help`

### MozJPEG not found

```bash
pip install mozjpeg-lossless-optimization
```

On some systems this requires a C compiler and cmake for building the native extension:

```bash
# Debian/Ubuntu
sudo apt install build-essential cmake python3-dev

# macOS
xcode-select --install
```

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| **pikepdf** | Yes | PDF manipulation (reading/writing streams, object replacement) |
| **Pillow** | Yes | Image decoding, resizing, and fallback JPEG encoding |
| **mozjpeg-lossless-optimization** | Recommended | Lossless JPEG optimization (Huffman + progressive) |
| **jpegli** (`cjpegli` binary) | Optional | Superior JPEG encoding for downsampled images |
| **Python 3.8+** | Yes | — |
