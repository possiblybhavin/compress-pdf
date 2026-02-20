#!/usr/bin/env python3
"""Deep dive into the specific compression techniques used."""

import pikepdf
from pikepdf import Name
import struct

def get_jpeg_quality_info(raw_bytes):
    """Extract JPEG quantization tables to estimate quality."""
    if raw_bytes[:2] != b'\xff\xd8':
        return None
    
    results = []
    pos = 2
    while pos < len(raw_bytes) - 2:
        if raw_bytes[pos] != 0xFF:
            pos += 1
            continue
        marker = raw_bytes[pos+1]
        if marker == 0xDB:  # DQT
            seg_len = struct.unpack('>H', raw_bytes[pos+2:pos+4])[0]
            offset = pos + 4
            while offset < pos + 2 + seg_len:
                qt_info = raw_bytes[offset]
                precision = (qt_info >> 4) & 0x0F
                table_id = qt_info & 0x0F
                offset += 1
                if precision == 0:  # 8-bit values
                    qt_values = list(raw_bytes[offset:offset+64])
                    offset += 64
                else:  # 16-bit values
                    qt_values = [struct.unpack('>H', raw_bytes[offset+j*2:offset+j*2+2])[0] for j in range(64)]
                    offset += 128
                avg = sum(qt_values) / len(qt_values)
                results.append({
                    'table_id': table_id,
                    'avg_value': avg,
                    'min_value': min(qt_values),
                    'max_value': max(qt_values),
                    'first_8': qt_values[:8]
                })
        elif marker == 0xC0 or marker == 0xC2:  # SOF0 or SOF2
            seg_len = struct.unpack('>H', raw_bytes[pos+2:pos+4])[0]
            precision = raw_bytes[pos+4]
            height = struct.unpack('>H', raw_bytes[pos+5:pos+7])[0]
            width = struct.unpack('>H', raw_bytes[pos+7:pos+9])[0]
            num_components = raw_bytes[pos+9]
            sof_type = "Baseline" if marker == 0xC0 else "Progressive"
            results.append({
                'sof_type': sof_type,
                'precision': precision,
                'width': width,
                'height': height,
                'components': num_components
            })
            # Get sampling factors
            for c in range(num_components):
                comp_id = raw_bytes[pos+10+c*3]
                sampling = raw_bytes[pos+11+c*3]
                h_sampling = (sampling >> 4) & 0x0F
                v_sampling = sampling & 0x0F
                qt_id = raw_bytes[pos+12+c*3]
                results.append({
                    'component_id': comp_id,
                    'h_sampling': h_sampling,
                    'v_sampling': v_sampling,
                    'qt_table': qt_id
                })
        elif marker == 0xDA:  # SOS - Start of Scan
            break
        
        if marker not in (0x00, 0xFF, 0xD8):
            try:
                seg_len = struct.unpack('>H', raw_bytes[pos+2:pos+4])[0]
                pos += 2 + seg_len
            except:
                pos += 2
        else:
            pos += 2
    
    return results


def analyze_page_image_detail(filepath, label):
    pdf = pikepdf.open(filepath)
    print(f"\n{'='*70}")
    print(f"  DETAILED IMAGE ANALYSIS: {label}")
    print(f"{'='*70}")
    
    for page_idx, page in enumerate(pdf.pages):
        resources = page.get(Name.Resources, {})
        xobjects = resources.get(Name.XObject, {})
        
        for name, ref in dict(xobjects).items():
            try:
                obj = ref
                if not isinstance(obj, pikepdf.Stream):
                    continue
                obj_dict = dict(obj)
                subtype = obj_dict.get(Name.Subtype)
                if subtype != Name.Image:
                    continue
                
                w = int(obj_dict.get(Name.Width, 0))
                h = int(obj_dict.get(Name.Height, 0))
                filt = str(obj_dict.get(Name.Filter, "None"))
                cs = str(obj_dict.get(Name.ColorSpace, "None"))
                bpc = obj_dict.get(Name.BitsPerComponent, None)
                raw = obj.read_raw_bytes()
                
                print(f"\n  Page {page_idx+1} - {name}:")
                print(f"    Dimensions: {w}x{h} ({w*h:,} pixels)")
                print(f"    ColorSpace: {cs}")
                print(f"    BPC: {bpc}")
                print(f"    Filter: {filt}")
                print(f"    Raw stream: {len(raw):,} bytes")
                
                # Calculate uncompressed size
                channels = 3 if 'RGB' in cs or 'DeviceRGB' in cs else (1 if 'Gray' in cs else 3)
                uncompressed = w * h * channels * (int(str(bpc)) if bpc else 8) // 8
                print(f"    Uncompressed estimate: {uncompressed:,} bytes ({uncompressed/1024/1024:.1f} MB)")
                print(f"    Compression ratio: {len(raw)/uncompressed:.4f} ({(1-len(raw)/uncompressed)*100:.1f}% reduction)")
                
                # If it's JPEG, analyze the quality
                if 'DCTDecode' in filt:
                    # For chained filters (FlateDecode+DCTDecode), we need the decoded-once version
                    if 'FlateDecode' in filt and 'DCTDecode' in filt:
                        # The raw bytes are Flate-compressed JPEG
                        import zlib
                        try:
                            jpeg_data = zlib.decompress(raw)
                            print(f"    After Flate decompression: {len(jpeg_data):,} bytes (JPEG data)")
                        except:
                            jpeg_data = raw
                    else:
                        jpeg_data = raw
                    
                    quality_info = get_jpeg_quality_info(jpeg_data)
                    if quality_info:
                        for info in quality_info:
                            if 'sof_type' in info:
                                print(f"    JPEG Type: {info['sof_type']}")
                                print(f"    JPEG Dimensions: {info['width']}x{info['height']}")
                                print(f"    Components: {info['components']}")
                            elif 'component_id' in info:
                                print(f"    Component {info['component_id']}: sampling={info['h_sampling']}x{info['v_sampling']}, QT={info['qt_table']}")
                            elif 'table_id' in info:
                                print(f"    QT Table {info['table_id']}: avg={info['avg_value']:.1f}, range=[{info['min_value']}-{info['max_value']}]")
                                print(f"      First 8 values: {info['first_8']}")
                    
            except Exception as e:
                print(f"    Error: {e}")
                import traceback
                traceback.print_exc()


analyze_page_image_detail("./data/input/input-original.pdf", "ORIGINAL")
analyze_page_image_detail("./data/input/input-compressed.pdf", "COMPRESSED")

# Now print a summary of key findings
print(f"\n\n{'='*70}")
print(f"  KEY FINDINGS SUMMARY")
print(f"{'='*70}")
print("""
PAGE 5 is the dominant factor:
  Original:   5712x4284 JPEG = 6,464,787 bytes (6.2 MB) — 83% of total file
  Compressed: 1210x908  JPEG =   140,557 bytes (137 KB) — downsampled ~4.7x

Other pages use FlateDecode+DCTDecode (Flate wrapping JPEG):
  This provides modest additional compression on the JPEG data.

The FlateDecode-only images (PNG-style) in page 15 also got smaller,
suggesting either re-encoding or better Flate compression settings.
""")
