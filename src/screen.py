"""Захват экрана и подготовка изображения для модели.

Модели зрения обычно работают лучше с уменьшенным изображением. Мы масштабируем
скриншот по большей стороне до `max_side`, отдаём модели уменьшенную картинку и
её размеры, а координаты, которые вернёт модель, пересчитываем обратно в реальные
пиксели через коэффициент `scale`.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import mss
from PIL import Image


@dataclass
class Capture:
    """Результат захвата экрана."""

    image: Image.Image          # уменьшенное изображение (то, что видит модель)
    real_width: int             # реальная ширина экрана, px
    real_height: int            # реальная высота экрана, px
    scale: float                # real = model_coord / scale  (см. to_real)

    @property
    def model_width(self) -> int:
        return self.image.width

    @property
    def model_height(self) -> int:
        return self.image.height

    def to_real(self, x: float, y: float) -> tuple[int, int]:
        """Перевод координат из системы координат картинки модели в реальные пиксели."""
        rx = int(round(x / self.scale))
        ry = int(round(y / self.scale))
        rx = max(0, min(rx, self.real_width - 1))
        ry = max(0, min(ry, self.real_height - 1))
        return rx, ry

    def to_data_url(self, fmt: str = "PNG") -> str:
        buf = io.BytesIO()
        self.image.save(buf, format=fmt)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        mime = "image/png" if fmt.upper() == "PNG" else "image/jpeg"
        return f"data:{mime};base64,{b64}"

    def save(self, path: str) -> None:
        self.image.save(path)


class ScreenCapturer:
    def __init__(self, monitor_index: int = 1, max_side: int = 1280) -> None:
        self.monitor_index = monitor_index
        self.max_side = max_side

    def capture(self) -> Capture:
        with mss.mss() as sct:
            monitors = sct.monitors
            idx = self.monitor_index if self.monitor_index < len(monitors) else 1
            monitor = monitors[idx]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        real_w, real_h = img.size
        longest = max(real_w, real_h)
        scale = 1.0
        if self.max_side and longest > self.max_side:
            scale = self.max_side / longest
            new_size = (int(real_w * scale), int(real_h * scale))
            img = img.resize(new_size, Image.LANCZOS)

        return Capture(image=img, real_width=real_w, real_height=real_h, scale=scale)
