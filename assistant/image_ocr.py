"""OCR for a supplied image only; no screen capture or background watcher."""
import re

def compact(text):
    return re.sub(r'\s+', '', text).replace('＠', '@')

def title_matches(text, target):
    text = compact(text)
    text = re.sub(r'[（(]\d+[）)]$', '', text)
    return text.strip('「」『』[]【】') == compact(target).strip('「」『』[]【】')

async def recognize(engine, image):
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.storage.streams import DataWriter
    # OCR only sees the cropped region. No image or text is written to disk.
    image = image.convert('RGBA')
    writer = DataWriter()
    bitmap = None
    try:
        writer.write_bytes(image.tobytes('raw', 'BGRA'))
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8,
            image.width, image.height, BitmapAlphaMode.IGNORE)
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            x = min(w.bounding_rect.x for w in words)
            y = min(w.bounding_rect.y for w in words)
            right = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
            bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            lines.append({'text': line.text, 'x': x / 2, 'y': y / 2,
                          'right': right / 2, 'bottom': bottom / 2})
        return lines
    finally:
        if bitmap:
            bitmap.close()
        writer.close()
