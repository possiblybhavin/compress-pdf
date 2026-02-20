#!/usr/bin/env python3
"""Deep comparison of original vs compressed PDF image structures."""

import pikepdf
from pikepdf import Name
from collections import defaultdict
import io

def analyze_pdf_images(filepath, label):
    pdf = pikepdf.open(filepath)
    print(f"\n{'='*70}")
    print(f"  {label}: {filepath}")
    print(f"{'='*70}")
    print(f"  Pages: {len(pdf.pages)}")
    print(f"  PDF Version: {pdf.pdf_version}")
    
    # Count all objects
    total_objects = 0
    image_objects = []
    
    for objnum in pdf.objects:
        total_objects += 1
        try:
            obj = pdf.get_object(objnum)
            if isinstance(obj, pikepdf.Stream):
                obj_dict = dict(obj)
                if obj_dict.get(Name.Type) == Name.XObject and obj_dict.get(Name.Subtype) == Name.Image:
                    image_objects.append((objnum, obj))
        except Exception:
            pass
    
    print(f"  Total objects: {total_objects}")
    print(f"  Image objects found: {len(image_objects)}")
    
    image_details = []
    
    for i, (objnum, obj) in enumerate(image_objects):
        obj_dict = dict(obj)
        width = int(obj_dict.get(Name.Width, 0))
        height = int(obj_dict.get(Name.Height, 0))
        bpc = obj_dict.get(Name.BitsPerComponent, "N/A")
        colorspace = obj_dict.get(Name.ColorSpace, "N/A")
        
        # Get filter info
        filt = obj_dict.get(Name.Filter, "None")
        if isinstance(filt, pikepdf.Array):
            filt = [str(f) for f in filt]
        else:
            filt = str(filt)
        
        # Decode params
        decode_parms = obj_dict.get(Name.DecodeParms, "None")
        
        # Get raw stream length
        raw_length = len(obj.read_raw_bytes())
        
        # Try to get decoded length
        try:
            decoded_length = len(bytes(obj.read_bytes()))
        except Exception:
            decoded_length = None
        
        detail = {
            'index': i,
            'objnum': objnum,
            'width': width,
            'height': height,
            'bpc': bpc,
            'colorspace': str(colorspace),
            'filter': filt,
            'decode_parms': str(decode_parms),
            'raw_bytes': raw_length,
            'decoded_bytes': decoded_length,
        }
        image_details.append(detail)
        
        print(f"\n  --- Image {i} (obj {objnum}) ---")
        print(f"      Dimensions: {width} x {height}")
        print(f"      BitsPerComponent: {bpc}")
        print(f"      ColorSpace: {colorspace}")
        print(f"      Filter: {filt}")
        if str(decode_parms) != "None":
            print(f"      DecodeParms: {decode_parms}")
        print(f"      Raw stream size: {raw_length:,} bytes ({raw_length/1024:.1f} KB)")
        if decoded_length:
            print(f"      Decoded size: {decoded_length:,} bytes ({decoded_length/1024:.1f} KB)")
            if decoded_length > 0:
                ratio = raw_length / decoded_length
                print(f"      Compression ratio: {ratio:.3f} ({(1-ratio)*100:.1f}% reduction)")
        
        # For JPEG/DCT, try to identify JPEG quality by examining headers
        if 'DCTDecode' in str(filt):
            raw = obj.read_raw_bytes()
            # Check for JPEG markers
            if raw[:2] == b'\xff\xd8':
                print(f"      JPEG detected (valid SOI marker)")
                # Look for quantization tables (DQT marker FF DB)
                pos = 0
                dqt_count = 0
                while pos < min(len(raw), 2000):
                    if raw[pos:pos+2] == b'\xff\xdb':
                        dqt_count += 1
                        seg_len = int.from_bytes(raw[pos+2:pos+4], 'big')
                        # First byte after length: precision(4bits) | table_id(4bits)
                        qt_info = raw[pos+4]
                        precision = (qt_info >> 4) & 0x0F
                        table_id = qt_info & 0x0F
                        # Sum of quantization values gives quality hint
                        if precision == 0:  # 8-bit
                            qt_values = list(raw[pos+5:pos+5+64])
                            avg_qt = sum(qt_values) / len(qt_values) if qt_values else 0
                            print(f"      QT Table {table_id}: avg value={avg_qt:.1f} (higher=lower quality)")
                    pos += 1
        
        # Check for JPXDecode (JPEG2000)
        if 'JPXDecode' in str(filt):
            print(f"      JPEG2000 encoding detected")
    
    # Summary
    total_image_bytes = sum(d['raw_bytes'] for d in image_details)
    print(f"\n  --- SUMMARY ---")
    print(f"  Total image data (raw/compressed): {total_image_bytes:,} bytes ({total_image_bytes/1024:.1f} KB)")
    
    return image_details


def compare_images(orig_details, comp_details):
    print(f"\n{'='*70}")
    print(f"  COMPARISON")
    print(f"{'='*70}")
    
    # Match images by dimensions
    for od in orig_details:
        matches = [cd for cd in comp_details if cd['width'] == od['width'] and cd['height'] == od['height']]
        if matches:
            cd = matches[0]
            print(f"\n  Image {od['width']}x{od['height']}:")
            print(f"    Original:   filter={od['filter']}, raw={od['raw_bytes']:,} bytes, cs={od['colorspace']}, bpc={od['bpc']}")
            print(f"    Compressed: filter={cd['filter']}, raw={cd['raw_bytes']:,} bytes, cs={cd['colorspace']}, bpc={cd['bpc']}")
            if od['raw_bytes'] > 0:
                reduction = (1 - cd['raw_bytes'] / od['raw_bytes']) * 100
                print(f"    Reduction: {reduction:.1f}%")
                
                # Identify what changed
                changes = []
                if od['filter'] != cd['filter']:
                    changes.append(f"Filter: {od['filter']} → {cd['filter']}")
                if od['colorspace'] != cd['colorspace']:
                    changes.append(f"ColorSpace: {od['colorspace']} → {cd['colorspace']}")
                if od['bpc'] != cd['bpc']:
                    changes.append(f"BPC: {od['bpc']} → {cd['bpc']}")
                if changes:
                    print(f"    Changes: {'; '.join(changes)}")
        else:
            print(f"\n  Image {od['width']}x{od['height']}: NO MATCH in compressed version")
    
    # Check for images in compressed that aren't in original
    orig_dims = {(d['width'], d['height']) for d in orig_details}
    for cd in comp_details:
        if (cd['width'], cd['height']) not in orig_dims:
            print(f"\n  NEW in compressed: {cd['width']}x{cd['height']} filter={cd['filter']} raw={cd['raw_bytes']:,}")


# Also examine page-level resources for any differences
def analyze_page_resources(filepath, label):
    pdf = pikepdf.open(filepath)
    print(f"\n{'='*70}")
    print(f"  Page Resources: {label}")
    print(f"{'='*70}")
    for i, page in enumerate(pdf.pages):
        resources = page.get(Name.Resources, {})
        xobjects = resources.get(Name.XObject, {})
        print(f"\n  Page {i+1}: {len(dict(xobjects))} XObjects")
        for name, ref in dict(xobjects).items():
            try:
                obj = ref
                if isinstance(obj, pikepdf.Stream):
                    obj_dict = dict(obj)
                    subtype = obj_dict.get(Name.Subtype, "?")
                    if subtype == Name.Image:
                        w = obj_dict.get(Name.Width, "?")
                        h = obj_dict.get(Name.Height, "?")
                        filt = obj_dict.get(Name.Filter, "None")
                        raw_len = len(obj.read_raw_bytes())
                        print(f"    {name}: Image {w}x{h}, filter={filt}, {raw_len:,} bytes")
                    elif subtype == Name.Form:
                        raw_len = len(obj.read_raw_bytes())
                        print(f"    {name}: Form XObject, {raw_len:,} bytes")
            except Exception as e:
                print(f"    {name}: error reading - {e}")


orig_details = analyze_pdf_images("./data/input/input-original.pdf", "ORIGINAL (7.2 MB)")
comp_details = analyze_pdf_images("./data/input/input-compressed.pdf", "COMPRESSED (883 KB)")

compare_images(orig_details, comp_details)

analyze_page_resources("./data/input/input-original.pdf", "ORIGINAL")
analyze_page_resources("./data/input/input-compressed.pdf", "COMPRESSED")
