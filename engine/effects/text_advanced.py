# FILE: engine/effects/text_advanced.py

import os
import cv2
import numpy as np
import textwrap
from PIL import Image, ImageDraw, ImageFont

# Import Layer Base yang baru saja kita buat
from engine.core.layer_base import Layer

# --- FUNGSI PENGGANTI (Agar tidak perlu file utils lain) ---
def get_full_path(path):
    """Pengganti utils.paths.project_path"""
    if os.path.isabs(path):
        return path
    # Asumsi path relatif terhadap root project atau folder assets
    base_dir = os.getcwd() 
    return os.path.join(base_dir, path)

# -----------------------------------------------------------

class TextAdvancedEffect(Layer):
    def __init__(
        self,
        text,
        x,
        y,
        font_path,
        font_size=32,
        color=(255, 255, 255),
        opacity=1.0,
        z_index=0,
        enabled=True,
        align="left",
        stroke_width=0,
        stroke_color=(0, 0, 0),
    ):
        super().__init__(z_index=z_index, enabled=enabled, name="TextAdvanced")

        # Transform (diwarisi dari Layer, tapi kita set nilainya)
        self.transform.x = int(x)
        self.transform.y = int(y)
        if self.transform.scale == 0:
            self.transform.scale = 1.0

        # Text props
        self.text = text
        self.font_path = font_path
        self.full_font_path = get_full_path(font_path) # Pakai fungsi lokal
        self.base_font_size = int(font_size)
        self.color = tuple(color)
        self.base_opacity = float(opacity)
        self.align = align
        self.stroke_width = int(stroke_width)
        self.stroke_color = tuple(stroke_color)
        self.padding = 10

        # Cache
        self._cached_surface = None
        self._cached_rotated = None
        self._cache_params = None
        self._last_rotation = None
        self._w_orig = 0
        self._h_orig = 0
        
        # Motion easing vars
        self._target_x = x
        self._target_y = y
        self._easing_factor = 0.35

    # -------------------------------------------------

    def _render_text_bitmap(self, scale):
        # Cek font, jika tidak ada pakai default
        try:
            if not self.font_path or not os.path.exists(self.full_font_path):
                font = ImageFont.load_default()
            else:
                size = max(1, int(self.base_font_size * scale))
                font = ImageFont.truetype(self.full_font_path, size)
        except:
            font = ImageFont.load_default()

        # 1. Scaling Values
        # size sudah dihitung di try/except
        s_width = int(self.stroke_width * scale) if self.stroke_width > 0 else 0
        
        # Ambil properti tambahan
        box_width_raw = getattr(self, "box_width", 0) 
        box_width = int(box_width_raw * scale) if box_width_raw > 0 else 0
        
        line_spacing_pct = getattr(self, "line_spacing", 100) / 100.0
        align = self.align 
        
        # Shadow & BG Config
        shadow_on = getattr(self, "shadow_on", False)
        shadow_color = getattr(self, "shadow_color", (0,0,0))
        shadow_off_x = int(5 * scale)
        shadow_off_y = int(5 * scale)

        bg_on = getattr(self, "bg_on", False)
        bg_color = getattr(self, "bg_color", (0,0,0))
        bg_pad = int(10 * scale)

        # 2. LOGIKA UTAMA: AUTO VS PARAGRAPH
        final_text = self.text
        
        if box_width > 0:
            # --- PARAGRAPH MODE ---
            try:
                avg_char_w = font.getlength("x")
            except: 
                avg_char_w = 10 # Fallback
                
            if avg_char_w > 0:
                chars_per_line = int(box_width / avg_char_w)
                final_text = textwrap.fill(self.text, width=chars_per_line)

        # 3. MENGHITUNG UKURAN TEXT
        dummy_img = Image.new("RGBA", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)
        
        try:
            ascent, descent = font.getmetrics()
            font_height = ascent + descent
        except:
            font_height = 20
            
        pil_spacing = int(font_height * (line_spacing_pct - 1.0))
        
        bbox = dummy_draw.multiline_textbbox(
            (0, 0), 
            final_text, 
            font=font, 
            stroke_width=s_width, 
            spacing=pil_spacing, 
            align=align
        )
        
        text_real_w = bbox[2] - bbox[0]
        text_real_h = bbox[3] - bbox[1]

        # 4. MENENTUKAN UKURAN CANVAS FINAL
        if box_width > 0:
            final_w = max(text_real_w, box_width)
        else:
            final_w = text_real_w
            
        final_h = text_real_h

        # Tambahkan padding untuk shadow & bg
        total_w = final_w + (bg_pad * 2 if bg_on else 0) + abs(shadow_off_x if shadow_on else 0) + 20
        total_h = final_h + (bg_pad * 2 if bg_on else 0) + abs(shadow_off_y if shadow_on else 0) + 20
        
        img = Image.new("RGBA", (int(total_w), int(total_h)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        start_x = bg_pad if bg_on else 0
        start_y = bg_pad if bg_on else 0
        
        # Koreksi offset bawaan font
        start_y -= bbox[1]
        
        if box_width > 0:
            # Logic Alignment di dalam Fixed Box
            if align == "center":
                offset_x = (box_width - text_real_w) / 2
                start_x += offset_x
            elif align == "right":
                offset_x = (box_width - text_real_w)
                start_x += offset_x
            start_x -= bbox[0]
        else:
            start_x -= bbox[0]

        # 5. GAMBAR BACKGROUND
        if bg_on:
            bg_w = box_width if box_width > 0 else text_real_w
            bg_rect = [0, 0, bg_w + (bg_pad * 2), text_real_h + (bg_pad * 2)]
            draw.rectangle(bg_rect, fill=bg_color)

        # 6. GAMBAR SHADOW
        if shadow_on:
            draw.multiline_text(
                (start_x + shadow_off_x, start_y + shadow_off_y),
                final_text, font=font, fill=shadow_color, align=align,
                spacing=pil_spacing, stroke_width=s_width, stroke_fill=shadow_color
            )

        # 7. GAMBAR TEKS UTAMA
        draw.multiline_text(
            (start_x, start_y),
            final_text, font=font, fill=self.color, align=align,
            spacing=pil_spacing, stroke_width=s_width, stroke_fill=self.stroke_color
        )

        self._w_orig = int(total_w)
        self._h_orig = int(total_h)

        return np.array(img)

    # -------------------------------------------------

    def apply(self, frame, frame_index, fps, context: dict):
        # Adaptive color check
        adaptive = context.get("adaptive_text", {}).get("color")
        if adaptive is not None and adaptive != self.color:
            self.color = adaptive
            self._cached_surface = None

        scale = self.transform.scale
        rot = getattr(self.transform, "rotation", 0)
        box_width = getattr(self, "box_width", 0)

        cache_key = (
            self.text, self.font_path, self.color, self.stroke_width, self.stroke_color,
            round(scale, 3), box_width, getattr(self, "line_spacing", 100),
            getattr(self, "align", "left"), getattr(self, "bg_on", False),
            getattr(self, "shadow_on", False)
        )

        if self._cached_surface is None or self._cache_params != cache_key:
            surf = self._render_text_bitmap(scale)
            if surf is None: return frame
            self._cached_surface = surf
            self._cache_params = cache_key
            self._cached_rotated = None
            self._last_rotation = None

        if self._cached_rotated is None or self._last_rotation != rot:
            if rot == 0:
                self._cached_rotated = self._cached_surface
            else:
                pil = Image.fromarray(self._cached_surface)
                pil = pil.rotate(rot, expand=True, resample=Image.BICUBIC)
                self._cached_rotated = np.array(pil)
            self._last_rotation = rot

        overlay = self._cached_rotated
        if overlay is None: return frame

        # Blending logic (Standard)
        h, w = overlay.shape[:2]
        cx = self.transform.x + self._w_orig / 2
        cy = self.transform.y + self._h_orig / 2
        px = int(cx - w / 2)
        py = int(cy - h / 2)

        fh, fw = frame.shape[:2]
        x1, y1 = max(0, px), max(0, py)
        x2, y2 = min(fw, px + w), min(fh, py + h)

        if x1 >= x2 or y1 >= y2: return frame

        ox1, oy1 = x1 - px, y1 - py
        ox2, oy2 = ox1 + (x2 - x1), oy1 + (y2 - y1)

        roi = frame[y1:y2, x1:x2]
        ov = overlay[oy1:oy2, ox1:ox2]

        alpha = ov[:, :, 3:4].astype(np.float32) / 255.0
        alpha *= self.base_opacity
        alpha *= float(context.get("opacity", 1.0))
        alpha = np.clip(alpha, 0.0, 1.0)

        rgb = ov[:, :, :3].astype(np.float32)
        bg = roi.astype(np.float32)

        roi[:] = (alpha * rgb + (1.0 - alpha) * bg).astype(np.uint8)
        return frame

    # -------------------------------------------------

    def get_bbox(self):
        if self._cached_surface is None:
            self._render_text_bitmap(self.transform.scale)
        w = self._w_orig + self.padding * 2
        h = self._h_orig + self.padding * 2
        return (
            int(self.transform.x - self.padding),
            int(self.transform.y - self.padding),
            int(w), int(h),
        )

    def set_drag_target(self, target_x, target_y):
        self._target_x = float(target_x)
        self._target_y = float(target_y)
    
    def set_easing_factor(self, factor):
        self._easing_factor = max(0.01, min(0.99, float(factor)))
    
    def update_with_easing(self):
        # Simple Linear Interpolation (Internal)
        self.transform.x += (self._target_x - self.transform.x) * self._easing_factor
        self.transform.y += (self._target_y - self.transform.y) * self._easing_factor

    def draw_fast(self, frame):
        if self._cached_surface is None:
            self._render_text_bitmap(self.transform.scale)
        if self._cached_surface is None: return frame

        self.update_with_easing()

        overlay = self._cached_surface
        h, w = overlay.shape[:2]
        fh, fw = frame.shape[:2]
        x = int(self.transform.x)
        y = int(self.transform.y)
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(fw, x + w), min(fh, y + h)

        if x1 >= x2 or y1 >= y2: return frame

        ox1, oy1 = x1 - x, y1 - y
        ox2 = ox1 + (x2 - x1)
        oy2 = oy1 + (y2 - y1)

        ov = overlay[oy1:oy2, ox1:ox2]
        rgb = ov[..., :3]
        alpha = ov[..., 3]
        mask = alpha > 0
        roi = frame[y1:y2, x1:x2]
        roi[mask] = rgb[mask]
        return frame