#!/usr/bin/env python3
"""
PDF Scan Compressor
===================
Compresses scanned-document PDFs by applying the same techniques
used by services like smallpdf.com:

1. DOWNSAMPLE oversized images to a target DPI (the #1 savings)
2. RE-ENCODE JPEGs at an optimal quality level  
3. WRAP JPEG streams with FlateDecode for additional ~3-5% savings
4. STRIP ICC profiles → use DeviceRGB/DeviceGray
5. RE-COMPRESS Flate streams with better zlib settings

Usage:
    python compress_pdf.py input.pdf output.pdf [--dpi 150] [--quality 75]
"""

import pikepdf
from pikepdf import Name, Array, Dictionary
from PIL import Image
import io
import zlib
import argparse
import os
import sys


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
        # Chained: FlateDecode + DCTDecode — raw bytes are Flate-compressed JPEG
        raw = stream_obj.read_raw_bytes()
        return zlib.decompress(raw)
    elif 'DCTDecode' in filt_str:
        # Pure JPEG
        return stream_obj.read_raw_bytes()
    else:
        return None


def compress_image(stream_obj, target_dpi=150, jpeg_quality=75, page_mediabox=None):
    """
    Compress a single PDF image XObject.
    Returns (new_data, new_filter, new_width, new_height, changed) or None.
    """
    obj_dict = dict(stream_obj)
    width = int(obj_dict.get(Name.Width, 0))
    height = int(obj_dict.get(Name.Height, 0))
    filt = obj_dict.get(Name.Filter, None)
    filt_str = str(filt) if filt else "None"
    cs = str(obj_dict.get(Name.ColorSpace, ""))
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
    
    if is_jpeg:
        jpeg_data = extract_jpeg_data(stream_obj)
        if jpeg_data is None:
            return None
        
        needs_resample = current_dpi and current_dpi > target_dpi * 1.1  # 10% tolerance
        
        if needs_resample:
            # Downsample: decode → resize → re-encode
            img = Image.open(io.BytesIO(jpeg_data))
            scale = target_dpi / current_dpi
            new_w = max(int(width * scale), 1)
            new_h = max(int(height * scale), 1)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(f"      Resampled: {width}x{height} → {new_w}x{new_h} "
                  f"(DPI: {current_dpi:.0f} → ~{target_dpi})")
            
            # Re-encode at target quality
            buf = io.BytesIO()
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.save(buf, format='JPEG', quality=jpeg_quality, optimize=True,
                     subsampling='4:2:0')
            jpeg_bytes = buf.getvalue()
            color_mode = img.mode
            
        else:
            # No resampling needed — keep the original JPEG data as-is
            jpeg_bytes = jpeg_data
            new_w, new_h = width, height
            # Detect color mode from JPEG
            img = Image.open(io.BytesIO(jpeg_data))
            color_mode = img.mode
            img.close()
        
        # Apply FlateDecode on top of JPEG for additional compression
        flate_bytes = zlib.compress(jpeg_bytes, 9)
        
        # Use Flate wrapping if it saves space
        if len(flate_bytes) < len(jpeg_bytes) - 100:  # At least 100 bytes savings
            final_data = flate_bytes
            final_filter = Array([Name.FlateDecode, Name.DCTDecode])
            print(f"      Flate wrapping: {len(jpeg_bytes):,} → {len(flate_bytes):,} bytes "
                  f"(-{len(jpeg_bytes) - len(flate_bytes):,})")
        else:
            final_data = jpeg_bytes
            final_filter = Name.DCTDecode
        
        new_size = len(final_data)
        print(f"      Size: {original_size:,} → {new_size:,} bytes "
              f"({(1 - new_size/original_size)*100:.1f}% reduction)")
        
        return {
            'data': final_data,
            'filter': final_filter,
            'width': new_w,
            'height': new_h,
            'colorspace': Name.DeviceRGB if color_mode == 'RGB' else Name.DeviceGray,
            'bpc': 8,
            'changed': True
        }
    
    elif is_flate:
        # For Flate-encoded images (PNG-style), try re-compressing with better settings
        try:
            decoded = bytes(stream_obj.read_bytes())
            recompressed = zlib.compress(decoded, 9)  # Maximum compression
            
            if len(recompressed) < original_size * 0.95:  # At least 5% improvement
                print(f"      Flate re-compressed: {original_size:,} → {len(recompressed):,} bytes")
                return {
                    'data': recompressed,
                    'filter': Name.FlateDecode,
                    'width': width,
                    'height': height,
                    'colorspace': None,  # Keep original
                    'bpc': bpc,
                    'changed': True
                }
        except Exception as e:
            print(f"      Flate re-compression failed: {e}")
    
    return None


def compress_pdf(input_path, output_path, target_dpi=150, jpeg_quality=75):
    """
    Compress a PDF containing scanned document images.
    
    Parameters:
        input_path: Path to input PDF
        output_path: Path to output PDF  
        target_dpi: Target DPI for image downsampling (default: 150)
        jpeg_quality: JPEG quality 1-100 (default: 75)
    """
    pdf = pikepdf.open(input_path)
    
    input_size = os.path.getsize(input_path)
    print(f"Input: {input_path} ({input_size:,} bytes / {input_size/1024/1024:.1f} MB)")
    print(f"Settings: target_dpi={target_dpi}, jpeg_quality={jpeg_quality}")
    print(f"Pages: {len(pdf.pages)}")
    print()
    
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
                
                # Convert name to string for display, keep original for assignment
                name_str = str(name)
                print(f"  Page {page_idx+1} / {name_str}: {w}x{h}")
                
                original_size = len(obj.read_raw_bytes())
                
                result = compress_image(obj, target_dpi, jpeg_quality, mediabox)
                
                if result and result['changed']:
                    new_size = len(result['data'])
                    
                    # Only apply if it actually reduces size
                    if new_size < original_size:
                        # Create a new stream object 
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
                        
                        # Replace the reference in the page's XObject dictionary
                        page_xobjects = page[Name.Resources][Name.XObject]
                        page_xobjects[name] = new_stream
                        
                        total_saved += (original_size - new_size)
                        images_processed += 1
                    else:
                        print(f"      Skipped (re-encoded is larger: {original_size:,} → {new_size:,})")
                else:
                    print(f"      Skipped (no improvement possible)")
                    
            except Exception as e:
                print(f"      Error processing {name}: {e}")
    
    # Clean up unreferenced resources and save with optimization
    print(f"\nOptimizing and saving...")
    pdf.remove_unreferenced_resources()
    pdf.save(output_path, 
             object_stream_mode=pikepdf.ObjectStreamMode.generate,
             compress_streams=True,
             recompress_flate=True,
             fix_metadata_version=True)
    
    output_size = os.path.getsize(output_path)
    
    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Input:  {input_size:,} bytes ({input_size/1024/1024:.1f} MB)")
    print(f"  Output: {output_size:,} bytes ({output_size/1024:.0f} KB)")
    print(f"  Reduction: {(1 - output_size/input_size)*100:.1f}%")
    print(f"  Images processed: {images_processed}")
    print(f"{'='*60}")
    
    return output_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Compress scanned-document PDFs')
    parser.add_argument('input', help='Input PDF path')
    parser.add_argument('output', help='Output PDF path')
    parser.add_argument('--dpi', type=int, default=150, 
                       help='Target DPI for downsampling (default: 150)')
    parser.add_argument('--quality', type=int, default=75,
                       help='JPEG quality 1-100 (default: 75)')
    
    args = parser.parse_args()
    compress_pdf(args.input, args.output, args.dpi, args.quality)
