# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Logo di StillFind."""
from __future__ import annotations

import customtkinter as ctk
from PIL import Image, ImageTk

from stillfind.core.resources import brand_dir


class Brand:
    """Il marchio (la «S») e il logo con la scritta, in versione chiara e scura."""

    @staticmethod
    def _open(name: str) -> Image.Image:
        with Image.open(brand_dir() / name) as image:
            return image.convert("RGBA")

    @classmethod
    def wordmark(cls, width: int) -> ctk.CTkImage:
        light, dark = cls._open("wordmark-light.png"), cls._open("wordmark-dark.png")
        return ctk.CTkImage(light_image=light, dark_image=dark, size=(width, round(width * light.height / light.width)))

    @classmethod
    def symbol(cls, size: int) -> ctk.CTkImage:
        image = cls._open("mark.png")
        return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))

    @classmethod
    def set_window_icon(cls, window) -> None:
        try:
            window._brand_icon = ImageTk.PhotoImage(cls._open("mark.png").resize((128, 128)), master=window)
            window.iconphoto(True, window._brand_icon)
        except Exception:
            pass  # alcuni gestori di finestre non accettano icone: non è un problema
